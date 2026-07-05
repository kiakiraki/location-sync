-- timestampを正準形（UTC ISO 8601: YYYY-MM-DDTHH:MM:SS.sssZ）に正規化する
--
-- 背景: Google Takeout由来の行は "+0900"（コロンなしJSTオフセット）、
-- OwnTracks由来の行はUTC（Z）で、形式が混在していた。従来はクエリのたびに
-- datetime(CASE ...) で変換していたが、この式はインデックスを使えず
-- 全件スキャンになる。全行を正準形に揃えることで、素の文字列比較で
-- 時系列順が成立し、idx_locations_timestamp が効くようになる。
--
-- 冪等: 既に正準形の行はWHERE句で除外されるため、再実行しても安全。
-- 変換に失敗する行（strftimeがNULLを返す行）は元の値のまま残す。

UPDATE locations
SET timestamp = strftime('%Y-%m-%dT%H:%M:%fZ',
    CASE WHEN timestamp GLOB '*[+-][0-9][0-9][0-9][0-9]'
         THEN substr(timestamp, 1, length(timestamp) - 2) || ':' || substr(timestamp, -2)
         ELSE timestamp END)
WHERE timestamp NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'
  AND strftime('%Y-%m-%dT%H:%M:%fZ',
    CASE WHEN timestamp GLOB '*[+-][0-9][0-9][0-9][0-9]'
         THEN substr(timestamp, 1, length(timestamp) - 2) || ':' || substr(timestamp, -2)
         ELSE timestamp END) IS NOT NULL;
