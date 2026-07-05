# location-sync

位置情報の収集・蓄積・参照API。Cloudflare Workers + D1。

## Architecture

```
[OwnTracks App]  →  POST /locations  →  [Cloudflare Workers]  →  [D1]
[Google Export]   →  CSV → batch import  →       ↑                  ↓
[Claude Skill]   ←  GET /locations   ←←←←←←←←←←←←←←←←←←←←←←←←←←←←
```

## Setup

### 1. プロジェクト初期化

```bash
cd location-sync
npm install

# D1データベース作成
npx wrangler d1 create location-sync
# → 出力される database_id を wrangler.toml に貼り付け
```

### 2. API Token 設定

```bash
# API Token を生成
openssl rand -base64 32

# Workers の Secret として登録（wrangler.toml には書かない）
npx wrangler secret put API_TOKEN
```

### 3. D1 マイグレーション

`migrations/` 配下のSQLをファイル番号順にすべて適用する。

```bash
# ローカルDB（テスト用）
for f in migrations/*.sql; do
  npx wrangler d1 execute location-sync --local --file="$f"
done

# 本番DB
for f in migrations/*.sql; do
  npx wrangler d1 execute location-sync --remote --file="$f"
done
```

### 4. デプロイ

```bash
npm run deploy
```

mainブランチへのpushでもGitHub Actions経由で自動デプロイされる
（typecheck + testが通った場合のみ）。

### 開発

```bash
npm run dev        # ローカル開発サーバー
npm run typecheck  # 型チェック
npm test           # ユニットテスト
```

### 5. 動作確認

```bash
# ヘルスチェック
curl https://<YOUR_WORKER>.workers.dev/health

# 位置情報取得（要認証）
curl -s "https://<YOUR_WORKER>.workers.dev/locations?days=7" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 6. 既存データインポート

```bash
# まずCSVを用意（parse_location_history.py で生成済み）

# ドライラン（JSONファイルに出力して中身を確認）
python scripts/import_to_api.py locations.csv --dry-run -o chunks/

# 本番インポート
python scripts/import_to_api.py locations.csv \
  --token YOUR_TOKEN \
  --chunk-size 500
```

### 7. OwnTracks 設定

Android OwnTracks アプリ:
1. Mode: **HTTP**
2. URL: `https://<YOUR_WORKER>.workers.dev/locations`
3. Headers → Authorization: `Bearer YOUR_TOKEN`
4. Monitoring: **Significant changes** (バッテリー節約) or **Move** (高頻度)

## API Reference

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | ✗ | ヘルスチェック + レコード数 |
| GET | `/locations` | ✓ | 位置情報一覧（クエリパラメータでフィルタ） |
| GET | `/locations/latest` | ✓ | 最新の位置情報1件 |
| POST | `/locations` | ✓ | 位置情報登録（OwnTracks互換） |
| POST | `/locations/batch` | ✓ | 一括インポート（冪等、重複はスキップ） |
| POST | `/locations/backfill-h3` | ✓ | 既存データへのH3インデックス付与 |

### GET /locations クエリパラメータ

| Param | Default | Description |
|-------|---------|-------------|
| `days` | 7 | 取得日数（1〜365） |
| `limit` | 1000 | 最大件数（1〜10000） |
| `source` | - | ソースフィルタ（path/visit/activity/owntracks等） |
| `after` | - | この日時以降（ISO 8601） |
| `before` | - | この日時以前（ISO 8601） |
| `near_lat` / `near_lon` | - | 空間検索の中心座標（両方指定で有効） |
| `radius` | 1 | 検索半径km（0.1〜約7）。H3セルで絞り込み後、haversineで正確な半径にフィルタ |

タイムスタンプはすべてUTC ISO 8601（`YYYY-MM-DDTHH:MM:SS.sssZ`）の正準形で
保存・返却される。

## Files

```
location-sync/
├── wrangler.toml              # Cloudflare Workers 設定
├── src/
│   └── index.ts               # Workers メインコード
├── test/
│   └── helpers.test.ts        # ユニットテスト（vitest）
├── migrations/
│   ├── 0001_create_locations.sql      # D1 スキーマ
│   ├── 0002_add_h3_columns.sql        # H3空間インデックス
│   ├── 0003_normalize_timestamps.sql  # タイムスタンプ正規化
│   └── 0004_unique_locations.sql      # 重複排除 + UNIQUE制約
├── scripts/
│   ├── import_to_api.py       # CSV一括インポーター
│   └── backfill_h3.py         # H3 backfill
└── README.md
```
