import { describe, expect, it } from "vitest";
import {
	authenticate,
	buildNearbyParams,
	computeH3,
	haversineKm,
	normalizeTimestamp,
	type Env,
} from "../src/index";

// src/index.ts の MAX_RADIUS_KM と同値（Workersランタイムの制約で
// エントリモジュールから定数をexportできないため、値を直接持つ）
const MAX_RADIUS_KM = 5 * 1.406;

const env = { API_TOKEN: "secret-token" } as Env;

function req(headers: Record<string, string>): Request {
	return new Request("https://example.com/locations", { headers });
}

describe("normalizeTimestamp", () => {
	it("converts Google Takeout +0900 (no colon) to UTC", () => {
		expect(normalizeTimestamp("2023-05-01T12:34:56.000+0900")).toBe("2023-05-01T03:34:56.000Z");
	});

	it("converts +09:00 offsets to UTC", () => {
		expect(normalizeTimestamp("2023-05-01T12:34:56+09:00")).toBe("2023-05-01T03:34:56.000Z");
	});

	it("normalizes second-precision UTC to canonical form", () => {
		expect(normalizeTimestamp("2024-11-30T23:59:59Z")).toBe("2024-11-30T23:59:59.000Z");
	});

	it("keeps canonical timestamps unchanged", () => {
		expect(normalizeTimestamp("2025-01-01T00:00:00.123Z")).toBe("2025-01-01T00:00:00.123Z");
	});

	it("rejects invalid input", () => {
		expect(normalizeTimestamp("not-a-date")).toBeNull();
		expect(normalizeTimestamp("")).toBeNull();
		expect(normalizeTimestamp(1234567890)).toBeNull();
		expect(normalizeTimestamp(null)).toBeNull();
		expect(normalizeTimestamp(undefined)).toBeNull();
	});
});

describe("computeH3", () => {
	it("computes res7 and res9 cells for valid coordinates", () => {
		const h3 = computeH3(35.681236, 139.767125);
		expect(h3.h3_res7).toMatch(/^87/);
		expect(h3.h3_res9).toMatch(/^89/);
	});

	it("returns nulls for missing or invalid coordinates", () => {
		expect(computeH3(null, 139.7)).toEqual({ h3_res7: null, h3_res9: null });
		expect(computeH3(35.6, undefined)).toEqual({ h3_res7: null, h3_res9: null });
		expect(computeH3("abc", 139.7)).toEqual({ h3_res7: null, h3_res9: null });
	});
});

describe("buildNearbyParams", () => {
	it("uses res9 for small radii and res7 for large", () => {
		expect(buildNearbyParams(35.68, 139.77, 0.5).column).toBe("h3_res9");
		expect(buildNearbyParams(35.68, 139.77, 5).column).toBe("h3_res7");
	});

	// D1のバインドパラメータ上限は100個/クエリ。セル以外の条件パラメータの
	// 余裕を残して91セル（k=5）以下でなければならない
	it("never exceeds 91 cells for any allowed radius", () => {
		for (let r = 0.1; r <= MAX_RADIUS_KM; r += 0.1) {
			const { cells } = buildNearbyParams(35.68, 139.77, r);
			expect(cells.length).toBeLessThanOrEqual(91);
		}
	});

	it("covers the requested radius with enough rings", () => {
		// 1km at res9: k = ceil(1/0.201) = 5 → 91 cells
		expect(buildNearbyParams(35.68, 139.77, 1).cells.length).toBe(91);
		// 3km at res7: k = ceil(3/1.406) = 3 → 37 cells
		expect(buildNearbyParams(35.68, 139.77, 3).cells.length).toBe(37);
	});
});

describe("haversineKm", () => {
	it("returns 0 for identical points", () => {
		expect(haversineKm(35.68, 139.77, 35.68, 139.77)).toBe(0);
	});

	it("computes known distances (Tokyo Sta. - Osaka Sta. ~= 403km)", () => {
		const d = haversineKm(35.681236, 139.767125, 34.702485, 135.495951);
		expect(d).toBeGreaterThan(395);
		expect(d).toBeLessThan(410);
	});
});

describe("authenticate", () => {
	it("accepts a valid Bearer token", () => {
		expect(authenticate(req({ Authorization: "Bearer secret-token" }), env)).toBe(true);
	});

	it("rejects a wrong Bearer token", () => {
		expect(authenticate(req({ Authorization: "Bearer wrong" }), env)).toBe(false);
	});

	it("accepts Basic auth with the token as password (OwnTracks)", () => {
		const cred = btoa("user:secret-token");
		expect(authenticate(req({ Authorization: `Basic ${cred}` }), env)).toBe(true);
	});

	it("rejects Basic auth with a wrong password", () => {
		const cred = btoa("user:wrong");
		expect(authenticate(req({ Authorization: `Basic ${cred}` }), env)).toBe(false);
	});

	it("rejects requests without an Authorization header", () => {
		expect(authenticate(req({}), env)).toBe(false);
	});
});
