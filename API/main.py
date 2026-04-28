"""
GEMS read-only CSV API: API-key auth + allowlisted gold tables via Databricks SQL warehouse (PAT).
"""

import csv
import hashlib
import hmac
import io
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

# Local: API/.env. Azure: use Application settings (env vars); .env optional if present.
load_dotenv(Path(__file__).resolve().parent / ".env")

app = FastAPI(title="GEMS Gold Export API", version="0.4.0")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_IDENT_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
# Allow digits, T, Z, space, dash, colon, dot, plus (ISO-8601 timestamps or plain ints/decimals).
_SINCE_VALUE_RE = re.compile(r"^[0-9A-Za-z\-:.+ ]{1,64}$")
_API_KEY_PREFIX = "gems_live_"
_API_KEY_PARTITION = "api_key"


def _cfg() -> dict:
    host = os.getenv("DATABRICKS_HOST", "").strip().rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://") :]
    return {
        "host": host,
        "http_path": os.getenv("DATABRICKS_HTTP_PATH", "").strip(),
        "token": os.getenv("DATABRICKS_TOKEN", "").strip(),
        "catalog": os.getenv("GEMS_CATALOG", "gems_catalog").strip(),
        "schema": os.getenv("GEMS_SCHEMA", "gold_v1").strip(),
        "allowed": _parse_allowed_tables(os.getenv("ALLOWED_TABLES", "")),
        "max_rows": int(os.getenv("MAX_EXPORT_ROWS", "100000")),
        "tables_conn": os.getenv("AZURE_TABLES_CONNECTION_STRING", "").strip(),
        "api_keys_table": os.getenv("AZURE_API_KEYS_TABLE", "gemsApiKeys").strip(),
        "api_key_pepper": os.getenv("API_KEY_PEPPER", "").strip(),
        "allowed_users": _parse_allowed_users(os.getenv("ALLOWED_USERS", "")),
        "allowed_domains": _parse_allowed_users(os.getenv("ALLOWED_DOMAINS", "")),
    }


def _parse_allowed_tables(raw: str) -> frozenset[str]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return frozenset(parts)


def _parse_allowed_users(raw: str) -> frozenset[str]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return frozenset(parts)


def _owner_is_authorized(owner: str) -> bool:
    c = _cfg()
    allowed_users = c["allowed_users"]
    allowed_domains = c["allowed_domains"]
    if not allowed_users and not allowed_domains:
        return True
    normalized = (owner or "").strip().lower()
    if normalized in allowed_users:
        return True
    domain = normalized.rsplit("@", 1)[-1] if "@" in normalized else ""
    return bool(domain) and domain in allowed_domains


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_api_key(raw_key: str, pepper: str) -> str:
    return hmac.new(
        pepper.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _api_key_table_client():
    c = _cfg()
    if not c["tables_conn"] or not c["api_key_pepper"] or not c["api_keys_table"]:
        raise HTTPException(500, "Server misconfigured: API key storage not configured")
    try:
        from azure.data.tables import TableServiceClient

        svc = TableServiceClient.from_connection_string(c["tables_conn"])
        return svc.get_table_client(c["api_keys_table"])
    except Exception as e:
        raise HTTPException(500, f"Server misconfigured: API key table unavailable: {e!s}") from e


def get_api_key(x_api_key: Annotated[str | None, Depends(api_key_header)]) -> dict:
    c = _cfg()
    if not x_api_key or not x_api_key.startswith(_API_KEY_PREFIX):
        raise HTTPException(401, "Invalid or missing API key (use header X-API-Key)")
    key_hash = _hash_api_key(x_api_key, c["api_key_pepper"])
    client = _api_key_table_client()
    try:
        entity = client.get_entity(_API_KEY_PARTITION, key_hash)
    except Exception:
        raise HTTPException(401, "Invalid or missing API key (use header X-API-Key)")

    if str(entity.get("revokedAt", "") or ""):
        raise HTTPException(401, "API key has been revoked")

    owner = str(entity.get("owner", ""))
    if not _owner_is_authorized(owner):
        raise HTTPException(403, "API key owner is no longer authorized")

    try:
        entity["lastUsedAt"] = _utc_now()
        client.upsert_entity(entity)
    except Exception:
        pass

    return {
        "owner": owner,
        "name": str(entity.get("name", "")),
        "key_hash": key_hash,
    }


def _validate_table_name(table: str) -> str:
    t = table.strip()
    if not t or not _IDENT_RE.match(t):
        raise HTTPException(400, "Invalid table name")
    allowed = _cfg()["allowed"]
    if not allowed:
        raise HTTPException(500, "Server misconfigured: ALLOWED_TABLES empty")
    if t not in allowed:
        raise HTTPException(403, "Table not allowed for this API key")
    return t


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


def _describe_columns(table: str) -> list[dict]:
    """Return [{'name','type'}, ...] via DESCRIBE TABLE on an allowlisted fully-qualified table."""
    c = _cfg()
    fq = f"{c['catalog']}.{c['schema']}.{table}"
    try:
        conn = _connect_db()
        cur = conn.cursor()
        try:
            cur.execute(f"DESCRIBE TABLE {fq}")
            rows = cur.fetchall()
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        raise HTTPException(502, f"Databricks DESCRIBE failed: {e!s}") from e

    cols: list[dict] = []
    for row in rows:
        name = str(row[0]) if len(row) > 0 and row[0] is not None else ""
        # DESCRIBE TABLE output ends with blank row(s) and partition info; skip them.
        if not name or name.startswith("#") or name.strip() == "":
            break
        dtype = str(row[1]) if len(row) > 1 and row[1] is not None else ""
        cols.append({"name": name, "type": dtype})
    return cols


def _validate_since_column(table: str, col: str) -> dict:
    """Ensure `col` exists on `table` and return its column info (name, type)."""
    if not _IDENT_RE.match(col):
        raise HTTPException(400, "Invalid since_col")
    schema = _describe_columns(table)
    for c in schema:
        if c["name"] == col:
            return c
    raise HTTPException(400, f"Column '{col}' not found on table '{table}'")


def _format_since_literal(value: str, col_type: str) -> str:
    """Return a safe SQL literal for the WHERE clause based on the column's type."""
    if not _SINCE_VALUE_RE.match(value):
        raise HTTPException(400, "Invalid since_value format")
    t = col_type.lower()
    # Integer-like types: emit as number; reject non-digits.
    if any(k in t for k in ("int", "long", "bigint", "short", "tinyint")):
        if not re.match(r"^-?\d+$", value):
            raise HTTPException(400, "since_value must be an integer for this column")
        return value
    # Numeric types: decimal / double / float.
    if any(k in t for k in ("decimal", "double", "float", "numeric")):
        if not re.match(r"^-?\d+(\.\d+)?$", value):
            raise HTTPException(400, "since_value must be numeric for this column")
        return value
    # Timestamp / date / string: quote as string literal; no quotes possible because regex forbids them.
    return f"'{value}'"


@app.get("/health")
def health():
    c = _cfg()
    ok = bool(
        c["host"]
        and c["http_path"]
        and c["token"]
        and c["allowed"]
        and c["tables_conn"]
        and c["api_keys_table"]
        and c["api_key_pepper"]
    )
    return {"status": "ok" if ok else "degraded", "allowed_table_count": len(c["allowed"])}


@app.get("/tables")
def list_tables(_: Annotated[str, Depends(get_api_key)]):
    """Tables this API key may export (allowlist)."""
    return {"tables": sorted(_cfg()["allowed"])}


def _table_version(table: str) -> dict:
    t = _validate_table_name(table)
    c = _cfg()
    fq = f"{c['catalog']}.{c['schema']}.{t}"
    try:
        conn = _connect_db()
        cur = conn.cursor()
        try:
            cur.execute(f"DESCRIBE HISTORY {fq} LIMIT 1")
            rows = cur.fetchall()
            columns = [col[0] for col in cur.description] if cur.description else []
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        raise HTTPException(502, f"Databricks table history lookup failed: {e!s}") from e

    if not rows:
        raise HTTPException(404, f"No Delta history found for table '{t}'")

    row = dict(zip(columns, rows[0], strict=False))
    return {
        "table": t,
        "catalog": c["catalog"],
        "schema": c["schema"],
        "version": _json_safe(row.get("version")),
        "timestamp": _json_safe(row.get("timestamp")),
        "operation": _json_safe(row.get("operation")),
    }


@app.get("/version/{table}")
def get_version(table: str, _: Annotated[dict, Depends(get_api_key)]):
    """Return the latest Delta table version for one allowlisted gold table."""
    return _table_version(table)


@app.get("/versions")
def get_versions(_: Annotated[dict, Depends(get_api_key)]):
    """Return latest Delta table versions for all allowlisted gold tables."""
    versions = [_table_version(table) for table in sorted(_cfg()["allowed"])]
    return {"tables": versions}


@app.get("/schema/{table}")
def get_schema(table: str, _: Annotated[str, Depends(get_api_key)]):
    """Return column name/type list for an allowlisted table."""
    t = _validate_table_name(table)
    return {"table": t, "columns": _describe_columns(t)}


@app.get("/preview/{table}")
def preview(
    table: str,
    _: Annotated[str, Depends(get_api_key)],
    limit: int = Query(100, ge=1, le=1000),
):
    """Return up to `limit` rows as JSON for quick browsing."""
    t = _validate_table_name(table)
    c = _cfg()
    fq = f"{c['catalog']}.{c['schema']}.{t}"
    sql = f"SELECT * FROM {fq} LIMIT {int(limit)}"
    try:
        conn = _connect_db()
        cur = conn.cursor()
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            columns = [col[0] for col in cur.description] if cur.description else []
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        raise HTTPException(502, f"Databricks query failed: {e!s}") from e

    data = [dict(zip(columns, [_json_safe(v) for v in r], strict=False)) for r in rows]
    return {"table": t, "columns": columns, "rows": data}


def _json_safe(v):
    """Convert non-JSON-serializable values (Decimal, datetime, date) to strings."""
    try:
        import datetime as _dt
        from decimal import Decimal

        if isinstance(v, (Decimal,)):
            return str(v)
        if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
            return v.isoformat()
    except Exception:
        pass
    return v


@app.get("/export/{table}.csv")
def export_csv(
    table: str,
    _: Annotated[str, Depends(get_api_key)],
    since_col: str | None = Query(None, description="Optional watermark column (must exist on table)"),
    since_value: str | None = Query(None, description="Return rows WHERE since_col > since_value"),
):
    """
    Download allowlisted table as CSV. Latest snapshot from Databricks (warehouse sees current Delta state).
    If both `since_col` and `since_value` are provided, only rows strictly greater than `since_value` are returned.
    """
    t = _validate_table_name(table)
    c = _cfg()
    fq = f"{c['catalog']}.{c['schema']}.{t}"

    where = ""
    if since_col and since_value is not None:
        col_info = _validate_since_column(t, since_col)
        literal = _format_since_literal(since_value, col_info["type"])
        where = f" WHERE {since_col} > {literal}"
    elif since_col or since_value:
        raise HTTPException(400, "Provide both since_col and since_value, or neither")

    sql = f"SELECT * FROM {fq}{where} LIMIT {c['max_rows']}"

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


# ---------------------------------------------------------------------------
# /query — constrained ad-hoc SELECT for the dashboard chatbot.
# ---------------------------------------------------------------------------

_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|merge|grant|revoke|copy|call|use|set|load|msck|analyze|optimize|vacuum|refresh)\b",
    re.IGNORECASE,
)


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=20_000)
    limit: int | None = Field(default=1000, ge=1, le=100_000)


def _validate_select_sql(sql: str, allowed: frozenset[str], catalog: str, schema: str) -> str:
    """Return a sanitized SELECT/WITH statement (without trailing ';') or raise."""
    s = sql.strip()
    while s.endswith(";"):
        s = s[:-1].strip()
    if not s:
        raise HTTPException(400, "SQL is empty")
    if ";" in s:
        raise HTTPException(400, "Only a single SQL statement is allowed")
    if not re.match(r"^\s*(select|with)\b", s, re.IGNORECASE):
        raise HTTPException(400, "Only SELECT or WITH statements are allowed")
    if _FORBIDDEN_SQL.search(s):
        raise HTTPException(400, "Forbidden SQL keyword detected")

    try:
        import sqlglot
        from sqlglot import exp as sqlglot_exp
    except Exception as e:
        raise HTTPException(500, f"Server missing SQL parser: {e!s}") from e

    try:
        tree = sqlglot.parse_one(s, read="databricks")
    except Exception as e:
        raise HTTPException(400, f"SQL parse failed: {e!s}") from e

    if tree is None:
        raise HTTPException(400, "SQL parse returned no tree")

    cte_names: set[str] = set()
    for cte in tree.find_all(sqlglot_exp.CTE):
        try:
            cte_names.add(cte.alias_or_name)
        except Exception:
            pass

    for t in tree.find_all(sqlglot_exp.Table):
        name = t.name
        db = t.args.get("db")
        cat = t.args.get("catalog")
        db_name = db.name if db is not None else None
        cat_name = cat.name if cat is not None else None

        if name in cte_names and db_name is None and cat_name is None:
            continue

        if cat_name is not None and cat_name != catalog:
            raise HTTPException(403, f"Catalog '{cat_name}' is not allowed")
        if db_name is not None and db_name != schema:
            raise HTTPException(403, f"Schema '{db_name}' is not allowed")
        if name not in allowed:
            raise HTTPException(403, f"Table '{name}' is not allowed for this API key")

    return s


@app.post("/query")
def query(req: QueryRequest, _: Annotated[str, Depends(get_api_key)]):
    """
    Run a constrained SELECT against the allowlisted gold tables.

    Intended for the dashboard chatbot. Server enforces:
      - SELECT / WITH only
      - single statement
      - references must be to allowlisted tables (or CTE names)
      - hard row cap (wrapped in an outer LIMIT)
    """
    c = _cfg()
    safe = _validate_select_sql(req.sql, c["allowed"], c["catalog"], c["schema"])

    requested = int(req.limit or 1000)
    limit = min(requested, c["max_rows"])
    wrapped_sql = f"SELECT * FROM ({safe}) AS __gems_q LIMIT {limit}"

    try:
        conn = _connect_db()
        cur = conn.cursor()
        try:
            cur.execute(wrapped_sql)
            rows = cur.fetchall()
            columns = [col[0] for col in cur.description] if cur.description else []
        finally:
            cur.close()
            conn.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Databricks query failed: {e!s}") from e

    data = [dict(zip(columns, [_json_safe(v) for v in r], strict=False)) for r in rows]
    return {
        "columns": columns,
        "rows": data,
        "row_count": len(data),
        "limit_applied": limit,
        "truncated": len(data) >= limit,
    }
