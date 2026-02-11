/**
 * location-sync API
 * =================
 * Cloudflare Workers + D1 で位置情報を管理するAPI。
 *
 * Endpoints:
 *   GET  /health          - ヘルスチェック（認証不要）
 *   GET  /locations        - 位置情報の取得（Claude Skill用）
 *   GET  /locations/latest - 最新の位置情報
 *   POST /locations        - OwnTracksからの位置情報受信
 *   POST /locations/batch  - CSV一括インポート用
 */

export interface Env {
	DB: D1Database;
	API_TOKEN: string;
}

// --- Auth ---

function authenticate(request: Request, env: Env): boolean {
	const auth = request.headers.get("Authorization");
	if (!auth) return false;

	// Bearer Token
	if (auth.startsWith("Bearer ")) {
		const token = auth.replace("Bearer ", "").trim();
		return token === env.API_TOKEN;
	}

	// Basic Auth (OwnTracks HTTP mode)
	// パスワードフィールドにAPI_TOKENを設定する想定
	if (auth.startsWith("Basic ")) {
		try {
			const decoded = atob(auth.replace("Basic ", "").trim());
			const [, password] = decoded.split(":");
			return password === env.API_TOKEN;
		} catch {
			return false;
		}
	}

	return false;
}

function unauthorized(): Response {
	return new Response(JSON.stringify({ error: "Unauthorized" }), {
		status: 401,
		headers: { "Content-Type": "application/json" },
	});
}

function jsonResponse(data: unknown, status = 200): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: {
			"Content-Type": "application/json",
			"Access-Control-Allow-Origin": "*",
		},
	});
}

function errorResponse(message: string, status = 400): Response {
	return jsonResponse({ error: message }, status);
}

// --- CORS ---

function handleCors(request: Request): Response | null {
	if (request.method === "OPTIONS") {
		return new Response(null, {
			headers: {
				"Access-Control-Allow-Origin": "*",
				"Access-Control-Allow-Methods": "GET, POST, OPTIONS",
				"Access-Control-Allow-Headers": "Content-Type, Authorization",
				"Access-Control-Max-Age": "86400",
			},
		});
	}
	return null;
}

// --- Timestamp normalization ---
// Google Takeout: +0900 (コロンなし), OwnTracks: Z (UTC)
// SQLite の datetime() は +09:00 形式を要求するため、コロンなしオフセットを変換
const TS_NORM = `CASE WHEN timestamp GLOB '*[+-][0-9][0-9][0-9][0-9]'
  THEN substr(timestamp, 1, length(timestamp) - 2) || ':' || substr(timestamp, -2)
  ELSE timestamp END`;
const TS_UTC = `datetime(${TS_NORM})`;

// --- Handlers ---

async function handleHealth(env: Env): Promise<Response> {
	try {
		const result = await env.DB.prepare(
			"SELECT COUNT(*) as count FROM locations"
		).first<{ count: number }>();
		return jsonResponse({
			status: "ok",
			service: "location-sync",
			records: result?.count ?? 0,
		});
	} catch {
		return jsonResponse({ status: "ok", service: "location-sync", db: "not initialized" });
	}
}

async function handleGetLocations(request: Request, env: Env): Promise<Response> {
	const url = new URL(request.url);

	// クエリパラメータ
	const days = Math.min(Math.max(parseInt(url.searchParams.get("days") ?? "7"), 1), 365);
	const limit = Math.min(Math.max(parseInt(url.searchParams.get("limit") ?? "1000"), 1), 10000);
	const source = url.searchParams.get("source");  // path, visit, activity, raw:WIFI, owntracks
	const after = url.searchParams.get("after");     // ISO 8601
	const before = url.searchParams.get("before");   // ISO 8601

	let query = "SELECT * FROM locations WHERE 1=1";
	const params: unknown[] = [];

	if (after) {
		query += ` AND ${TS_UTC} >= ?`;
		params.push(after);
	} else {
		// デフォルト: N日前から
		query += ` AND ${TS_UTC} >= datetime('now', ?)`;
		params.push(`-${days} days`);
	}

	if (before) {
		query += ` AND ${TS_UTC} <= ?`;
		params.push(before);
	}

	if (source) {
		query += " AND source = ?";
		params.push(source);
	}

	query += ` ORDER BY ${TS_UTC} DESC LIMIT ?`;
	params.push(limit);

	const results = await env.DB.prepare(query).bind(...params).all();

	return jsonResponse({
		count: results.results.length,
		locations: results.results,
	});
}

async function handleGetLatest(env: Env): Promise<Response> {
	const result = await env.DB.prepare(
		`SELECT * FROM locations ORDER BY ${TS_UTC} DESC LIMIT 1`
	).first();

	if (!result) {
		return errorResponse("No location data found", 404);
	}

	return jsonResponse({ location: result });
}

async function handlePostLocation(request: Request, env: Env): Promise<Response> {
	const body = await request.json() as Record<string, unknown>;

	// OwnTracks HTTP mode payload
	// https://owntracks.org/booklet/tech/http/
	if (body._type === "location") {
		const timestamp = body.tst
			? new Date((body.tst as number) * 1000).toISOString()
			: new Date().toISOString();

		await env.DB.prepare(
			`INSERT INTO locations (timestamp, lat, lon, accuracy, altitude, speed, source)
			 VALUES (?, ?, ?, ?, ?, ?, ?)`
		).bind(
			timestamp,
			body.lat,
			body.lon,
			body.acc ?? null,
			body.alt ?? null,
			body.vel ?? null,
			"owntracks"
		).run();

		// OwnTracksはレスポンスで空配列を期待する
		return jsonResponse([]);
	}

	// OwnTracks waypoint（ジオフェンス定義）
	if (body._type === "waypoint" && body.lat !== undefined && body.lon !== undefined) {
		const timestamp = body.tst
			? new Date((body.tst as number) * 1000).toISOString()
			: new Date().toISOString();

		await env.DB.prepare(
			`INSERT INTO locations (timestamp, lat, lon, accuracy, source, semantic_type)
			 VALUES (?, ?, ?, ?, ?, ?)`
		).bind(
			timestamp,
			body.lat,
			body.lon,
			body.rad ?? null,
			"owntracks:waypoint",
			body.desc ?? null,
		).run();

		return jsonResponse([]);
	}

	// OwnTracks transition（ジオフェンス出入りイベント）
	if (body._type === "transition" && body.lat !== undefined && body.lon !== undefined) {
		const timestamp = body.tst
			? new Date((body.tst as number) * 1000).toISOString()
			: new Date().toISOString();

		await env.DB.prepare(
			`INSERT INTO locations (timestamp, lat, lon, accuracy, source, semantic_type, activity_type)
			 VALUES (?, ?, ?, ?, ?, ?, ?)`
		).bind(
			timestamp,
			body.lat,
			body.lon,
			body.acc ?? null,
			"owntracks:transition",
			body.desc ?? null,
			body.event ?? null,
		).run();

		return jsonResponse([]);
	}

	// OwnTracksのその他メッセージ（status, cmd, card等）は無視
	if (body._type) {
		return jsonResponse([]);
	}

	// 汎用 POST（カスタムアプリ等）
	if (body.lat !== undefined && body.lon !== undefined) {
		const timestamp = (body.timestamp as string) ?? new Date().toISOString();

		await env.DB.prepare(
			`INSERT INTO locations (timestamp, lat, lon, accuracy, source, place_id, semantic_type, activity_type, altitude, speed)
			 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
		).bind(
			timestamp,
			body.lat,
			body.lon,
			body.accuracy ?? null,
			body.source ?? "manual",
			body.place_id ?? null,
			body.semantic_type ?? null,
			body.activity_type ?? null,
			body.altitude ?? null,
			body.speed ?? null,
		).run();

		return jsonResponse({ status: "ok" }, 201);
	}

	return errorResponse("Invalid payload: lat and lon required");
}

async function handleBatchImport(request: Request, env: Env): Promise<Response> {
	const body = await request.json() as { locations: Record<string, unknown>[] };

	if (!body.locations || !Array.isArray(body.locations)) {
		return errorResponse("Expected { locations: [...] }");
	}

	const batchSize = 100;
	let imported = 0;
	let errors = 0;

	for (let i = 0; i < body.locations.length; i += batchSize) {
		const chunk = body.locations.slice(i, i + batchSize);
		const stmts = chunk.map((loc) =>
			env.DB.prepare(
				`INSERT INTO locations (timestamp, lat, lon, accuracy, source, place_id, semantic_type, activity_type, altitude, speed)
				 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
			).bind(
				loc.timestamp ?? null,
				loc.lat ?? null,
				loc.lon ?? null,
				loc.accuracy ?? null,
				loc.source ?? null,
				loc.place_id ?? null,
				loc.semantic_type ?? null,
				loc.activity_type ?? null,
				loc.altitude ?? null,
				loc.speed ?? null,
			)
		);

		try {
			await env.DB.batch(stmts);
			imported += chunk.length;
		} catch (e) {
			errors += chunk.length;
			console.error(`Batch error at offset ${i}:`, e);
		}
	}

	return jsonResponse({
		status: "ok",
		imported,
		errors,
		total: body.locations.length,
	});
}

// --- Router ---

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		// CORS preflight
		const corsResponse = handleCors(request);
		if (corsResponse) return corsResponse;

		const url = new URL(request.url);
		const path = url.pathname;
		const method = request.method;

		// Health check (no auth)
		if (path === "/health" && method === "GET") {
			return handleHealth(env);
		}

		// デバッグ: リクエストをそのまま返す（認証前、一時的）
		if (path === "/debug" && method === "POST") {
			const body = await request.text();
			const headers: Record<string, string> = {};
			request.headers.forEach((v, k) => { headers[k] = v; });
			console.log("DEBUG HEADERS:", JSON.stringify(headers));
			console.log("DEBUG BODY:", body.substring(0, 1000));
			return jsonResponse({ headers, body: body.substring(0, 2000) });
		}

		// All other endpoints require auth
		if (!authenticate(request, env)) {
			return unauthorized();
		}

		// Route
		if (path === "/locations" && method === "GET") {
			return handleGetLocations(request, env);
		}
		if (path === "/locations/latest" && method === "GET") {
			return handleGetLatest(env);
		}
		if (path === "/locations" && method === "POST") {
			return handlePostLocation(request, env);
		}
		if (path === "/locations/batch" && method === "POST") {
			return handleBatchImport(request, env);
		}

		return errorResponse("Not found", 404);
	},
};