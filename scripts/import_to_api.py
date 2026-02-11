#!/usr/bin/env python3
"""
CSV → location-sync API バッチインポートスクリプト
===================================================
parse_location_history.py で出力した locations.csv を
location-sync API の /locations/batch エンドポイントに投入する。

D1のバッチ制限を考慮し、チャンク単位で送信する。

使い方:
  python import_to_api.py locations.csv \
      --api-url https://location-sync-api.kiakiraki.workers.dev \
      --token YOUR_API_TOKEN \
      --chunk-size 500

  # ドライラン（送信せずJSONファイルに出力）
  python import_to_api.py locations.csv --dry-run -o chunks/
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def parse_csv(filepath: str) -> list[dict]:
    """CSVを読み込んでdictリストに変換"""
    records = []
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            record = {
                "timestamp": row.get("timestamp") or None,
                "lat": float(row["lat"]) if row.get("lat") else None,
                "lon": float(row["lon"]) if row.get("lon") else None,
                "accuracy": float(row["accuracy"]) if row.get("accuracy") else None,
                "source": row.get("source") or None,
                "place_id": row.get("place_id") or None,
                "semantic_type": row.get("semantic_type") or None,
                "activity_type": row.get("activity_type") or None,
                "altitude": float(row["altitude"]) if row.get("altitude") else None,
                "speed": float(row["speed"]) if row.get("speed") else None,
            }
            if record["lat"] is not None and record["lon"] is not None:
                records.append(record)
    return records


def send_batch(api_url: str, token: str, locations: list[dict], chunk_idx: int) -> dict:
    """APIにバッチ送信"""
    if not HAS_REQUESTS:
        # requestsがない場合はurllibで代替
        import urllib.request
        data = json.dumps({"locations": locations}).encode("utf-8")
        req = urllib.request.Request(
            f"{api_url}/locations/batch",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    else:
        resp = requests.post(
            f"{api_url}/locations/batch",
            json={"locations": locations},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()


def main():
    parser = argparse.ArgumentParser(description="CSV → location-sync API インポーター")
    parser.add_argument("csv_file", help="入力CSVファイル")
    parser.add_argument("--api-url", default="https://location-sync-api.kiakiraki.workers.dev",
                        help="API base URL")
    parser.add_argument("--token", help="API Bearer Token")
    parser.add_argument("--chunk-size", type=int, default=500,
                        help="1回のAPIリクエストあたりのレコード数 (default: 500)")
    parser.add_argument("--dry-run", action="store_true",
                        help="APIに送信せず、JSONファイルに出力")
    parser.add_argument("-o", "--output", default="chunks",
                        help="ドライラン時の出力ディレクトリ")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="リクエスト間の待ち時間(秒) (default: 0.5)")
    args = parser.parse_args()

    print(f"📖 {args.csv_file} を読み込み中...")
    records = parse_csv(args.csv_file)
    print(f"✅ {len(records):,} レコード読み込み完了")

    total_chunks = (len(records) + args.chunk_size - 1) // args.chunk_size
    total_imported = 0
    total_errors = 0

    if args.dry_run:
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n🔍 ドライラン: {output_dir}/ にJSON出力")

        for i in range(0, len(records), args.chunk_size):
            chunk = records[i:i + args.chunk_size]
            chunk_idx = i // args.chunk_size
            chunk_path = output_dir / f"batch_{chunk_idx:04d}.json"
            with open(chunk_path, "w", encoding="utf-8") as f:
                json.dump({"locations": chunk}, f, ensure_ascii=False)
            print(f"   📄 {chunk_path.name}: {len(chunk)} records")

        print(f"\n✅ {total_chunks} 個のJSONファイルに出力完了")
        print(f"\n💡 curlで手動インポートする場合:")
        print(f'   for f in {args.output}/batch_*.json; do')
        print(f'     curl -X POST "{args.api_url}/locations/batch" \\')
        print(f'       -H "Authorization: Bearer YOUR_TOKEN" \\')
        print(f'       -H "Content-Type: application/json" \\')
        print(f'       -d @"$f" && sleep 0.5')
        print(f'   done')
        return

    if not args.token:
        print("❌ --token が必要です（ドライラン以外）")
        sys.exit(1)

    print(f"\n🚀 {total_chunks} チャンクに分けて送信します")
    print(f"   API: {args.api_url}")
    print(f"   チャンクサイズ: {args.chunk_size}")
    print()

    for i in range(0, len(records), args.chunk_size):
        chunk = records[i:i + args.chunk_size]
        chunk_idx = i // args.chunk_size

        try:
            result = send_batch(args.api_url, args.token, chunk, chunk_idx)
            imported = result.get("imported", 0)
            errors = result.get("errors", 0)
            total_imported += imported
            total_errors += errors
            print(f"   [{chunk_idx + 1}/{total_chunks}] ✅ {imported} imported, {errors} errors")
        except Exception as e:
            total_errors += len(chunk)
            print(f"   [{chunk_idx + 1}/{total_chunks}] ❌ Error: {e}")

        if i + args.chunk_size < len(records):
            time.sleep(args.delay)

    print(f"\n{'='*50}")
    print(f"📊 インポート完了")
    print(f"   成功: {total_imported:,}")
    print(f"   失敗: {total_errors:,}")
    print(f"   合計: {len(records):,}")


if __name__ == "__main__":
    main()
