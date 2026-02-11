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

# package.json 生成（Wranglerが必要とする）
npm init -y

# D1データベース作成
npx wrangler d1 create location-sync
# → 出力される database_id を wrangler.toml に貼り付け
```

### 2. wrangler.toml 設定

```bash
# API Token を生成
openssl rand -base64 32
# → 出力を wrangler.toml の API_TOKEN に設定
# → 同じ値を SKILL.md にも記載
```

`wrangler.toml` の `database_id` と `API_TOKEN` を埋める。

### 3. D1 マイグレーション

```bash
# ローカルDB（テスト用）
npx wrangler d1 execute location-sync --local --file=migrations/0001_create_locations.sql

# 本番DB
npx wrangler d1 execute location-sync --remote --file=migrations/0001_create_locations.sql
```

### 4. デプロイ

```bash
npx wrangler deploy
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
| POST | `/locations/batch` | ✓ | 一括インポート |

### GET /locations クエリパラメータ

| Param | Default | Description |
|-------|---------|-------------|
| `days` | 7 | 取得日数（1〜365） |
| `limit` | 1000 | 最大件数（1〜10000） |
| `source` | - | ソースフィルタ（path/visit/activity/owntracks等） |
| `after` | - | この日時以降（ISO 8601） |
| `before` | - | この日時以前（ISO 8601） |

## Files

```
location-sync/
├── wrangler.toml              # Cloudflare Workers 設定
├── src/
│   └── index.ts               # Workers メインコード
├── migrations/
│   └── 0001_create_locations.sql  # D1 スキーマ
├── scripts/
│   └── import_to_api.py       # CSV一括インポーター
└── README.md
```
