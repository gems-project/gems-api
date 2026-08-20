"""
GEMS read-only CSV API: API-key auth + allowlisted gold tables via Databricks SQL warehouse (PAT).

Phase 1 (default): X-API-Key alone still works. If an Auth0 Bearer token is also sent,
it must be valid and its email must match the API key owner.

Phase 2: set REQUIRE_AUTH0=true so every data call needs X-API-Key + Auth0 Bearer.
Swagger /docs Authorize asks for both schemes now (key + Auth0 login).
"""

import csv
import hashlib
import hmac
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.openapi.docs import get_swagger_ui_oauth2_redirect_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

# Local: API/.env. Azure: use Application settings (env vars); .env optional if present.
load_dotenv(Path(__file__).resolve().parent / ".env")

_AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "").strip().rstrip("/")
_AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "").strip()
_AUTH0_CLIENT_ID = (
    os.getenv("AUTH0_SWAGGER_CLIENT_ID", "").strip()
    or os.getenv("AUTH0_CLIENT_ID", "").strip()
)
_SWAGGER_OAUTH_REDIRECT = "/docs/oauth2-redirect"


def _swagger_ui_init_oauth() -> dict[str, Any]:
    """Pre-fill Auth0 so users only click Authorize (no ids/secrets to type)."""
    cfg: dict[str, Any] = {
        "usePkceWithAuthorizationCodeGrant": True,
        "scopes": "openid profile email",
        "appName": "GEMS",
    }
    if _AUTH0_CLIENT_ID:
        cfg["clientId"] = _AUTH0_CLIENT_ID
    # Never pre-fill a secret — public PKCE client; empty secret confuses users & Auth0.
    cfg["clientSecret"] = ""
    extra: dict[str, str] = {}
    if _AUTH0_AUDIENCE:
        extra["audience"] = _AUTH0_AUDIENCE
    # Fresh Auth0 login each Authorize (SSO alone made Logout→Authorize reuse a dead code).
    extra["prompt"] = "login"
    if extra:
        cfg["additionalQueryStringParams"] = extra
    return cfg


# Custom /docs below (hides OAuth plumbing). Keep redoc default.
app = FastAPI(
    title="GEMS Gold Export API",
    version="0.5.5",
    docs_url=None,
    redoc_url="/redoc",
)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

_IDENT_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
# Allow digits, T, Z, space, dash, colon, dot, plus (ISO-8601 timestamps or plain ints/decimals).
_SINCE_VALUE_RE = re.compile(r"^[0-9A-Za-z\-:.+ ]{1,64}$")
_API_KEY_PREFIX = "gems_live_"
_API_KEY_PARTITION = "api_key"
_JWKS_CACHE: dict[str, Any] = {"fetched_at": 0.0, "keys": None}
_JWKS_TTL_SECONDS = 3600

_PUBLIC_PATHS = {
    "/",
    "/health",
    "/auth/client-config",
    "/authz/me",
    "/authz/allowed-users",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
}


class GemsSchema(str, Enum):
    gold_v1 = "gold_v1"
    gold_v2 = "gold_v2"
    gold_v3 = "gold_v3"


ALLOWED_SCHEMAS = tuple(schema.value for schema in GemsSchema)


@app.get("/docs", include_in_schema=False)
def swagger_ui() -> HTMLResponse:
    """Minimal Authorize UI: hide OAuth plumbing; clear stale codes so re-login works."""
    init_oauth = json.dumps(_swagger_ui_init_oauth())
    openapi_url = app.openapi_url or "/openapi.json"
    redirect = _SWAGGER_OAUTH_REDIRECT
    # Full custom page: FastAPI's helper cannot register the auth-code clear plugin.
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{app.title} — docs</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"/>
  <style>
    .swagger-ui section.models {{ display: none !important; }}
    /* Hide OAuth fields users must never fill */
    .swagger-ui .auth-container .gems-hide-row {{ display: none !important; }}
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
  const GemsAuthFix = function() {{
    return {{
      statePlugins: {{
        auth: {{
          wrapActions: {{
            // Swagger bug: after Logout, Authorize reuses the old one-time auth code.
            authorizeOauth2: (ori) => (payload) => {{
              try {{
                if (payload && payload.auth) payload.auth.code = "";
              }} catch (e) {{}}
              return ori(payload);
            }},
            authPopup: (ori) => (url, oauth2Data) => {{
              try {{
                if (oauth2Data && oauth2Data.auth && oauth2Data.auth.code) {{
                  delete oauth2Data.auth.code;
                }}
              }} catch (e) {{}}
              return ori(url, oauth2Data);
            }},
          }},
        }},
      }},
    }};
  }};

  function simplifyAuthModal(root) {{
    const box = root || document;
    // Swagger splits this into two <p> tags inside .scope-def — replace the whole block.
    const newScopesHelp =
      "Scopes are used to grant an application different levels of access to data on behalf of the end user. API requires the following scopes. ";
    box.querySelectorAll(".auth-container").forEach((container) => {{
      container.querySelectorAll(".scope-def").forEach((el) => {{
        if (el.dataset.gemsScopesRewritten === "1") return;
        el.innerHTML = "";
        const p = document.createElement("p");
        p.textContent = newScopesHelp;
        el.appendChild(p);
        el.dataset.gemsScopesRewritten = "1";
      }});
      container.querySelectorAll("label").forEach((label) => {{
        const t = (label.textContent || "").trim().toLowerCase().replace(":", "");
        if (t === "client_id" || t === "client_secret") {{
          const row = label.closest(".wrapper") || label.parentElement;
          if (row) row.classList.add("gems-hide-row");
        }}
      }});
      container.querySelectorAll("input").forEach((input) => {{
        const n = (input.getAttribute("name") || input.getAttribute("data-name") || "").toLowerCase();
        if (n.includes("client_secret")) {{
          input.value = "";
          const row = input.closest(".wrapper") || input.parentElement;
          if (row) row.classList.add("gems-hide-row");
        }}
        if (n.includes("client_id")) {{
          const row = input.closest(".wrapper") || input.parentElement;
          if (row) row.classList.add("gems-hide-row");
        }}
      }});
      // Hide Auth0 plumbing lines Swagger prints above the buttons.
      container.querySelectorAll("p").forEach((el) => {{
        const t = (el.textContent || "").trim();
        if (
          t.startsWith("Authorization URL:") ||
          t.startsWith("Token URL:") ||
          t.startsWith("Flow:") ||
          t.startsWith("Application:")
        ) {{
          el.classList.add("gems-hide-row");
        }}
      }});
      container.querySelectorAll(".scopes").forEach((el) => el.classList.add("gems-hide-row"));
    }});
  }}

  const ui = SwaggerUIBundle({{
    url: {json.dumps(openapi_url)},
    dom_id: "#swagger-ui",
    layout: "BaseLayout",
    deepLinking: true,
    persistAuthorization: true,
    tryItOutEnabled: true,
    docExpansion: "list",
    defaultModelsExpandDepth: -1,
    oauth2RedirectUrl: window.location.origin + {json.dumps(redirect)},
    presets: [SwaggerUIBundle.presets.apis],
    plugins: [GemsAuthFix],
    onComplete: function() {{
      simplifyAuthModal(document);
      const obs = new MutationObserver(() => simplifyAuthModal(document));
      obs.observe(document.getElementById("swagger-ui"), {{ childList: true, subtree: true }});
    }},
  }});
  ui.initOAuth({init_oauth});
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html)


@app.get(_SWAGGER_OAUTH_REDIRECT, include_in_schema=False)
def swagger_oauth2_redirect():
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/")
def root():
    return {
        "name": "GEMS Gold Export API",
        "message": "Use /docs → Authorize: Log in (click Authorize only), then paste your API key.",
        "docs": "/docs",
        "health": "/health",
        "auth_client_config": "/auth/client-config",
        "tables": "/tables",
        "versions": "/versions",
    }


@app.get("/auth/client-config")
def auth_client_config():
    """Public Auth0 settings for desktop scripts (client id is not a secret)."""
    if not _AUTH0_DOMAIN or not _AUTH0_CLIENT_ID:
        raise HTTPException(
            503,
            "Auth0 client config is not set on GEMS-API "
            "(need AUTH0_DOMAIN and AUTH0_SWAGGER_CLIENT_ID).",
        )
    return {
        "domain": _AUTH0_DOMAIN,
        "client_id": _AUTH0_CLIENT_ID,
        "audience": _AUTH0_AUDIENCE or None,
        "callback": "http://127.0.0.1:8765/callback",
        "scopes": "openid profile email",
    }


def _truthy(raw: str | bool | None) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"true", "1", "yes"}


def _require_auth0() -> bool:
    return _truthy(os.getenv("REQUIRE_AUTH0", "false"))


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


def _get_jwks() -> dict[str, Any]:
    domain = _AUTH0_DOMAIN or os.getenv("AUTH0_DOMAIN", "").strip().rstrip("/")
    if not domain:
        raise HTTPException(500, "Server misconfigured: AUTH0_DOMAIN not set")
    now = time.time()
    if _JWKS_CACHE["keys"] is not None and now - float(_JWKS_CACHE["fetched_at"]) < _JWKS_TTL_SECONDS:
        return _JWKS_CACHE["keys"]
    response = requests.get(f"https://{domain}/.well-known/jwks.json", timeout=10)
    response.raise_for_status()
    payload = response.json()
    _JWKS_CACHE["keys"] = payload
    _JWKS_CACHE["fetched_at"] = now
    return payload


def _bearer_email(authorization: str | None, *, verify_audience: bool = True) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(401, "Missing bearer token")

    domain = (_AUTH0_DOMAIN or os.getenv("AUTH0_DOMAIN", "")).strip().rstrip("/")
    audience = (_AUTH0_AUDIENCE or os.getenv("AUTH0_AUDIENCE", "")).strip() or None
    if not domain:
        local = os.getenv("LOCAL_DEV_AUTHZ_EMAIL", "").strip().lower()
        if local:
            return local
        raise HTTPException(
            401,
            "AUTH0_DOMAIN is not set on GEMS-API. Add it in Azure App Settings and restart.",
        )

    try:
        from jose import jwt

        jwks = _get_jwks()
        header = jwt.get_unverified_header(token)
        key = next((item for item in jwks.get("keys", []) if item.get("kid") == header.get("kid")), None)
        if key is None:
            _JWKS_CACHE["fetched_at"] = 0.0
            jwks = _get_jwks()
            key = next((item for item in jwks.get("keys", []) if item.get("kid") == header.get("kid")), None)
        if key is None:
            raise HTTPException(401, "Unknown token signing key")

        # Dashboard Easy Auth tokens often have a different audience than AUTH0_AUDIENCE
        # (API Identifier). /authz/me must accept those; data routes can still require audience.
        decode_kwargs: dict[str, Any] = {
            "algorithms": ["RS256"],
            "issuer": f"https://{domain}/",
            "options": {"verify_aud": bool(audience) and verify_audience},
        }
        if audience and verify_audience:
            decode_kwargs["audience"] = audience
        claims = jwt.decode(token, key, **decode_kwargs)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(401, f"Invalid bearer token: {e!s}") from e

    email = str(
        claims.get("email")
        or claims.get("https://gems.bovi-analytics.org/email")
        or claims.get("https://gems.bovi-analytics.com/email")
        or claims.get("upn")
        or claims.get("preferred_username")
        or ""
    ).strip().lower()
    if not email:
        raise HTTPException(
            401,
            "Auth0 access token has no email claim. In Auth0: Actions → add a Login Action "
            "that sets https://gems.bovi-analytics.org/email, add it to the Login flow, "
            "Apply, then Logout and Authorize again in Swagger (or re-run the script).",
        )
    if "email_verified" in claims and not _truthy(claims.get("email_verified")):
        raise HTTPException(403, "Auth0 email is not verified")
    return email


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


def get_api_key(
    x_api_key: Annotated[str | None, Depends(api_key_header)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """Validate API key; optionally/require Auth0 Bearer and match emails.

    Phase 1 (REQUIRE_AUTH0=false): key alone is enough. If Bearer is sent, it must
    match the key owner email.
    Phase 2 (REQUIRE_AUTH0=true): key + valid Auth0 Bearer with matching email.
    """
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

    owner = str(entity.get("owner", "")).strip().lower()
    if not _owner_is_authorized(owner):
        raise HTTPException(403, "API key owner is no longer authorized")

    has_bearer = bool(authorization and authorization.lower().startswith("bearer "))
    auth0_verified = False
    if has_bearer:
        try:
            token_email = _bearer_email(authorization)
        except HTTPException:
            # Phase 1: Swagger often sends a junk/empty Auth0 value; ignore and use key only.
            if _require_auth0():
                raise
            token_email = ""
        if token_email:
            if token_email != owner:
                raise HTTPException(
                    403,
                    f"Auth0 email ({token_email}) does not match API key owner ({owner})",
                )
            auth0_verified = True
        elif _require_auth0():
            raise HTTPException(
                401,
                "Auth0 Bearer token required with an email claim matching the API key owner.",
            )
    elif _require_auth0():
        raise HTTPException(
            401,
            "Auth0 Bearer token required. Log in via /docs Authorize (Auth0) or the "
            "updated client script, and send Authorization: Bearer <token> with X-API-Key.",
        )

    try:
        entity["lastUsedAt"] = _utc_now()
        client.upsert_entity(entity)
    except Exception:
        pass

    return {
        "owner": owner,
        "name": str(entity.get("name", "")),
        "key_hash": key_hash,
        "auth0_verified": auth0_verified,
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
    # Dashboard Easy Auth tokens are not issued for AUTH0_AUDIENCE; skip aud check here.
    email = _bearer_email(authorization, verify_audience=False)
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


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=(
            "GEMS gold export API.\n\n"
            "**Authorize** (lock icon):\n"
            "1. **Log in** — click **Authorize** only (do not type client_id or client_secret)\n"
            "2. **API key** — paste your `gems_live_…` key"
        ),
        routes=app.routes,
    )
    domain = (_AUTH0_DOMAIN or os.getenv("AUTH0_DOMAIN", "")).strip().rstrip("/")
    components = schema.setdefault("components", {})
    security_schemes = components.setdefault("securitySchemes", {})
    # Friendly names only — drop any auto-generated HTTPBearer / OAuth schemes.
    for dead in list(security_schemes):
        security_schemes.pop(dead, None)

    # Scheme key order = display order in Authorize dialog.
    if domain:
        security_schemes["Log in"] = {
            "type": "oauth2",
            "description": (
                "Click **Authorize** below — nothing to type. "
                "Use the same email as the GEMS dashboard."
            ),
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": f"https://{domain}/authorize",
                    "tokenUrl": f"https://{domain}/oauth/token",
                    "scopes": {
                        "openid": " ",
                        "profile": " ",
                        "email": " ",
                    },
                }
            },
        }
    security_schemes["API key"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Your gems_live_… key from the dashboard.",
    }

    if domain:
        dual_security = [{"Log in": ["openid", "profile", "email"], "API key": []}]
    else:
        dual_security = [{"API key": []}]

    for path, path_item in schema.get("paths", {}).items():
        if path in _PUBLIC_PATHS or path.startswith("/docs") or path.startswith("/redoc"):
            continue
        for method, operation in list(path_item.items()):
            if method in {"get", "post", "put", "patch", "delete"} and isinstance(operation, dict):
                operation["security"] = dual_security

    schema["security"] = dual_security
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi
