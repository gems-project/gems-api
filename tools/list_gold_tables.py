"""List every table in gems_catalog.gold_v1 using the dashboard's .env.

Usage (from repo root):

    cd dashboard
    .venv\\Scripts\\activate
    cd ..
    python tools\\list_gold_tables.py

Prints:
    1. goldanimalcharacteristics
    2. goldbodyweight
    3. ...

Copy the names you want into ALLOWED_TABLES in dashboard/.env (comma-separated,
no spaces).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_DASHBOARD_ENV = Path(__file__).resolve().parent.parent / "dashboard" / ".env"
load_dotenv(_DASHBOARD_ENV, override=True)

from databricks import sql  # noqa: E402

HOST = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
if HOST.startswith("https://"):
    HOST = HOST[len("https://") :]
HTTP_PATH = os.environ.get("DATABRICKS_HTTP_PATH", "").strip()
TOKEN = os.environ.get("DATABRICKS_TOKEN", "").strip()
CATALOG = os.environ.get("GEMS_CATALOG", "gems_catalog").strip()
SCHEMA = os.environ.get("GEMS_SCHEMA", "gold_v1").strip()


def main() -> int:
    if not (HOST and HTTP_PATH and TOKEN):
        print("Missing DATABRICKS_* env vars in dashboard/.env.", file=sys.stderr)
        return 1

    print(f"Listing tables in `{CATALOG}`.`{SCHEMA}`...\n")
    with sql.connect(
        server_hostname=HOST, http_path=HTTP_PATH, access_token=TOKEN
    ) as conn, conn.cursor() as cur:
        cur.execute(f"SHOW TABLES IN `{CATALOG}`.`{SCHEMA}`")
        rows = cur.fetchall()

    names = sorted({str(r[1]) for r in rows if r and len(r) > 1})
    for i, n in enumerate(names, start=1):
        print(f"  {i:>3}. {n}")

    print()
    print(f"Total: {len(names)} tables")
    print()
    print("Comma-separated (paste this into ALLOWED_TABLES to allow all):")
    print()
    print(",".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
