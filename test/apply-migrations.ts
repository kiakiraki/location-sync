import { applyD1Migrations, env } from "cloudflare:test";

// 各テストファイルの実行前にマイグレーションを全適用する。
// isolatedStorage（デフォルト有効）により、各テストはこの状態に巻き戻る
await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
