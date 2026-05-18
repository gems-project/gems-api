from __future__ import annotations

import re


class SQLValidationError(ValueError):
    pass


FQ_NAME_ERROR = (
    "Use a fully-qualified name with backticks like "
    "`gems_catalog`.`gold_v1`.`goldbodyweight`. Do not use schema prefixes "
    "like `gold`, `silver`, `bronze`, or `gems_schema`. Re-check list_tables output and use "
    "the full_name field directly."
)


_FORBIDDEN_SQL = re.compile(
    r"\b(drop|delete|update|insert|merge|alter|create|replace|truncate|grant|revoke|copy|call|use|set|load|vacuum|optimize|refresh)\b",
    re.IGNORECASE,
)
_AGGREGATE_SQL = re.compile(r"\b(count|avg|sum|min|max)\s*\(", re.IGNORECASE)
_LIMIT_SQL = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)
_PII_WORDS = re.compile(
    r"\b(email|e-mail|phone|address|name|firstname|lastname|fullname|contract|workbook|path)\b",
    re.IGNORECASE,
)


def validate_aggregate_query(
    sql: str,
    allowed: frozenset[str],
    catalog: str,
    schema: str,
    redacted_columns: frozenset[str],
) -> str:
    statement = (sql or "").strip()
    while statement.endswith(";"):
        statement = statement[:-1].strip()
    if not statement:
        raise SQLValidationError("SQL is empty.")
    if ";" in statement:
        raise SQLValidationError("Only a single SQL statement is allowed.")
    if not re.match(r"^\s*(select|with)\b", statement, re.IGNORECASE):
        raise SQLValidationError("Only SELECT or WITH statements are allowed.")
    if _FORBIDDEN_SQL.search(statement):
        raise SQLValidationError("Forbidden SQL keyword detected.")

    try:
        import sqlglot
        from sqlglot import exp as sqlglot_exp

        tree = sqlglot.parse_one(statement, read="databricks")
        if tree is None:
            raise SQLValidationError("SQL parse returned no tree.")

        cte_names: set[str] = set()
        for cte in tree.find_all(sqlglot_exp.CTE):
            if cte.alias_or_name:
                cte_names.add(cte.alias_or_name)

        for table in tree.find_all(sqlglot_exp.Table):
            name = table.name
            if name in cte_names:
                continue
            db = table.args.get("db")
            cat = table.args.get("catalog")
            db_name = db.name if db is not None else None
            cat_name = cat.name if cat is not None else None
            if cat_name is not None and cat_name != catalog:
                raise SQLValidationError(FQ_NAME_ERROR)
            if db_name is not None and db_name != schema:
                raise SQLValidationError(FQ_NAME_ERROR)
            if name not in allowed:
                raise SQLValidationError(FQ_NAME_ERROR)
    except ModuleNotFoundError:
        lowered_for_tables = statement.lower()
        if not any(re.search(rf"\b{re.escape(table.lower())}\b", lowered_for_tables) for table in allowed):
            raise SQLValidationError(FQ_NAME_ERROR)
        forbidden_fq = re.findall(r"\b([a-zA-Z][\w]*)\.([a-zA-Z][\w]*)\.([a-zA-Z][\w]*)\b", statement)
        for cat_name, db_name, table_name in forbidden_fq:
            if cat_name != catalog or db_name != schema or table_name not in allowed:
                raise SQLValidationError(FQ_NAME_ERROR)
    except SQLValidationError:
        raise
    except Exception as exc:
        raise SQLValidationError(f"SQL parse failed: {exc}") from exc

    lowered = statement.lower()
    has_aggregation = bool(_AGGREGATE_SQL.search(statement))
    has_group_by = "group by" in lowered
    has_distinct = bool(re.search(r"\bselect\s+distinct\b", lowered))
    limit_match = _LIMIT_SQL.search(statement)
    limit_value = int(limit_match.group(1)) if limit_match else None
    has_small_limit = limit_value is not None and limit_value <= 50
    has_star = bool(re.search(r"\bselect\s+\*", lowered))
    mentions_pii = bool(_PII_WORDS.search(statement)) or any(
        re.search(rf"\b{re.escape(col)}\b", statement, re.IGNORECASE)
        for col in redacted_columns
    )

    if limit_value is not None and limit_value > 50 and not (
        has_aggregation or has_group_by or has_distinct
    ):
        raise SQLValidationError("Non-aggregate preview queries must use LIMIT <= 50.")

    if has_aggregation or has_group_by or has_distinct:
        return statement

    if has_small_limit and not has_star and not mentions_pii:
        return statement

    raise SQLValidationError(
        "Chat queries must be aggregated (COUNT/AVG/SUM/MIN/MAX/GROUP BY/DISTINCT) "
        "or a LIMIT <= 50 preview without PII/redacted columns."
    )