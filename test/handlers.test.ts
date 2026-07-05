import {
	createExecutionContext,
	createScheduledController,
	env,
	SELF,
	waitOnExecutionContext,
} from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index";

// SELF経由で実Worker（ルーティング・認証・D1込み）を叩く統合テスト。
// ストレージはテスト間で共有されるため、beforeEachで明示的にクリアする

beforeEach(async () => {
	await env.DB.prepare("DELETE FROM locations").run();
});

const AUTH = { Authorization: "Bearer test-token" };
const JSON_POST = { ...AUTH, "Content-Type": "application/json" };
const BASE = "https://example.com";

function post(path: string, body: unknown, headers: Record<string, string> = JSON_POST) {
	return SELF.fetch(`${BASE}${path}`, {
		method: "POST",
		headers,
		body: JSON.stringify(body),
	});
}

async function importBatch(locations: Record<string, unknown>[]) {
	const resp = await post("/locations/batch", { locations });
	expect(resp.status).toBe(200);
	return resp.json() as Promise<Record<string, number | string>>;
}

function isoHoursAgo(hours: number): string {
	return new Date(Date.now() - hours * 3_600_000).toISOString();
}

describe("GET /health", () => {
	it("responds without auth and reports the record count", async () => {
		const resp = await SELF.fetch(`${BASE}/health`);
		expect(resp.status).toBe(200);
		expect(await resp.json()).toEqual({ status: "ok", service: "location-sync", records: 0 });
	});
});

describe("authentication", () => {
	it("rejects requests without a token", async () => {
		const resp = await SELF.fetch(`${BASE}/locations`);
		expect(resp.status).toBe(401);
	});

	it("rejects a wrong token", async () => {
		const resp = await SELF.fetch(`${BASE}/locations`, {
			headers: { Authorization: "Bearer wrong" },
		});
		expect(resp.status).toBe(401);
	});

	it("accepts Basic auth with the token as password (OwnTracks)", async () => {
		const resp = await SELF.fetch(`${BASE}/locations`, {
			headers: { Authorization: `Basic ${btoa("user:test-token")}` },
		});
		expect(resp.status).toBe(200);
	});

	it("returns 404 for unknown authenticated routes", async () => {
		const resp = await SELF.fetch(`${BASE}/nope`, { headers: AUTH });
		expect(resp.status).toBe(404);
	});
});

describe("POST /locations (OwnTracks)", () => {
	it("stores a location payload and responds with []", async () => {
		const resp = await post("/locations", {
			_type: "location",
			lat: 35.681236,
			lon: 139.767125,
			acc: 12,
			alt: 5,
			vel: 3,
			tst: 1751700000, // 2025-07-05T07:20:00Z
		});
		expect(resp.status).toBe(200);
		expect(await resp.json()).toEqual([]);

		const row = await env.DB.prepare("SELECT * FROM locations").first();
		expect(row).toMatchObject({
			timestamp: "2025-07-05T07:20:00.000Z", // 正準形で保存される
			lat: 35.681236,
			lon: 139.767125,
			accuracy: 12,
			altitude: 5,
			speed: 3,
			source: "owntracks",
		});
		expect(row?.h3_res7).toMatch(/^87/);
		expect(row?.h3_res9).toMatch(/^89/);
	});

	it("is idempotent for the same payload (INSERT OR IGNORE)", async () => {
		const payload = { _type: "location", lat: 35.68, lon: 139.77, tst: 1751700000 };
		await post("/locations", payload);
		await post("/locations", payload);

		const count = await env.DB.prepare("SELECT COUNT(*) as c FROM locations").first<{ c: number }>();
		expect(count?.c).toBe(1);
	});

	it("ignores non-location OwnTracks messages", async () => {
		const resp = await post("/locations", { _type: "status", battery: 80 });
		expect(resp.status).toBe(200);
		expect(await resp.json()).toEqual([]);

		const count = await env.DB.prepare("SELECT COUNT(*) as c FROM locations").first<{ c: number }>();
		expect(count?.c).toBe(0);
	});
});

describe("POST /locations (generic)", () => {
	it("stores a custom payload with 201", async () => {
		const resp = await post("/locations", {
			lat: 35.0,
			lon: 135.0,
			timestamp: "2026-01-01T09:00:00+09:00",
			source: "custom",
		});
		expect(resp.status).toBe(201);

		const row = await env.DB.prepare("SELECT timestamp, source FROM locations").first();
		expect(row).toEqual({ timestamp: "2026-01-01T00:00:00.000Z", source: "custom" });
	});

	it("rejects an invalid timestamp", async () => {
		const resp = await post("/locations", { lat: 35.0, lon: 135.0, timestamp: "garbage" });
		expect(resp.status).toBe(400);
	});

	it("rejects a payload without coordinates", async () => {
		const resp = await post("/locations", { foo: "bar" });
		expect(resp.status).toBe(400);
	});

	it("rejects malformed JSON", async () => {
		const resp = await SELF.fetch(`${BASE}/locations`, {
			method: "POST",
			headers: JSON_POST,
			body: "{not json",
		});
		expect(resp.status).toBe(400);
	});
});

describe("POST /locations/batch", () => {
	it("reports imported/duplicates/invalid counts", async () => {
		const base = { lat: 35.68, lon: 139.77, source: "path" };
		const result = await importBatch([
			{ ...base, timestamp: "2026-01-01T00:00:00Z" },
			{ ...base, timestamp: "2026-01-01T00:01:00Z" },
			{ ...base, timestamp: "2026-01-01T00:00:00Z" }, // 1行目と重複
			{ ...base, timestamp: "not-a-date" }, // 不正
			{ timestamp: "2026-01-01T00:02:00Z", lon: 139.77 }, // lat欠落 → 不正
		]);

		expect(result).toEqual({
			status: "ok",
			imported: 2,
			duplicates: 1,
			invalid: 2,
			errors: 0,
			total: 5,
		});
	});

	it("counts a full resend as duplicates (idempotent)", async () => {
		const locations = [
			{ timestamp: "2026-01-01T00:00:00Z", lat: 35.68, lon: 139.77, source: "path" },
			{ timestamp: "2026-01-01T00:01:00Z", lat: 35.69, lon: 139.78, source: "path" },
		];
		await importBatch(locations);
		const resent = await importBatch(locations);

		expect(resent).toMatchObject({ imported: 0, duplicates: 2 });
	});

	it("rejects a body without a locations array", async () => {
		const resp = await post("/locations/batch", { rows: [] });
		expect(resp.status).toBe(400);
	});
});

describe("GET /locations", () => {
	it("filters by the default 7-day window", async () => {
		await importBatch([
			{ timestamp: isoHoursAgo(1), lat: 35.68, lon: 139.77, source: "recent" },
			{ timestamp: isoHoursAgo(24 * 30), lat: 35.68, lon: 139.77, source: "old" },
		]);

		const resp = await SELF.fetch(`${BASE}/locations`, { headers: AUTH });
		const body = (await resp.json()) as { count: number; locations: { source: string }[] };
		expect(body.count).toBe(1);
		expect(body.locations[0].source).toBe("recent");
	});

	it("supports days/source/after/before/limit filters", async () => {
		await importBatch([
			{ timestamp: "2026-01-01T00:00:00Z", lat: 35.0, lon: 139.0, source: "a" },
			{ timestamp: "2026-01-02T00:00:00Z", lat: 35.1, lon: 139.1, source: "b" },
			{ timestamp: "2026-01-03T00:00:00Z", lat: 35.2, lon: 139.2, source: "a" },
		]);
		const get = async (qs: string) => {
			const resp = await SELF.fetch(`${BASE}/locations?${qs}`, { headers: AUTH });
			expect(resp.status).toBe(200);
			return resp.json() as Promise<{ count: number; locations: { timestamp: string }[] }>;
		};

		// after/before は正準形でない入力も受け付けて正規化される
		expect((await get("after=2026-01-01T12:00:00%2B09:00")).count).toBe(2);
		expect((await get("after=2026-01-01T00:00:00Z&before=2026-01-02T00:00:00Z")).count).toBe(2);
		expect((await get("after=2026-01-01T00:00:00Z&source=a")).count).toBe(2);

		const limited = await get("after=2026-01-01T00:00:00Z&limit=1");
		expect(limited.count).toBe(1);
		// timestamp降順
		expect(limited.locations[0].timestamp).toBe("2026-01-03T00:00:00.000Z");
	});

	it("rejects invalid after/before values", async () => {
		for (const qs of ["after=garbage", "before=garbage"]) {
			const resp = await SELF.fetch(`${BASE}/locations?${qs}`, { headers: AUTH });
			expect(resp.status).toBe(400);
		}
	});
});

describe("GET /locations (near search)", () => {
	// 東京駅周辺2点 + 大阪駅1点
	const points = [
		{ timestamp: "2026-01-01T00:00:00Z", lat: 35.681236, lon: 139.767125, source: "tokyo-sta" },
		{ timestamp: "2026-01-01T00:01:00Z", lat: 35.6867, lon: 139.7671, source: "tokyo-600m" },
		{ timestamp: "2026-01-01T00:02:00Z", lat: 34.702485, lon: 135.495951, source: "osaka" },
	];

	async function near(lat: number, lon: number, radius: number) {
		const resp = await SELF.fetch(
			`${BASE}/locations?near_lat=${lat}&near_lon=${lon}&radius=${radius}`,
			{ headers: AUTH },
		);
		expect(resp.status).toBe(200);
		return resp.json() as Promise<{
			count: number;
			locations: { source: string }[];
			meta: Record<string, unknown>;
		}>;
	}

	it("returns only points within the radius (haversine-filtered)", async () => {
		await importBatch(points);

		const body = await near(35.681236, 139.767125, 1);
		expect(body.locations.map((l) => l.source).sort()).toEqual(["tokyo-600m", "tokyo-sta"]);
		expect(body.meta).toMatchObject({ radius_km: 1, resolution_used: 9 });

		// 半径300mなら東京駅の1点のみ（600m離れた点はH3セルは近くてもhaversineで落ちる）
		const narrow = await near(35.681236, 139.767125, 0.3);
		expect(narrow.locations.map((l) => l.source)).toEqual(["tokyo-sta"]);
	});

	it("uses res7 for radii over ~1km", async () => {
		await importBatch(points);
		const body = await near(35.681236, 139.767125, 5);
		expect(body.meta).toMatchObject({ resolution_used: 7 });
		expect(body.locations.map((l) => l.source).sort()).toEqual(["tokyo-600m", "tokyo-sta"]);
	});

	it("finds old points too (no default days window in spatial mode)", async () => {
		await importBatch([
			{ timestamp: "2020-01-01T00:00:00Z", lat: 35.681236, lon: 139.767125, source: "ancient" },
		]);
		const body = await near(35.681236, 139.767125, 1);
		expect(body.count).toBe(1);
	});

	it("rejects non-numeric coordinates", async () => {
		const resp = await SELF.fetch(`${BASE}/locations?near_lat=abc&near_lon=139.77`, {
			headers: AUTH,
		});
		expect(resp.status).toBe(400);
	});
});

describe("GET /locations/latest", () => {
	it("returns 404 when empty", async () => {
		const resp = await SELF.fetch(`${BASE}/locations/latest`, { headers: AUTH });
		expect(resp.status).toBe(404);
	});

	it("returns the newest record", async () => {
		await importBatch([
			{ timestamp: "2026-01-01T00:00:00Z", lat: 35.0, lon: 139.0, source: "old" },
			{ timestamp: "2026-01-02T00:00:00Z", lat: 35.1, lon: 139.1, source: "new" },
		]);
		const resp = await SELF.fetch(`${BASE}/locations/latest`, { headers: AUTH });
		const body = (await resp.json()) as { location: { source: string } };
		expect(body.location.source).toBe("new");
	});
});

describe("POST /locations/backfill-h3", () => {
	it("fills missing H3 cells and reports completion", async () => {
		await env.DB.prepare(
			`INSERT INTO locations (timestamp, lat, lon, source) VALUES (?, ?, ?, ?)`,
		).bind("2026-01-01T00:00:00.000Z", 35.68, 139.77, "legacy").run();

		const resp = await post("/locations/backfill-h3", {});
		expect(await resp.json()).toEqual({ status: "complete", updated: 1, remaining: 0 });

		const row = await env.DB.prepare("SELECT h3_res7, h3_res9 FROM locations").first();
		expect(row?.h3_res7).toMatch(/^87/);
		expect(row?.h3_res9).toMatch(/^89/);
	});

	it("reports complete with zero updates when nothing is missing", async () => {
		const resp = await post("/locations/backfill-h3", {});
		expect(await resp.json()).toEqual({
			status: "complete",
			updated: 0,
			remaining: 0,
			total_records: 0,
		});
	});
});

describe("scheduled (OwnTracks freshness monitor)", () => {
	// worker.scheduled はテストと同一isolateで動くので、
	// グローバルfetchを差し替えればSlack通知を捕捉できる
	const fetchSpy = vi.fn(async () => new Response("ok"));

	beforeEach(() => {
		fetchSpy.mockClear();
		vi.stubGlobal("fetch", fetchSpy);
	});

	afterEach(() => {
		vi.unstubAllGlobals();
	});

	async function runScheduled() {
		const controller = createScheduledController({
			scheduledTime: new Date(),
			cron: "0 * * * *",
		});
		const ctx = createExecutionContext();
		await worker.scheduled(controller as unknown as ScheduledEvent, env, ctx);
		await waitOnExecutionContext(ctx);
	}

	async function insertOwntracksAt(timestamp: string) {
		await env.DB.prepare(
			"INSERT INTO locations (timestamp, lat, lon, source) VALUES (?, ?, ?, 'owntracks')",
		).bind(timestamp, 35.68, 139.77).run();
	}

	it("notifies Slack when data is stale (threshold just crossed)", async () => {
		await insertOwntracksAt(isoHoursAgo(24.5));

		await runScheduled();

		expect(fetchSpy).toHaveBeenCalledTimes(1);
		const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
		expect(url).toBe(env.SLACK_WEBHOOK_URL);
		const payload = JSON.parse(init.body as string) as { text: string };
		expect(payload.text).toContain("24時間");
	});

	it("stays silent when data is fresh", async () => {
		await insertOwntracksAt(isoHoursAgo(1));
		await runScheduled();
		expect(fetchSpy).not.toHaveBeenCalled();
	});

	it("stays silent when there is no owntracks data at all", async () => {
		await runScheduled();
		expect(fetchSpy).not.toHaveBeenCalled();
	});
});
