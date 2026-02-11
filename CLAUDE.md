# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

位置情報の収集・蓄積・参照API。Cloudflare Workers + D1で構成。
Google Timeline（Takeoutエクスポート）の履歴データとOwnTracksアプリからのリアルタイムデータを統合管理する。

## Architecture

```
[OwnTracks App]  →  POST /locations  →  [Cloudflare Workers (src/index.ts)]  →  [D1 SQLite]
[Google Export]   →  Timeline.json → parse_location_history.py → CSV → import_to_api.py → API
[Claude Skill]   ←  GET /locations   ← Workers ← D1
```

- **API（Workers）**: `src/index.ts` — 単一ファイルにルーティング・認証・全ハンドラを実装
- **DB**: Cloudflare D1（SQLite互換）。スキーマは `migrations/` 配下のSQL
- **データパイプライン**: `parse_location_history.py`（JSON→CSV変換）→ `scripts/import_to_api.py`（CSV→API投入）
- **認証**: Bearer Token（`wrangler.toml` の `API_TOKEN`）。OwnTracksのBasic Authにも対応

## Commands

### デプロイ
```bash
npx wrangler deploy
```

### D1マイグレーション
```bash
# ローカル
npx wrangler d1 execute location-sync --local --file=migrations/0001_create_locations.sql

# 本番
npx wrangler d1 execute location-sync --remote --file=migrations/0001_create_locations.sql
```

### ローカル開発
```bash
npx wrangler dev
```

### Google Takeoutデータの変換・インポート
```bash
# JSON構造を確認
python parse_location_history.py peek Timeline.json

# 統計情報
python parse_location_history.py stats Timeline.json

# CSV変換
python parse_location_history.py to_csv Timeline.json -o locations.csv

# APIへ一括インポート（ドライラン）
python scripts/import_to_api.py locations.csv --dry-run -o chunks/

# APIへ一括インポート（本番）
python scripts/import_to_api.py locations.csv --token <TOKEN> --chunk-size 500
```

## Key Design Decisions

- **単一ファイルWorker**: `src/index.ts` にルーター・認証・全エンドポイントをフラットに実装。フレームワーク不使用
- **OwnTracks互換**: POST /locations は OwnTracks HTTP modeのペイロード（`_type: "location"`）を受け付け、レスポンスは空配列 `[]` を返す
- **バッチインポート**: D1のバッチ制限を考慮し、API側で100件ずつ `DB.batch()` で処理。クライアント側は500件チャンクで送信
- **タイムスタンプ混在**: 歴史データはJST（+09:00）、OwnTracksデータはUTC。表示時はJSTに変換が必要
- **Google Timeline対応**: `parse_location_history.py` は旧形式（`latitudeE7`）・新形式（`semanticSegments`）・`rawSignals` など複数のエクスポート形式に対応

## D1 Schema

`locations` テーブル: `id`, `timestamp(TEXT)`, `lat(REAL)`, `lon(REAL)`, `accuracy`, `source`, `place_id`, `semantic_type`, `activity_type`, `altitude`, `speed`, `created_at`

主要インデックス: `timestamp DESC`, `(lat, lon)`, `source`, `(timestamp DESC, source)`

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | No | ヘルスチェック + レコード数 |
| GET | `/locations` | Yes | 位置情報一覧（days/limit/source/after/before） |
| GET | `/locations/latest` | Yes | 最新1件 |
| POST | `/locations` | Yes | OwnTracks互換の位置登録 |
| POST | `/locations/batch` | Yes | 一括インポート |
