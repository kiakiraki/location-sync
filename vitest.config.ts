import { fileURLToPath } from "node:url";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig({
	plugins: [
		cloudflareTest(async () => {
			// migrations/*.sql を番号順に読み込み、test/apply-migrations.ts で
			// テスト用D1に全適用する（本番と同じスキーマでテストするため）
			const migrations = await readD1Migrations(
				fileURLToPath(new URL("./migrations", import.meta.url)),
			);

			return {
				wrangler: { configPath: "./wrangler.toml" },
				miniflare: {
					bindings: {
						TEST_MIGRATIONS: migrations,
						// 本番ではsecret（wrangler secret put）。テストでは固定値を注入する
						API_TOKEN: "test-token",
						SLACK_WEBHOOK_URL: "https://hooks.slack.test/services/TEST",
					},
				},
			};
		}),
	],
	test: {
		setupFiles: ["./test/apply-migrations.ts"],
	},
});
