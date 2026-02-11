-- Migration: 0001_create_locations.sql
-- Location history storage for location-sync

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    accuracy REAL,
    source TEXT,
    place_id TEXT,
    semantic_type TEXT,
    activity_type TEXT,
    altitude REAL,
    speed REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 時系列クエリ用（最頻出）
CREATE INDEX idx_locations_timestamp ON locations(timestamp DESC);

-- 空間クエリ用（特定エリアの履歴検索）
CREATE INDEX idx_locations_coords ON locations(lat, lon);

-- ソース別フィルタ用
CREATE INDEX idx_locations_source ON locations(source);

-- 複合インデックス: 期間＋ソースの絞り込み
CREATE INDEX idx_locations_ts_source ON locations(timestamp DESC, source);
