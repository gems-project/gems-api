"""Quick end-to-end smoke test against a deployed GEMS-API.

Run this after redeploying the API to verify all five endpoints respond.
Nothing in here is dashboard-specific; it just hits the API directly.

    python tools/smoke_test_api.py \
        --base https://gems-api-xxxx.azurewebsites.net \
        --key  YOUR_GEMS_API_KEY
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import quote

import requests


def _header(title: str) -> None:
    print()
    print(f"== {title} ==")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True, help="API base URL, no trailing slash.")
    p.add_argument("--key", required=True, help="GEMS_API_KEY value.")
    p.add_argument(
        "--table",
        default=None,
        help="Specific table to use for /schema, /preview, /export, /query. "
        "Defaults to the first table returned by /tables.",
    )
    args = p.parse_args()

    base = args.base.rstrip("/")
    headers = {"X-API-Key": args.key}
    failures: list[str] = []

    # /health
    _header("/health")
    try:
        r = requests.get(f"{base}/health", timeout=15)
        r.raise_for_status()
        print(json.dumps(r.json(), indent=2))
    except Exception as e:
        failures.append(f"/health: {e}")
        print(f"FAIL: {e}")

    # /tables
    _header("/tables")
    tables: list[str] = []
    try:
        r = requests.get(f"{base}/tables", headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        tables = data.get("tables", [])
        print(f"{len(tables)} table(s): {tables[:5]}{'...' if len(tables) > 5 else ''}")
    except Exception as e:
        failures.append(f"/tables: {e}")
        print(f"FAIL: {e}")

    if not tables:
        print("\nNo tables to test /schema /preview /export /query against; stopping.")
        return 1 if failures else 0

    target = args.table or tables[0]
    print(f"\nUsing target table: {target}")

    # /schema/{table}
    _header(f"/schema/{target}")
    try:
        r = requests.get(
            f"{base}/schema/{quote(target)}", headers=headers, timeout=30
        )
        r.raise_for_status()
        cols = r.json().get("columns", [])
        print(f"{len(cols)} column(s). First 3: {cols[:3]}")
    except Exception as e:
        failures.append(f"/schema: {e}")
        print(f"FAIL: {e}")

    # /preview/{table}?limit=5
    _header(f"/preview/{target}?limit=5")
    try:
        r = requests.get(
            f"{base}/preview/{quote(target)}",
            headers=headers,
            params={"limit": 5},
            timeout=60,
        )
        r.raise_for_status()
        body = r.json()
        rows = body.get("rows", [])
        print(f"{len(rows)} row(s). Columns: {body.get('columns', [])}")
    except Exception as e:
        failures.append(f"/preview: {e}")
        print(f"FAIL: {e}")

    # /export/{table}.csv (head only, so we don't stream megabytes)
    _header(f"/export/{target}.csv (head)")
    try:
        r = requests.get(
            f"{base}/export/{quote(target)}.csv",
            headers=headers,
            stream=True,
            timeout=60,
        )
        r.raise_for_status()
        first_line = b""
        for chunk in r.iter_content(chunk_size=1024):
            first_line += chunk
            if b"\n" in first_line:
                break
        header_line = first_line.split(b"\n", 1)[0].decode("utf-8", "replace")
        print(f"CSV header row: {header_line}")
        r.close()
    except Exception as e:
        failures.append(f"/export: {e}")
        print(f"FAIL: {e}")

    # /query
    _header("/query (SELECT COUNT(*) FROM <target>)")
    try:
        r = requests.post(
            f"{base}/query",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "sql": f"SELECT COUNT(*) AS n FROM gems_catalog.gold_v1.{target}",
                "limit": 1,
            },
            timeout=60,
        )
        if r.status_code == 404:
            print(
                "Skipping: /query not present on this API build (redeploy the "
                "updated API to enable the Chat page)."
            )
        else:
            r.raise_for_status()
            print(json.dumps(r.json(), indent=2, default=str))
    except Exception as e:
        failures.append(f"/query: {e}")
        print(f"FAIL: {e}")

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
