"""Inspect every allowlisted gold table and report watermark-column candidates.

Runs locally (or in any place that can reach the Databricks SQL warehouse).
Uses the same env vars the GEMS-API uses, so pointing it at `API/.env` just
works:

    python tools/check_watermark_columns.py --env-file API/.env

Output: a Markdown table listing, for each allowlisted table:

    - total columns
    - names of timestamp/date/int/bigint columns (the Download page treats
      these as watermark candidates)
    - verdict: OK (has a candidate), MISSING (no candidate; enable CDF)

And, for tables with no candidate, the exact `ALTER TABLE ... SET TBLPROPERTIES`
statement to enable Delta Change Data Feed so incremental downloads can be
implemented via `table_changes(...)` instead.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_env(env_path: Path | None) -> None:
    if env_path is None:
        return
    if not env_path.exists():
        print(f"WARNING: env file not found: {env_path}", file=sys.stderr)
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        print(
            "python-dotenv not installed. Either `pip install python-dotenv` or "
            "export the Databricks env vars manually before running this script.",
            file=sys.stderr,
        )
        sys.exit(2)
    load_dotenv(env_path)


def _cfg() -> dict:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://") :]
    return {
        "host": host,
        "http_path": os.environ.get("DATABRICKS_HTTP_PATH", "").strip(),
        "token": os.environ.get("DATABRICKS_TOKEN", "").strip(),
        "catalog": os.environ.get("GEMS_CATALOG", "gems_catalog").strip(),
        "schema": os.environ.get("GEMS_SCHEMA", "gold_v1").strip(),
        "allowed": [
            t.strip()
            for t in os.environ.get("ALLOWED_TABLES", "").split(",")
            if t.strip()
        ],
    }


def _describe(cursor, catalog: str, schema: str, table: str) -> list[tuple[str, str]]:
    """Return [(col_name, col_type), ...] for the given fully-qualified table."""
    cursor.execute(f"DESCRIBE TABLE `{catalog}`.`{schema}`.`{table}`")
    cols: list[tuple[str, str]] = []
    for row in cursor.fetchall():
        name = str(row[0]) if row[0] is not None else ""
        if not name or name.startswith("#") or name.strip() == "":
            break
        dtype = str(row[1]) if row[1] is not None else ""
        cols.append((name, dtype))
    return cols


_WATERMARK_KEYWORDS = ("timestamp", "date", "bigint", "long", "int")


def _watermark_candidates(cols: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name, dtype in cols:
        t = dtype.lower()
        if any(k in t for k in _WATERMARK_KEYWORDS):
            out.append((name, dtype))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--env-file",
        type=Path,
        default=Path("API/.env"),
        help="Path to an .env file with Databricks + GEMS env vars (default: API/.env).",
    )
    p.add_argument(
        "--no-env-file",
        action="store_true",
        help="Skip loading any .env file; rely on already-exported env vars.",
    )
    args = p.parse_args()

    _load_env(None if args.no_env_file else args.env_file)
    cfg = _cfg()

    if not cfg["host"] or not cfg["http_path"] or not cfg["token"]:
        print(
            "ERROR: DATABRICKS_HOST / DATABRICKS_HTTP_PATH / DATABRICKS_TOKEN "
            "must be set (in env or via --env-file).",
            file=sys.stderr,
        )
        return 2

    if not cfg["allowed"]:
        print(
            "ERROR: ALLOWED_TABLES is empty. Set it in the .env before running.",
            file=sys.stderr,
        )
        return 2

    try:
        from databricks import sql as dsql
    except ImportError:
        print(
            "ERROR: databricks-sql-connector not installed. "
            "Run `pip install databricks-sql-connector` first.",
            file=sys.stderr,
        )
        return 2

    print(f"# Watermark-column report for `{cfg['catalog']}.{cfg['schema']}`\n")
    print(f"Inspecting {len(cfg['allowed'])} allowlisted tables...\n")

    ok: list[str] = []
    missing: list[str] = []
    rows_md: list[str] = [
        "| Table | # cols | Watermark candidates | Verdict |",
        "|-------|-------:|----------------------|---------|",
    ]
    errors: list[tuple[str, str]] = []

    with dsql.connect(
        server_hostname=cfg["host"],
        http_path=cfg["http_path"],
        access_token=cfg["token"],
    ) as conn:
        with conn.cursor() as cur:
            for table in cfg["allowed"]:
                try:
                    cols = _describe(cur, cfg["catalog"], cfg["schema"], table)
                except Exception as e:
                    errors.append((table, str(e)))
                    rows_md.append(f"| `{table}` | ? | (describe failed) | ERROR |")
                    continue

                candidates = _watermark_candidates(cols)
                if candidates:
                    ok.append(table)
                    cand_str = ", ".join(f"`{n}` ({t})" for n, t in candidates[:5])
                    if len(candidates) > 5:
                        cand_str += f", +{len(candidates) - 5} more"
                    verdict = "OK"
                else:
                    missing.append(table)
                    cand_str = "(none)"
                    verdict = "MISSING"
                rows_md.append(f"| `{table}` | {len(cols)} | {cand_str} | {verdict} |")

    print("\n".join(rows_md))
    print()
    print(f"Summary: {len(ok)} OK, {len(missing)} MISSING, {len(errors)} errors.\n")

    if missing:
        print("## Enable Delta Change Data Feed on tables without a watermark\n")
        print(
            "Run these in a Databricks SQL editor or a notebook attached to the same "
            f"catalog (`{cfg['catalog']}`). CDF lets incremental reads use "
            "`table_changes(...)` when no natural watermark exists.\n"
        )
        print("```sql")
        for table in missing:
            fq = f"`{cfg['catalog']}`.`{cfg['schema']}`.`{table}`"
            print(f"ALTER TABLE {fq} SET TBLPROPERTIES (delta.enableChangeDataFeed = true);")
        print("```")
        print()

    if errors:
        print("## Errors\n")
        for t, e in errors:
            print(f"- `{t}`: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
