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


class ApiError(Exception):
    """API呼び出し失敗。retryable=Trueなら再送する価値がある（ネットワーク断・429・5xx）"""

    def __init__(self, message: str, retryable: bool, fatal: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.fatal = fatal  # 401/403など、以降の全チャンクも失敗するもの


def _classify_http_status(status: int) -> tuple[bool, bool]:
    """HTTPステータス → (retryable, fatal)"""
    if status in (401, 403):
        return False, True
    if status == 429 or status >= 500:
        return True, False
    return False, False


def send_batch(api_url: str, token: str, locations: list[dict]) -> dict:
    """APIにバッチ送信。失敗時はApiErrorを投げる"""
    if not HAS_REQUESTS:
        # requestsがない場合はurllibで代替
        import urllib.error
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
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            retryable, fatal = _classify_http_status(e.code)
            raise ApiError(f"HTTP {e.code}: {e.reason}", retryable, fatal) from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise ApiError(f"接続エラー: {e}", retryable=True) from e
    else:
        try:
            resp = requests.post(
                f"{api_url}/locations/batch",
                json={"locations": locations},
                headers={"Authorization": f"Bearer {token}"},
                timeout=60,
            )
        except requests.exceptions.RequestException as e:
            raise ApiError(f"接続エラー: {e}", retryable=True) from e
        if resp.status_code >= 400:
            retryable, fatal = _classify_http_status(resp.status_code)
            body = " ".join(resp.text.split())[:120]
            raise ApiError(f"HTTP {resp.status_code}: {body}", retryable, fatal)
        return resp.json()


def send_batch_with_retry(api_url: str, token: str, locations: list[dict],
                          max_retries: int, label: str) -> dict:
    """リトライ付きバッチ送信。サーバー側が冪等なので再送しても重複しない"""
    for attempt in range(max_retries + 1):
        try:
            return send_batch(api_url, token, locations)
        except ApiError as e:
            if not e.retryable or attempt == max_retries:
                raise
            wait = 2 ** attempt
            print(f"   {label} ⚠️  {e} → {wait}秒後にリトライ ({attempt + 1}/{max_retries})")
            time.sleep(wait)
    raise AssertionError("unreachable")


def save_failed_chunk(output_dir: Path, chunk_idx: int, locations: list[dict]) -> Path:
    """最終的に失敗したチャンクを再送用に保存"""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"batch_{chunk_idx:04d}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"locations": locations}, f, ensure_ascii=False)
    return path


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
    parser.add_argument("--max-retries", type=int, default=3,
                        help="チャンク失敗時のリトライ回数。指数バックオフ 1s/2s/4s... (default: 3)")
    parser.add_argument("--failed-dir", default="failed_chunks",
                        help="リトライしても失敗したチャンクの保存先 (default: failed_chunks)")
    args = parser.parse_args()

    print(f"📖 {args.csv_file} を読み込み中...")
    records = parse_csv(args.csv_file)
    print(f"✅ {len(records):,} レコード読み込み完了")

    total_chunks = (len(records) + args.chunk_size - 1) // args.chunk_size
    total_imported = 0
    total_duplicates = 0
    total_invalid = 0
    total_errors = 0
    failed_chunks: list[Path] = []

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
        label = f"[{chunk_idx + 1}/{total_chunks}]"

        try:
            result = send_batch_with_retry(args.api_url, args.token, chunk,
                                           args.max_retries, label)
            imported = result.get("imported", 0)
            duplicates = result.get("duplicates", 0)
            invalid = result.get("invalid", 0)
            errors = result.get("errors", 0)
            total_imported += imported
            total_duplicates += duplicates
            total_invalid += invalid
            total_errors += errors
            detail = f"{imported} imported"
            if duplicates:
                detail += f", {duplicates} duplicates"
            if invalid:
                detail += f", {invalid} invalid"
            if errors:
                detail += f", {errors} errors"
            print(f"   {label} ✅ {detail}")
        except ApiError as e:
            total_errors += len(chunk)
            path = save_failed_chunk(Path(args.failed_dir), chunk_idx, chunk)
            failed_chunks.append(path)
            print(f"   {label} ❌ {e} → {path} に保存")
            if e.fatal:
                print(f"\n💀 認証エラーのため中断します（残りのチャンクは送信していません）")
                remaining = len(records) - (i + len(chunk))
                if remaining > 0:
                    print(f"   未送信: {remaining:,} レコード")
                sys.exit(1)

        if i + args.chunk_size < len(records):
            time.sleep(args.delay)

    print(f"\n{'='*50}")
    print(f"📊 インポート完了")
    print(f"   新規:   {total_imported:,}")
    print(f"   重複:   {total_duplicates:,}")
    print(f"   不正:   {total_invalid:,}")
    print(f"   失敗:   {total_errors:,}")
    print(f"   合計:   {len(records):,}")

    if failed_chunks:
        print(f"\n⚠️  {len(failed_chunks)} チャンクが失敗しました。再送するには:")
        print(f'   for f in {args.failed_dir}/batch_*.json; do')
        print(f'     curl -X POST "{args.api_url}/locations/batch" \\')
        print(f'       -H "Authorization: Bearer YOUR_TOKEN" \\')
        print(f'       -H "Content-Type: application/json" \\')
        print(f'       -d @"$f" && sleep 0.5')
        print(f'   done')
        sys.exit(1)


if __name__ == "__main__":
    main()
