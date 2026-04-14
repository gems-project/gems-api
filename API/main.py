"""
GEMS read-only CSV API: API-key auth + allowlisted gold tables via Databricks SQL warehouse (PAT).
"""

import csv
import io
import os
import re
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader

# Local: API/.env. Azure: use Application settings (env vars); .env optional if present.
load_dotenv(Path(__file__).resolve().parent / ".env")

app = FastAPI(title="GEMS Gold Export API", version="0.1.0")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_TABLE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def _cfg() -> dict:
    host = os.getenv("DATABRICKS_HOST", "").strip().rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://") :]
    return {
        "host": host,
        "http_path": os.getenv("DATABRICKS_HTTP_PATH", "").strip(),
        "token": os.getenv("DATABRICKS_TOKEN", "").strip(),
        "catalog": os.getenv("GEMS_CATALOG", "gems_catalog").strip(),
        "schema": os.getenv("GEMS_SCHEMA", "gems_schema").strip(),
        "api_key": os.getenv("GEMS_API_KEY", "").strip(),
        "allowed": _parse_allowed_tables(os.getenv("ALLOWED_TABLES", "")),
        "max_rows": int(os.getenv("MAX_EXPORT_ROWS", "100000")),
    }


def _parse_allowed_tables(raw: str) -> frozenset[str]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return frozenset(parts)


def get_api_key(x_api_key: Annotated[str | None, Depends(api_key_header)]) -> str:
    expected = _cfg()["api_key"]
    if not expected:
        raise HTTPException(500, "Server misconfigured: GEMS_API_KEY not set")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(401, "Invalid or missing API key (use header X-API-Key)")
    return x_api_key


def _validate_table_name(table: str) -> str:
    t = table.strip()
    if not t or not _TABLE_RE.match(t):
        raise HTTPException(400, "Invalid table name")
    allowed = _cfg()["allowed"]
    if not allowed:
        raise HTTPException(500, "Server misconfigured: ALLOWED_TABLES empty")
    if t not in allowed:
        raise HTTPException(403, "Table not allowed for this API key")
    return t


@app.get("/health")
def health():
    c = _cfg()
    ok = bool(c["host"] and c["http_path"] and c["token"] and c["api_key"] and c["allowed"])
    return {"status": "ok" if ok else "degraded", "allowed_table_count": len(c["allowed"])}


@app.get("/tables")
def list_tables(_: Annotated[str, Depends(get_api_key)]):
    """Tables this API key may export (allowlist)."""
    return {"tables": sorted(_cfg()["allowed"])}


def _connect_db():
    from databricks import sql as dsql

    c = _cfg()
    if not c["host"] or not c["http_path"] or not c["token"]:
        raise HTTPException(500, "Server misconfigured: Databricks connection env vars")

    return dsql.connect(
        server_hostname=c["host"],
        http_path=c["http_path"],
        access_token=c["token"],
    )


@app.get("/export/{table}.csv")
def export_csv(table: str, _: Annotated[str, Depends(get_api_key)]):
    """
    Download allowlisted table as CSV. Latest snapshot from Databricks (warehouse sees current Delta state).
    """
    t = _validate_table_name(table)
    c = _cfg()
    fq = f"{c['catalog']}.{c['schema']}.{t}"
    sql = f"SELECT * FROM {fq} LIMIT {c['max_rows']}"

    try:
        conn = _connect_db()
        cur = conn.cursor()
        cur.execute(sql)
    except Exception as e:
        raise HTTPException(502, f"Databricks query failed: {e!s}") from e

    columns = [col[0] for col in cur.description] if cur.description else []

    def generate():
        try:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(columns)
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
            batch_size = 5000
            while True:
                rows = cur.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    w.writerow(row)
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate(0)
        finally:
            cur.close()
            conn.close()

    filename = f"{t}.csv"
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
