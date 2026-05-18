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
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
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


class GemsSchema(str, Enum):
    gold_v1 = "gold_v1"
    gold_v2 = "gold_v2"
    gold_v3 = "gold_v3"


ALLOWED_SCHEMAS = tuple(schema.value for schema in GemsSchema)


@app.get("/")
def root():
    return {
        "name": "GEMS Gold Export API",
        "message": "Use /docs for interactive documentation. Data endpoints require X-API-Key.",
        "docs": "/docs",
        "health": "/health",
        "tables": "/tables",
        "versions": "/versions",
    }


def _cfg() -> dict:
    host = os.getenv("DATABRICKS_HOST", "").strip().rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://") :]
    return {
        "host": host,
        "http_path": os.getenv("DATABRICKS_HTTP_PATH", "").strip(),
        "token": os.getenv("DATABRICKS_TOKEN", "").strip(),
        "catalog": os.getenv("GEMS_CATALOG", "gems_catalog").strip(),
        "schema": _default_schema(),
        "allowed": _parse_allowed_tables(os.getenv("ALLOWED_TABLES", "")),
        "tables_conn": os.getenv("AZURE_TABLES_CONNECTION_STRING", "").strip(),
        "api_keys_table": os.getenv("AZURE_API_KEYS_TABLE", "gemsApiKeys").strip(),
        "api_key_pepper": os.getenv("API_KEY_PEPPER", "").strip(),
        "allowed_api_users": _parse_allowed_users(os.getenv("ALLOWED_API_USERS", "")),
    }


def _parse_allowed_tables(raw: str) -> frozenset[str]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return frozenset(parts)


def _default_schema() -> str:
    schema = os.getenv("GEMS_SCHEMA", GemsSchema.gold_v1.value).strip()
    if schema not in ALLOWED_SCHEMAS:
        return GemsSchema.gold_v1.value
    return schema


def _schema_value(schema: GemsSchema | str | None = None) -> str:
    return (schema or _cfg()["schema"]).value if isinstance(schema, GemsSchema) else str(schema or _cfg()["schema"])


def _is_missing_table_error(e: Exception) -> bool:
    message = str(e).lower()
    return any(
        marker in message
        for marker in (
            "table_or_view_not_found",
            "table or view not found",
            "table not found",
            "not found",
            "does not exist",
        )
    )


def _parse_allowed_users(raw: str) -> frozenset[str]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return frozenset(parts)


def _owner_is_authorized(owner: str) -> bool:
    c = _cfg()
    allowed_api_users = c["allowed_api_users"]
    if not allowed_api_users:
        return False
    normalized = (owner or "").strip().lower()
    if normalized in allowed_api_users:
        return True
    return False


def _bearer_email(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(401, "Missing bearer token")

    domain = os.getenv("AUTH0_DOMAIN", "").strip().rstrip("/")
    audience = os.getenv("AUTH0_AUDIENCE", "").strip() or None
    if not domain:
        # TODO: In production, set AUTH0_DOMAIN and validate JWT signature/issuer/audience.
        return os.getenv("LOCAL_DEV_AUTHZ_EMAIL", "")

    try:
        from jose import jwt

        jwks = requests.get(f"https://{domain}/.well-known/jwks.json", timeout=10).json()
        header = jwt.get_unverified_header(token)
        key = next((item for item in jwks["keys"] if item.get("kid") == header.get("kid")), None)
        if key is None:
            raise HTTPException(401, "Unknown token signing key")
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=audience,
            issuer=f"https://{domain}/",
            options={"verify_aud": bool(audience)},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(401, f"Invalid bearer token: {e!s}") from e

    return str(claims.get("email") or claims.get("upn") or claims.get("preferred_username") or "").lower()


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


def _describe_columns(table: str, schema: GemsSchema | str | None = None) -> list[dict]:
    """Return [{'name','type'}, ...] via DESCRIBE TABLE on an allowlisted fully-qualified table."""
    c = _cfg()
    schema_name = _schema_value(schema)
    fq = f"{c['catalog']}.{schema_name}.{table}"
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
        if _is_missing_table_error(e):
            return []
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


def _validate_since_column(table: str, col: str, schema: GemsSchema | str | None = None) -> dict:
    """Ensure `col` exists on `table` and return its column info (name, type)."""
    if not _IDENT_RE.match(col):
        raise HTTPException(400, "Invalid since_col")
    schema_cols = _describe_columns(table, schema)
    for c in schema_cols:
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
    return {"status": "ok" if ok else "degraded", "allowed_table_count": len(c["allowed"]), "schemas": list(ALLOWED_SCHEMAS)}


@app.get("/tables")
def list_tables(_: Annotated[str, Depends(get_api_key)]):
    """Tables this API key may export (allowlist)."""
    c = _cfg()
    return {"catalog": c["catalog"], "schemas": list(ALLOWED_SCHEMAS), "tables": sorted(c["allowed"])}


@app.get("/authz/me")
def authz_me(authorization: Annotated[str | None, Header()] = None):
    email = _bearer_email(authorization)
    return {"api_access": _owner_is_authorized(email), "email": email}


@app.get("/authz/allowed-users")
def authz_allowed_users(x_dashboard_authz_secret: Annotated[str | None, Header()] = None):
    expected = os.getenv("DASHBOARD_API_AUTHZ_SECRET", "").strip()
    if not expected or x_dashboard_authz_secret != expected:
        raise HTTPException(401, "Unauthorized")
    return {"allowed_users": sorted(_cfg()["allowed_api_users"])}


def _table_version(table: str, schema: GemsSchema | str | None = None) -> dict:
    t = _validate_table_name(table)
    c = _cfg()
    schema_name = _schema_value(schema)
    fq = f"{c['catalog']}.{schema_name}.{t}"
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
        if _is_missing_table_error(e):
            return {
                "table": t,
                "catalog": c["catalog"],
                "schema": schema_name,
                "version": None,
                "timestamp": None,
                "operation": None,
                "message": f"No data found for table '{t}' in schema '{schema_name}'",
            }
        raise HTTPException(502, f"Databricks table history lookup failed: {e!s}") from e

    if not rows:
        raise HTTPException(404, f"No Delta history found for table '{t}'")

    row = dict(zip(columns, rows[0], strict=False))
    return {
        "table": t,
        "catalog": c["catalog"],
        "schema": schema_name,
        "version": _json_safe(row.get("version")),
        "timestamp": _json_safe(row.get("timestamp")),
        "operation": _json_safe(row.get("operation")),
    }


@app.get("/version/{table}")
def get_version(table: str, _: Annotated[dict, Depends(get_api_key)], schema: GemsSchema = Query(GemsSchema.gold_v1)):
    """Return the latest Delta table version for one allowlisted gold table."""
    return _table_version(table, schema)


@app.get("/versions")
def get_versions(_: Annotated[dict, Depends(get_api_key)], schema: GemsSchema = Query(GemsSchema.gold_v1)):
    """Return latest Delta table versions for all allowlisted gold tables."""
    versions = [_table_version(table, schema) for table in sorted(_cfg()["allowed"])]
    return {"schema": schema.value, "tables": versions}


@app.get("/schema/{table}")
def get_schema(table: str, _: Annotated[str, Depends(get_api_key)], schema: GemsSchema = Query(GemsSchema.gold_v1)):
    """Return column name/type list for an allowlisted table."""
    t = _validate_table_name(table)
    columns = _describe_columns(t, schema)
    response = {"table": t, "schema": schema.value, "columns": columns}
    if not columns:
        response["message"] = f"No data found for table '{t}' in schema '{schema.value}'"
    return response


@app.get("/preview/{table}")
def preview(
    table: str,
    _: Annotated[str, Depends(get_api_key)],
    limit: int = Query(100, ge=1, le=1000),
    schema: GemsSchema = Query(GemsSchema.gold_v1),
):
    """Return up to `limit` rows as JSON for quick browsing."""
    t = _validate_table_name(table)
    c = _cfg()
    schema_name = schema.value
    fq = f"{c['catalog']}.{schema_name}.{t}"
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
        if _is_missing_table_error(e):
            return {
                "table": t,
                "schema": schema_name,
                "columns": [],
                "rows": [],
                "message": f"No data found for table '{t}' in schema '{schema_name}'",
            }
        raise HTTPException(502, f"Databricks query failed: {e!s}") from e

    data = [dict(zip(columns, [_json_safe(v) for v in r], strict=False)) for r in rows]
    return {"table": t, "schema": schema_name, "columns": columns, "rows": data}


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
    schema: GemsSchema = Query(GemsSchema.gold_v1),
):
    """
    Download allowlisted table as CSV. Latest snapshot from Databricks (warehouse sees current Delta state).
    If both `since_col` and `since_value` are provided, only rows strictly greater than `since_value` are returned.
    """
    t = _validate_table_name(table)
    c = _cfg()
    schema_name = schema.value
    fq = f"{c['catalog']}.{schema_name}.{t}"

    where = ""
    if since_col and since_value is not None:
        col_info = _validate_since_column(t, since_col, schema)
        literal = _format_since_literal(since_value, col_info["type"])
        where = f" WHERE {since_col} > {literal}"
    elif since_col or since_value:
        raise HTTPException(400, "Provide both since_col and since_value, or neither")

    sql = f"SELECT * FROM {fq}{where}"

    try:
        conn = _connect_db()
        cur = conn.cursor()
        cur.execute(sql)
    except Exception as e:
        if _is_missing_table_error(e):
            header = f"message\r\nNo data found for table '{t}' in schema '{schema_name}'\r\n"
            return StreamingResponse(
                iter([header]),
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{t}.csv"'},
            )
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
    limit: int | None = Field(default=None, ge=1)
    schema_: GemsSchema = Field(default=GemsSchema.gold_v1, alias="schema")


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
      - optional caller-provided row limit
    """
    c = _cfg()
    schema_name = req.schema_.value
    safe = _validate_select_sql(req.sql, c["allowed"], c["catalog"], schema_name)

    limit = int(req.limit) if req.limit is not None else None
    limit_sql = f" LIMIT {limit}" if limit is not None else ""
    wrapped_sql = f"SELECT * FROM ({safe}) AS __gems_q{limit_sql}"

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
        if _is_missing_table_error(e):
            return {
                "schema": schema_name,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "limit_applied": limit,
                "truncated": False,
                "message": f"No data found in schema '{schema_name}' for the requested query",
            }
        raise HTTPException(502, f"Databricks query failed: {e!s}") from e

    data = [dict(zip(columns, [_json_safe(v) for v in r], strict=False)) for r in rows]
    return {
        "schema": schema_name,
        "columns": columns,
        "rows": data,
        "row_count": len(data),
        "limit_applied": limit,
        "truncated": limit is not None and len(data) >= limit,
    }
