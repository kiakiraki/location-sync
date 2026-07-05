-- 重複レコードの排除とUNIQUE制約の追加
--
-- 背景: /locations/batch は無条件INSERTだったため、インポートを再実行すると
-- 全件重複していた。(timestamp, lat, lon, source) のUNIQUE制約を張り、
-- アプリ側は INSERT OR IGNORE にすることで再実行を安全（冪等）にする。
--
-- 注意: SQLiteのUNIQUE制約はNULL同士を別値として扱うため、source が NULL の
-- 行は制約の対象外になる（Worker側は常にsourceを設定するので実運用上は問題ない）。
--
-- 既存の重複は最小idの行を残して削除する。

DELETE FROM locations
WHERE id NOT IN (
    SELECT MIN(id) FROM locations GROUP BY timestamp, lat, lon, source
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_locations_unique
    ON locations(timestamp, lat, lon, source);
