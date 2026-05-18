from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
sys.path.insert(0, str(DASHBOARD))

from dotenv import load_dotenv

load_dotenv(DASHBOARD / ".env", override=True)


def _connect():
    from databricks import sql as dsql

    host = os.environ["DATABRICKS_HOST"].strip().removeprefix("https://").rstrip("/")
    return dsql.connect(
        server_hostname=host,
        http_path=os.environ["DATABRICKS_HTTP_PATH"].strip(),
        access_token=os.environ["DATABRICKS_TOKEN"].strip(),
    )


def _table_description(name: str) -> str:
    clean = name
    for prefix in ("bronze", "gold"):
        if clean.lower().startswith(prefix):
            clean = clean[len(prefix) :]
            break
    return f"Auto-generated draft for {clean}. TODO: add business description."


def main() -> None:
    catalog = os.environ.get("GEMS_CATALOG", "gems_catalog")
    schema = os.environ.get("GEMS_SCHEMA", "gems_schema")
    out = DASHBOARD / "resources" / "data_dictionary.json"
    tables: list[dict] = []
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SHOW TABLES IN `{catalog}`.`{schema}`")
        table_rows = cur.fetchall()
        names = sorted(str(row[1]) for row in table_rows)
        for name in names:
            cur.execute(f"DESCRIBE TABLE `{catalog}`.`{schema}`.`{name}`")
            columns = []
            for row in cur.fetchall():
                col_name = str(row[0] or "").strip()
                if not col_name or col_name.startswith("#"):
                    continue
                dtype = str(row[1] or "").strip()
                columns.append(
                    {
                        "name": col_name,
                        "dtype": dtype,
                        "description": f"TODO: describe {col_name}.",
                        "unit": ""
                    }
                )
            tables.append(
                {
                    "name": name,
                    "description": _table_description(name),
                    "columns": columns
                }
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"tables": tables}, indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()