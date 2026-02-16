#!/usr/bin/env python3
"""
H3 backfill スクリプト
=====================
既存の位置情報レコードにH3空間インデックスを付与する。
POST /locations/backfill-h3 エンドポイントを繰り返し呼び出し、
全レコードの処理が完了するまで実行する。

使い方:
  python scripts/backfill_h3.py \
      --api-url https://location-sync-api.kiakiraki.workers.dev \
      --token YOUR_API_TOKEN

  # ドライラン（1回だけ呼び出して結果を確認）
  python scripts/backfill_h3.py --token YOUR_API_TOKEN --dry-run
"""

import argparse
import json
import sys
import time

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def call_backfill(api_url: str, token: str) -> dict:
    """backfillエンドポイントを1回呼び出す"""
    url = f"{api_url}/locations/backfill-h3"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if HAS_REQUESTS:
        resp = requests.post(url, headers=headers, timeout=120)
        resp.raise_for_status()
        return resp.json()
    else:
        import urllib.request
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="H3 backfill スクリプト")
    parser.add_argument("--api-url", default="https://location-sync-api.kiakiraki.workers.dev",
                        help="API base URL")
    parser.add_argument("--token", required=True, help="API Bearer Token")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="リクエスト間の待ち時間(秒) (default: 1.0)")
    parser.add_argument("--dry-run", action="store_true",
                        help="1回だけ呼び出して結果を確認")
    args = parser.parse_args()

    print(f"🔧 H3 backfill 開始")
    print(f"   API: {args.api_url}")
    print()

    total_updated = 0
    iteration = 0

    while True:
        iteration += 1
        try:
            result = call_backfill(args.api_url, args.token)
        except Exception as e:
            print(f"   ❌ リクエストエラー: {e}")
            print(f"   {args.delay * 5:.0f}秒後にリトライ...")
            time.sleep(args.delay * 5)
            continue

        updated = result.get("updated", 0)
        remaining = result.get("remaining", 0)
        status = result.get("status", "unknown")
        total_updated += updated

        print(f"   [{iteration}] ✅ {updated} updated, {remaining} remaining (status: {status})")

        if status == "complete":
            break

        if args.dry_run:
            print(f"\n🔍 ドライラン: 1回の実行で終了")
            break

        time.sleep(args.delay)

    print(f"\n{'='*50}")
    print(f"📊 backfill 完了")
    print(f"   合計更新: {total_updated:,}")
    print(f"   リクエスト回数: {iteration}")


if __name__ == "__main__":
    main()
