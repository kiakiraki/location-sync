-- H3空間インデックス用カラム追加
-- res7（~5km²）: エリアレベル検索用
-- res9（~0.1km²）: 施設レベル検索用
-- NULL許容: 既存データはbackfillまでNULL

ALTER TABLE locations ADD COLUMN h3_res7 TEXT;
ALTER TABLE locations ADD COLUMN h3_res9 TEXT;

CREATE INDEX idx_locations_h3_res7 ON locations(h3_res7);
CREATE INDEX idx_locations_h3_res9 ON locations(h3_res9);
CREATE INDEX idx_locations_h3_res7_ts ON locations(h3_res7, timestamp DESC);
CREATE INDEX idx_locations_h3_res9_ts ON locations(h3_res9, timestamp DESC);
