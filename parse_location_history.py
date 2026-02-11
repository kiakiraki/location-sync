#!/usr/bin/env python3
"""
Google ロケーション履歴 JSON パーサー
======================================
巨大なJSONファイルを段階的に処理するためのツール。

使い方:
  # Step 1: 構造を確認（先頭だけ覗く）
  python parse_location_history.py peek <file.json>

  # Step 2: 統計情報（件数・期間・サイズ感）
  python parse_location_history.py stats <file.json>

  # Step 3: CSV に変換（D1投入用）
  python parse_location_history.py to_csv <file.json> -o locations.csv

  # Step 4: 期間を指定して抽出
  python parse_location_history.py to_csv <file.json> -o locations.csv \
      --after 2024-01-01 --before 2025-01-01

  # Step 5: 分割（Dawarich等の5MB制限対策）
  python parse_location_history.py split <file.json> -o chunks/ --max-mb 4
"""

import json
import sys
import os
import argparse
import csv
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path


# ---- ストリーミングパーサー (ijsonなしで動く簡易版) ----

def load_json_streaming(filepath: str):
    """
    ファイルサイズに応じて読み込み方法を切り替え。
    - 500MB未満: 一括読み込み (速い)
    - 500MB以上: チャンク読み込み案内
    """
    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    print(f"📁 ファイルサイズ: {size_mb:.1f} MB")

    if size_mb > 2000:
        print("⚠️  2GB超のファイルです。メモリ不足になる可能性があります。")
        print("   --after/--before で期間を絞るか、split コマンドの利用を検討してください。")

    print("📖 JSONを読み込み中...")
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    print("✅ 読み込み完了")
    return data


def find_location_entries(data) -> list:
    """
    Google Timeline JSONの様々なフォーマットに対応してロケーションエントリを抽出。
    2024年以降の新形式と旧形式の両方をサポート。
    semanticSegmentsはvisit/activity/timelinePathを含む複合エントリ。
    """
    entries = []

    if isinstance(data, dict):
        # 旧形式: {"locations": [...]}
        if "locations" in data:
            return data["locations"]

        # 新形式: {"semanticSegments": [...]}
        # semanticSegmentsを展開: timelinePathの各pointも個別エントリに
        if "semanticSegments" in data:
            for seg in data["semanticSegments"]:
                if "visit" in seg:
                    entries.append(seg)
                elif "activity" in seg:
                    entries.append(seg)
                elif "timelinePath" in seg:
                    # timelinePathの各ポイントを個別エントリとして展開
                    for pt in seg["timelinePath"]:
                        entries.append({"_type": "pathPoint", **pt})
                else:
                    entries.append(seg)

        # rawSignals からも位置情報を取れる場合がある
        if "rawSignals" in data:
            for sig in data["rawSignals"]:
                if "position" in sig:
                    pos = sig["position"]
                    entries.append({"_type": "rawPosition", **pos})

        if entries:
            return entries

        # 新形式: {"timelineObjects": [...]}
        if "timelineObjects" in data:
            return data["timelineObjects"]

        # Records.json 形式
        if "Records" in data:
            return data["Records"]

    # リスト直接の場合
    if isinstance(data, list):
        return data

    print(f"⚠️  認識できないJSON構造です。トップレベルのキー: {list(data.keys()) if isinstance(data, dict) else type(data)}")
    return []


def parse_latlng(s: str) -> tuple[float, float] | None:
    """
    様々な形式の緯度経度文字列をパース。
    - "33.8968768°, 130.8413181°" (度数記号付き)
    - "geo:33.8968768,130.8413181" (geoプレフィックス)
    - "33.8968768, 130.8413181" (プレーン)
    """
    if not s:
        return None
    # 度数記号とgeo:プレフィックスを除去
    cleaned = s.replace("°", "").replace("geo:", "").strip()
    parts = [p.strip() for p in cleaned.split(",")]
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            pass
    return None


def extract_location_point(entry: dict) -> dict | None:
    """
    各エントリから緯度・経度・タイムスタンプを抽出。
    複数のJSON形式に対応。
    """
    result = {}

    # --- 旧形式 (Records.json / locations) ---
    if "latitudeE7" in entry:
        result["lat"] = entry["latitudeE7"] / 1e7
        result["lon"] = entry["longitudeE7"] / 1e7
        result["timestamp"] = entry.get("timestamp") or entry.get("timestampMs")
        result["accuracy"] = entry.get("accuracy")
        result["source"] = entry.get("source", "")
        return result

    # --- timelinePath の個別ポイント ---
    if entry.get("_type") == "pathPoint":
        coords = parse_latlng(entry.get("point", ""))
        if coords:
            result["lat"], result["lon"] = coords
            result["timestamp"] = entry.get("time")
            result["accuracy"] = None
            result["source"] = "path"
            return result
        return None

    # --- rawSignals の position ---
    if entry.get("_type") == "rawPosition":
        # LatLng 文字列形式 ("31.589°, 130.551°")
        if "LatLng" in entry:
            coords = parse_latlng(entry["LatLng"])
            if coords:
                result["lat"], result["lon"] = coords
        # lat/lng 数値形式 (別のエクスポート形式)
        elif "lat" in entry or "latE7" in entry:
            result["lat"] = entry.get("lat") or entry.get("latE7", 0) / 1e7
            result["lon"] = entry.get("lng") or entry.get("lngE7", 0) / 1e7

        if "lat" not in result:
            return None

        result["timestamp"] = entry.get("timestamp")
        result["accuracy"] = entry.get("accuracyMeters")
        result["altitude"] = entry.get("altitudeMeters")
        result["speed"] = entry.get("speedMetersPerSecond")
        result["source"] = f"raw:{entry.get('source', '')}"
        return result

    # --- 新形式: visit ---
    if "visit" in entry:
        visit = entry["visit"]
        top = visit.get("topCandidate", {})
        place_loc = top.get("placeLocation", {})
        latlng_str = place_loc.get("latLng", "")
        coords = parse_latlng(latlng_str)
        if coords:
            result["lat"], result["lon"] = coords
        result["timestamp"] = entry.get("startTime") or entry.get("endTime")
        result["place_id"] = top.get("placeId", "")
        result["place_name"] = top.get("placeLocation", {}).get("name", "")
        result["semantic_type"] = top.get("semanticType", "")
        result["accuracy"] = None
        result["source"] = "visit"
        return result if "lat" in result else None

    # --- 新形式: activity (移動) ---
    if "activity" in entry:
        activity = entry["activity"]
        start = activity.get("start", "")
        end_point = activity.get("end", "")
        # start地点を使う
        if isinstance(start, str):
            coords = parse_latlng(start)
        elif isinstance(start, dict):
            coords = parse_latlng(start.get("latLng", ""))
        else:
            coords = None
        if coords:
            result["lat"], result["lon"] = coords
        result["timestamp"] = entry.get("startTime")
        result["activity_type"] = activity.get("topCandidate", {}).get("type", "")
        result["accuracy"] = None
        result["source"] = "activity"
        return result if "lat" in result else None

    # --- 新形式: timelinePoint ---
    if "timelinePoint" in entry:
        tp = entry["timelinePoint"]
        result["lat"] = tp.get("latE7", 0) / 1e7 if "latE7" in tp else tp.get("lat")
        result["lon"] = tp.get("lngE7", 0) / 1e7 if "lngE7" in tp else tp.get("lng")
        result["timestamp"] = tp.get("timestamp")
        result["accuracy"] = tp.get("accuracy")
        result["source"] = "timelinePoint"
        return result if result.get("lat") else None

    return None


def parse_timestamp(ts) -> datetime | None:
    """タイムスタンプ文字列をdatetimeに変換"""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # timestampMs
        if ts > 1e12:
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, str):
        # ISO 8601 (様々なバリエーション)
        for fmt in [
            "%Y-%m-%dT%H:%M:%S.%f%z",    # 2012-10-24T09:00:00.000+09:00
            "%Y-%m-%dT%H:%M:%S%z",        # 2012-10-24T09:00:00+09:00
            "%Y-%m-%dT%H:%M:%S.%fZ",      # 2012-10-24T09:00:00.000Z
            "%Y-%m-%dT%H:%M:%SZ",         # 2012-10-24T09:00:00Z
            "%Y-%m-%dT%H:%M:%S",          # 2012-10-24T09:00:00
        ]:
            try:
                dt = datetime.strptime(ts, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        # timestampMs as string
        try:
            ms = int(ts)
            if ms > 1e12:
                ms = ms / 1000
            return datetime.fromtimestamp(ms, tz=timezone.utc)
        except ValueError:
            pass
    return None


# ---- コマンド実装 ----

def cmd_peek(args):
    """JSON構造の先頭を覗く"""
    filepath = args.file
    size_mb = os.path.getsize(filepath) / (1024 * 1024)
    print(f"📁 ファイル: {filepath}")
    print(f"📏 サイズ: {size_mb:.1f} MB")
    print()

    # 先頭 4KB だけ読む
    with open(filepath, "r", encoding="utf-8") as f:
        head = f.read(4096)

    print("=== 先頭 4KB ===")
    print(head)
    print("================")
    print()

    # トップレベル構造を確認 (小さいファイルなら)
    if size_mb < 500:
        data = load_json_streaming(filepath)
        if isinstance(data, dict):
            print(f"🔑 トップレベルキー: {list(data.keys())}")
            for k, v in data.items():
                if isinstance(v, list):
                    print(f"   {k}: list ({len(v)} items)")
                    if len(v) > 0:
                        print(f"   最初の要素のキー: {list(v[0].keys()) if isinstance(v[0], dict) else type(v[0])}")
                elif isinstance(v, dict):
                    print(f"   {k}: dict (keys: {list(v.keys())[:5]}...)")
                else:
                    print(f"   {k}: {type(v).__name__} = {str(v)[:100]}")
    else:
        print(f"💡 500MB超のため、構造確認は先頭4KBのみ表示しています。")


def cmd_stats(args):
    """統計情報を表示"""
    data = load_json_streaming(args.file)
    entries = find_location_entries(data)
    print(f"\n📊 エントリ総数: {len(entries):,}")

    if not entries:
        print("エントリが見つかりませんでした。")
        return

    # サンプリングして統計
    timestamps = []
    entry_types = Counter()
    parsed_count = 0
    failed_count = 0

    for entry in entries:
        point = extract_location_point(entry)
        if point:
            parsed_count += 1
            ts = parse_timestamp(point.get("timestamp"))
            if ts:
                timestamps.append(ts)
            entry_types[point.get("source", "unknown")] += 1
        else:
            failed_count += 1
            # 未対応形式のキーを記録
            if isinstance(entry, dict):
                entry_types[f"unparsed:{','.join(sorted(entry.keys())[:3])}"] += 1

    print(f"✅ パース成功: {parsed_count:,}")
    print(f"⚠️  パース失敗: {failed_count:,}")
    print(f"\n📋 エントリタイプ:")
    for t, c in entry_types.most_common(10):
        print(f"   {t}: {c:,}")

    if timestamps:
        timestamps.sort()
        print(f"\n📅 期間:")
        print(f"   最古: {timestamps[0].strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"   最新: {timestamps[-1].strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"   日数: {(timestamps[-1] - timestamps[0]).days:,} 日間")

        # 年ごとの件数
        year_counts = Counter(ts.year for ts in timestamps)
        print(f"\n📆 年別レコード数:")
        for year in sorted(year_counts.keys()):
            print(f"   {year}: {year_counts[year]:,}")


def cmd_to_csv(args):
    """CSVに変換（D1投入用）"""
    data = load_json_streaming(args.file)
    entries = find_location_entries(data)

    after_dt = datetime.strptime(args.after, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.after else None
    before_dt = datetime.strptime(args.before, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.before else None

    output = args.output or "locations.csv"
    count = 0
    skipped = 0

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "lat", "lon", "accuracy", "source", "place_id", "semantic_type", "activity_type", "altitude", "speed"])

        for entry in entries:
            point = extract_location_point(entry)
            if not point:
                continue

            ts = parse_timestamp(point.get("timestamp"))
            if ts:
                if after_dt and ts < after_dt:
                    skipped += 1
                    continue
                if before_dt and ts >= before_dt:
                    skipped += 1
                    continue
                ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S%z")
            else:
                ts_str = ""

            writer.writerow([
                ts_str,
                point.get("lat", ""),
                point.get("lon", ""),
                point.get("accuracy", ""),
                point.get("source", ""),
                point.get("place_id", ""),
                point.get("semantic_type", ""),
                point.get("activity_type", ""),
                point.get("altitude", ""),
                point.get("speed", ""),
            ])
            count += 1

    print(f"\n✅ {count:,} レコードを {output} に出力しました。")
    if skipped:
        print(f"⏭️  {skipped:,} レコードがフィルタで除外されました。")
    print(f"📏 ファイルサイズ: {os.path.getsize(output) / (1024*1024):.1f} MB")


def cmd_split(args):
    """JSONファイルを分割（Dawarich等のインポート制限対策）"""
    data = load_json_streaming(args.file)
    entries = find_location_entries(data)

    output_dir = Path(args.output or "chunks")
    output_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = (args.max_mb or 4) * 1024 * 1024

    # 元のトップレベルキーを特定
    top_key = "locations"  # デフォルト
    if isinstance(data, dict):
        for k in ["locations", "semanticSegments", "timelineObjects", "Records"]:
            if k in data:
                top_key = k
                break

    chunk_idx = 0
    current_chunk = []
    current_size = 0

    for entry in entries:
        entry_json = json.dumps(entry, ensure_ascii=False)
        entry_size = len(entry_json.encode("utf-8"))

        if current_size + entry_size > max_bytes and current_chunk:
            # チャンクを書き出し
            chunk_path = output_dir / f"chunk_{chunk_idx:04d}.json"
            with open(chunk_path, "w", encoding="utf-8") as f:
                json.dump({top_key: current_chunk}, f, ensure_ascii=False)
            print(f"   📄 {chunk_path.name}: {len(current_chunk):,} entries ({current_size / (1024*1024):.1f} MB)")
            chunk_idx += 1
            current_chunk = []
            current_size = 0

        current_chunk.append(entry)
        current_size += entry_size

    # 残り
    if current_chunk:
        chunk_path = output_dir / f"chunk_{chunk_idx:04d}.json"
        with open(chunk_path, "w", encoding="utf-8") as f:
            json.dump({top_key: current_chunk}, f, ensure_ascii=False)
        print(f"   📄 {chunk_path.name}: {len(current_chunk):,} entries ({current_size / (1024*1024):.1f} MB)")
        chunk_idx += 1

    print(f"\n✅ {chunk_idx} 個のチャンクに分割しました → {output_dir}/")


# ---- メイン ----

def main():
    parser = argparse.ArgumentParser(
        description="Google ロケーション履歴 JSON パーサー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    # peek
    p_peek = sub.add_parser("peek", help="JSON構造の先頭を覗く")
    p_peek.add_argument("file", help="JSONファイルパス")

    # stats
    p_stats = sub.add_parser("stats", help="統計情報を表示")
    p_stats.add_argument("file", help="JSONファイルパス")

    # to_csv
    p_csv = sub.add_parser("to_csv", help="CSVに変換")
    p_csv.add_argument("file", help="JSONファイルパス")
    p_csv.add_argument("-o", "--output", help="出力ファイル名 (default: locations.csv)")
    p_csv.add_argument("--after", help="この日付以降のみ (YYYY-MM-DD)")
    p_csv.add_argument("--before", help="この日付より前のみ (YYYY-MM-DD)")

    # split
    p_split = sub.add_parser("split", help="JSONを分割")
    p_split.add_argument("file", help="JSONファイルパス")
    p_split.add_argument("-o", "--output", help="出力ディレクトリ (default: chunks/)")
    p_split.add_argument("--max-mb", type=int, default=4, help="チャンクの最大サイズ MB (default: 4)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"peek": cmd_peek, "stats": cmd_stats, "to_csv": cmd_to_csv, "split": cmd_split}[args.command](args)


if __name__ == "__main__":
    main()