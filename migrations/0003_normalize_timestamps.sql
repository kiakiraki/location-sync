-- timestampを正準形（UTC ISO 8601: YYYY-MM-DDTHH:MM:SS.sssZ）に正規化する
--
-- 背景: Google Takeout由来の行は "+0900"（コロンなしJSTオフセット）、
-- OwnTracks由来の行はUTC（Z）で、形式が混在していた。従来はクエリのたびに
-- datetime(CASE ...) で変換していたが、この式はインデックスを使えず
-- 全件スキャンになる。全行を正準形に揃えることで、素の文字列比較で
-- 時系列順が成立し、idx_locations_timestamp が効くようになる。
--
-- 冪等: 変換結果が現在値と一致する行（既に正準形）はWHERE句で除外される
-- ため、再実行しても安全。変換に失敗する行（strftimeがNULLを返す行）は
-- 元の値のまま残す。
--
-- 注意: D1はGLOBパターン長の制限が厳しいため、正準形の判定に長いGLOBは
-- 使わず「変換結果 <> 現在値」で更新対象を絞る。

UPDATE locations
SET timestamp = strftime('%Y-%m-%dT%H:%M:%fZ',
    CASE WHEN timestamp GLOB '*[+-][0-9][0-9][0-9][0-9]'
         THEN substr(timestamp, 1, length(timestamp) - 2) || ':' || substr(timestamp, -2)
         ELSE timestamp END)
WHERE strftime('%Y-%m-%dT%H:%M:%fZ',
    CASE WHEN timestamp GLOB '*[+-][0-9][0-9][0-9][0-9]'
         THEN substr(timestamp, 1, length(timestamp) - 2) || ':' || substr(timestamp, -2)
         ELSE timestamp END) IS NOT NULL
  AND timestamp <> strftime('%Y-%m-%dT%H:%M:%fZ',
    CASE WHEN timestamp GLOB '*[+-][0-9][0-9][0-9][0-9]'
         THEN substr(timestamp, 1, length(timestamp) - 2) || ':' || substr(timestamp, -2)
         ELSE timestamp END);
