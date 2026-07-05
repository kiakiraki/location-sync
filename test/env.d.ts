import type { D1Migration } from "cloudflare:test";
import type { Env as WorkerEnv } from "../src/index";

declare global {
	// cloudflare:test の env は Cloudflare.Env 型。Worker本体のEnvと
	// テスト専用バインディング（マイグレーションSQL）を合成して与える
	namespace Cloudflare {
		interface Env extends WorkerEnv {
			TEST_MIGRATIONS: D1Migration[];
		}
	}
}
