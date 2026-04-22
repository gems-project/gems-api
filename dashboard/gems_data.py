"""Standalone data layer for the GEMS dashboard.

The dashboard talks directly to the Databricks SQL warehouse using a PAT,
with no dependency on any other service.

Responsibilities:
- Allowlist-based table validation (ALLOWED_TABLES env var).
- Column redaction: internal/workflow columns are hidden from every view.
- Display-name helpers that strip the "gold" prefix so users see friendlier
  names (goldbodyweight → bodyweight) while the code still queries the real
  Delta table.
- Efficient bulk loads (Arrow fetch when available) for the Modeling page.
- sqlglot-based SELECT validation for the Chat page.

There is no row cap on reads; callers may pass an explicit `limit`, but by
default the full table is returned.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import os
import re
from decimal import Decimal

import pandas as pd
import streamlit as st

_IDENT_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")
_SINCE_VALUE_RE = re.compile(r"^[0-9A-Za-z\-:.+ ]{1,64}$")
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|merge|grant|revoke|copy|call|use|set|load|msck|analyze|optimize|vacuum|refresh)\b",
    re.IGNORECASE,
)
_WATERMARK_KEYWORDS = ("timestamp", "date", "bigint", "long", "int")


# Internal/workflow columns that users should never see (regardless of table).
REDACTED_COLUMNS: frozenset[str] = frozenset(
    {
        "contractName",
        "workbookFile",
        "workbookPath",
        "gateRunId",
        "ingestRunId",
        "excelSourceRow",
        "Expression",
    }
)


class DataError(Exception):
    """User-visible error raised by the data layer (bad input, disallowed table, ...)."""


def _cfg() -> dict:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://") :]
    raw_allowed = os.environ.get("ALLOWED_TABLES", "")
    return {
        "host": host,
        "http_path": os.environ.get("DATABRICKS_HTTP_PATH", "").strip(),
        "token": os.environ.get("DATABRICKS_TOKEN", "").strip(),
        "catalog": os.environ.get("GEMS_CATALOG", "gems_catalog").strip(),
        "schema": os.environ.get("GEMS_SCHEMA", "gold_v1").strip(),
        "allowed": frozenset(
            t.strip() for t in raw_allowed.split(",") if t.strip()
        ),
    }


def _connect():
    from databricks import sql as dsql

    c = _cfg()
    if not (c["host"] and c["http_path"] and c["token"]):
        raise DataError(
            "Databricks connection env vars missing (DATABRICKS_HOST / "
            "DATABRICKS_HTTP_PATH / DATABRICKS_TOKEN)."
        )
    return dsql.connect(
        server_hostname=c["host"],
        http_path=c["http_path"],
        access_token=c["token"],
    )


def _json_safe(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    return v


def _validate_table_name(table: str, allowed: frozenset[str]) -> str:
    t = (table or "").strip()
    if not t or not _IDENT_RE.match(t):
        raise DataError(f"Invalid table name: {table!r}")
    if not allowed:
        raise DataError("ALLOWED_TABLES is empty in the environment.")
    if t not in allowed:
        raise DataError(f"Table '{t}' is not in the allowlist.")
    return t


def _describe_columns(cursor, catalog: str, schema: str, table: str) -> list[dict]:
    cursor.execute(f"DESCRIBE TABLE `{catalog}`.`{schema}`.`{table}`")
    cols: list[dict] = []
    for row in cursor.fetchall():
        name = str(row[0]) if row[0] is not None else ""
        if not name or name.startswith("#") or name.strip() == "":
            break
        dtype = str(row[1]) if row[1] is not None else ""
        cols.append({"name": name, "type": dtype})
    return cols


def _filter_columns(cols: list[dict]) -> list[dict]:
    """Drop the internal/workflow columns we never want users to see."""
    return [c for c in cols if c.get("name") not in REDACTED_COLUMNS]


def _filter_df(df: pd.DataFrame) -> pd.DataFrame:
    """Drop redacted columns from a loaded DataFrame."""
    drop = [c for c in df.columns if c in REDACTED_COLUMNS]
    return df.drop(columns=drop) if drop else df


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Make numeric columns visible to ``select_dtypes(include='number')``.

    Databricks DECIMAL columns come through in a few shapes depending on
    the connector / pyarrow / pandas versions:
      1. Object dtype with Python ``Decimal`` values.
      2. Object dtype with *string* representations of numbers
         (``"472.72..."``) — what we actually see from the current warehouse.
      3. ``pd.ArrowDtype(decimal128(...))`` — extension dtype that
         ``is_numeric_dtype`` does not treat as numeric in older pandas.

    We convert cases 1–3 to plain numpy floats/ints. String columns that
    look like zero-padded identifiers (``"056"``) are left untouched so we
    don't silently drop leading zeros.
    """
    for col in df.columns:
        s = df[col]
        dtype = s.dtype

        if isinstance(dtype, pd.ArrowDtype):
            try:
                df[col] = pd.to_numeric(s)
            except (ValueError, TypeError):
                pass
            continue

        try:
            if dtype.kind in ("i", "u", "f", "b"):
                continue
        except AttributeError:
            pass

        if dtype != object:
            continue

        sample = s.dropna().head(100)
        if sample.empty:
            continue

        # Case 1: all Decimal → definitely numeric.
        if all(isinstance(v, Decimal) for v in sample):
            try:
                df[col] = pd.to_numeric(s)
            except (ValueError, TypeError):
                pass
            continue

        # Case 2: all strings — convert only if they don't look like
        # zero-padded IDs (length > 1 and starts with "0", e.g. "056").
        if all(isinstance(v, str) for v in sample):
            zero_padded = any(
                len(v) > 1 and v.startswith("0") and not v.startswith("0.")
                for v in sample
            )
            if zero_padded:
                continue
            try:
                df[col] = pd.to_numeric(s)
            except (ValueError, TypeError):
                pass
            continue

        # Mixed / other object content: best-effort.
        try:
            df[col] = pd.to_numeric(s)
        except (ValueError, TypeError):
            pass

    return df


def _validate_since_column(schema_cols: list[dict], col: str) -> dict:
    if not _IDENT_RE.match(col or ""):
        raise DataError(f"Invalid since_col: {col!r}")
    for c in schema_cols:
        if c["name"] == col:
            return c
    raise DataError(f"Column '{col}' not found on the table.")


def _format_since_literal(value: str, col_type: str) -> str:
    if not _SINCE_VALUE_RE.match(value or ""):
        raise DataError("Invalid since_value format.")
    t = (col_type or "").lower()
    if any(k in t for k in ("int", "long", "bigint", "short", "tinyint")):
        if not re.match(r"^-?\d+$", value):
            raise DataError("since_value must be an integer for this column.")
        return value
    if any(k in t for k in ("decimal", "double", "float", "numeric")):
        if not re.match(r"^-?\d+(\.\d+)?$", value):
            raise DataError("since_value must be numeric for this column.")
        return value
    return f"'{value}'"


def _validate_select_sql(
    sql: str, allowed: frozenset[str], catalog: str, schema: str
) -> str:
    s = (sql or "").strip()
    while s.endswith(";"):
        s = s[:-1].strip()
    if not s:
        raise DataError("SQL is empty.")
    if ";" in s:
        raise DataError("Only a single SQL statement is allowed.")
    if not re.match(r"^\s*(select|with)\b", s, re.IGNORECASE):
        raise DataError("Only SELECT or WITH statements are allowed.")
    if _FORBIDDEN_SQL.search(s):
        raise DataError("Forbidden SQL keyword detected.")

    try:
        import sqlglot
        from sqlglot import exp as sqlglot_exp
    except Exception as e:
        raise DataError(f"SQL parser unavailable: {e}") from e

    try:
        tree = sqlglot.parse_one(s, read="databricks")
    except Exception as e:
        raise DataError(f"SQL parse failed: {e}") from e

    if tree is None:
        raise DataError("SQL parse returned no tree.")

    cte_names: set[str] = set()
    for cte in tree.find_all(sqlglot_exp.CTE):
        if cte.alias_or_name:
            cte_names.add(cte.alias_or_name)

    for t in tree.find_all(sqlglot_exp.Table):
        name = t.name
        db = t.args.get("db")
        cat = t.args.get("catalog")
        db_name = db.name if db is not None else None
        cat_name = cat.name if cat is not None else None

        if name in cte_names and db_name is None and cat_name is None:
            continue
        if cat_name is not None and cat_name != catalog:
            raise DataError(f"Catalog '{cat_name}' is not allowed.")
        if db_name is not None and db_name != schema:
            raise DataError(f"Schema '{db_name}' is not allowed.")
        if name not in allowed:
            raise DataError(f"Table '{name}' is not allowed.")

    return s


def watermark_candidates(cols: list[dict]) -> list[dict]:
    """Subset of columns usable as an incremental-download watermark."""
    out: list[dict] = []
    for c in cols:
        t = str(c.get("type", "")).lower()
        if any(k in t for k in _WATERMARK_KEYWORDS):
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Display-name helpers. Users see "bodyweight" instead of "goldbodyweight".
# ---------------------------------------------------------------------------


def display_name(internal: str) -> str:
    """Strip the 'gold' prefix for UI presentation."""
    if not internal:
        return internal
    return internal[4:] if internal.lower().startswith("gold") else internal


def internal_name(display: str, allowed: frozenset[str]) -> str:
    """Inverse of display_name, guarded by the allowlist."""
    if display in allowed:
        return display
    candidate = f"gold{display}"
    if candidate in allowed:
        return candidate
    raise DataError(f"Unknown table: {display}")


# ---------------------------------------------------------------------------
# GemsData — user-facing class used by pages. Read methods are cached.
# ---------------------------------------------------------------------------


class GemsData:
    """Standalone Databricks data source for the dashboard."""

    def __init__(self) -> None:
        self.cfg = _cfg()

    def health(self) -> dict:
        c = self.cfg
        ok = bool(c["host"] and c["http_path"] and c["token"] and c["allowed"])
        return {
            "status": "ok" if ok else "degraded",
            "allowed_table_count": len(c["allowed"]),
            "catalog": c["catalog"],
            "schema": c["schema"],
        }

    # ---- Metadata --------------------------------------------------------

    @st.cache_data(ttl=300, show_spinner=False)
    def list_tables(_self) -> list[str]:
        return sorted(_self.cfg["allowed"])

    def list_tables_display(self) -> list[tuple[str, str]]:
        """Return [(display_name, internal_name), ...] sorted by display name."""
        pairs = [(display_name(t), t) for t in self.list_tables()]
        return sorted(pairs, key=lambda p: p[0])

    def resolve_table(self, display_or_internal: str) -> str:
        return internal_name(display_or_internal, self.cfg["allowed"])

    @st.cache_data(ttl=300, show_spinner=False)
    def get_schema(_self, table: str) -> list[dict]:
        c = _self.cfg
        t = _validate_table_name(table, c["allowed"])
        with _connect() as conn, conn.cursor() as cur:
            cols = _describe_columns(cur, c["catalog"], c["schema"], t)
        return _filter_columns(cols)

    # ---- Row reads -------------------------------------------------------

    @st.cache_data(ttl=300, show_spinner=False)
    def preview(_self, table: str, limit: int = 500) -> dict:
        c = _self.cfg
        t = _validate_table_name(table, c["allowed"])
        fq = f"`{c['catalog']}`.`{c['schema']}`.`{t}`"
        sql = f"SELECT * FROM {fq} LIMIT {int(limit)}"
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            columns = [col[0] for col in cur.description] if cur.description else []
        keep_idx = [i for i, name in enumerate(columns) if name not in REDACTED_COLUMNS]
        kept_cols = [columns[i] for i in keep_idx]
        data = [
            {kept_cols[j]: _json_safe(row[keep_idx[j]]) for j in range(len(keep_idx))}
            for row in rows
        ]
        return {"table": t, "columns": kept_cols, "rows": data}

    def load_dataframe(
        self,
        table: str,
        columns: list[str] | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Load a Delta table into a pandas DataFrame.

        Uses Arrow fetch when available (MUCH faster for large tables),
        falls back to row-by-row fetchall otherwise. Redacted columns are
        stripped.
        """
        c = self.cfg
        t = _validate_table_name(table, c["allowed"])
        fq = f"`{c['catalog']}`.`{c['schema']}`.`{t}`"

        if columns:
            cleaned: list[str] = []
            for col in columns:
                if not _IDENT_RE.match(col):
                    raise DataError(f"Invalid column name: {col!r}")
                if col in REDACTED_COLUMNS:
                    continue
                cleaned.append(col)
            if not cleaned:
                raise DataError("No valid columns requested.")
            col_sql = ", ".join(f"`{c_}`" for c_ in cleaned)
        else:
            col_sql = "*"

        lim_sql = f" LIMIT {int(limit)}" if limit else ""
        sql = f"SELECT {col_sql} FROM {fq}{lim_sql}"

        with _connect() as conn, conn.cursor() as cur:
            cur.execute(sql)
            try:
                arrow_table = cur.fetchall_arrow()
                df = arrow_table.to_pandas()
            except Exception:
                rows = cur.fetchall()
                col_names = (
                    [col[0] for col in cur.description] if cur.description else []
                )
                df = pd.DataFrame(rows, columns=col_names)

        df = _filter_df(df)
        df = _coerce_numeric(df)
        return df

    def export_csv(
        self,
        table: str,
        since_col: str | None = None,
        since_value: str | None = None,
    ) -> bytes:
        """Return the full CSV bytes. No row cap — user can download every row."""
        c = self.cfg
        t = _validate_table_name(table, c["allowed"])
        fq = f"`{c['catalog']}`.`{c['schema']}`.`{t}`"

        where = ""
        if since_col and since_value not in (None, ""):
            with _connect() as conn, conn.cursor() as cur:
                schema_cols = _describe_columns(cur, c["catalog"], c["schema"], t)
            col_info = _validate_since_column(schema_cols, since_col)
            literal = _format_since_literal(str(since_value), col_info["type"])
            where = f" WHERE `{since_col}` > {literal}"
        elif since_col or since_value:
            raise DataError("Provide both since_col and since_value, or neither.")

        sql = f"SELECT * FROM {fq}{where}"

        buf = io.StringIO()
        w = csv.writer(buf)
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(sql)
            columns = [col[0] for col in cur.description] if cur.description else []
            keep_idx = [
                i for i, name in enumerate(columns) if name not in REDACTED_COLUMNS
            ]
            w.writerow([columns[i] for i in keep_idx])
            while True:
                rows = cur.fetchmany(10000)
                if not rows:
                    break
                for row in rows:
                    w.writerow([row[i] for i in keep_idx])
        return buf.getvalue().encode("utf-8")

    def run_sql(self, sql: str, limit: int = 10000) -> dict:
        """Ad-hoc SELECT used by the Chat agent. Redacted columns removed."""
        c = self.cfg
        try:
            safe = _validate_select_sql(sql, c["allowed"], c["catalog"], c["schema"])
            lim = max(1, int(limit or 10000))
            wrapped = f"SELECT * FROM ({safe}) AS __gems_q LIMIT {lim}"
            with _connect() as conn, conn.cursor() as cur:
                cur.execute(wrapped)
                rows = cur.fetchall()
                columns = (
                    [col[0] for col in cur.description] if cur.description else []
                )
        except DataError as e:
            return {"error": True, "message": str(e)}
        except Exception as e:
            return {"error": True, "message": f"Query failed: {e}"}

        keep_idx = [i for i, name in enumerate(columns) if name not in REDACTED_COLUMNS]
        kept_cols = [columns[i] for i in keep_idx]
        data = [
            {kept_cols[j]: _json_safe(r[keep_idx[j]]) for j in range(len(keep_idx))}
            for r in rows
        ]
        return {
            "columns": kept_cols,
            "rows": data,
            "row_count": len(data),
            "limit_applied": lim,
            "truncated": len(data) >= lim,
        }
