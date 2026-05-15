"""Tool-calling chat agent with aggregate-only data guardrails."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from gems_data import GemsData, display_name
from llm_client import get_llm_client, get_llm_model

_DATA_DICTIONARY_PATH = os.path.join(
    os.path.dirname(__file__), "resources", "data_dictionary.json"
)


def _load_data_dictionary() -> dict:
    try:
        with open(_DATA_DICTIONARY_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"tables": []}


def _dictionary_prompt() -> str:
    dictionary = _load_data_dictionary()
    compact_tables = []
    for table in dictionary.get("tables", []):
        compact_tables.append(
            {
                "name": table.get("name"),
                "description": table.get("description", ""),
                "columns": [
                    {
                        "name": col.get("name"),
                        "dtype": col.get("dtype"),
                        "description": col.get("description", ""),
                        "unit": col.get("unit", ""),
                    }
                    for col in table.get("columns", [])
                ],
            }
        )
    return json.dumps({"tables": compact_tables}, default=str)[:60_000]


_SYSTEM_PROMPT = """You are a data analyst assistant for the GEMS project
(animal science / GreenFeed research data at Cornell).

ABSOLUTE PRIVACY RULE:
- Never request, expose, summarize, or reproduce row-level data.
- You may only use schema, column descriptions, table summaries, and aggregated
  or computed query results.
- If a question requires individual row-level records, refuse and suggest an
  aggregate alternative.

Use tools to answer questions:
1. list_tables() to see available tables.
2. describe_table(name) before SQL so you know real columns, dtypes, sample size,
   and null percentages.
3. run_aggregate_query(sql) only for aggregate/statistical summaries. The server
   rejects unsafe SQL and row dumps.
4. plot(spec) only after an aggregate result exists.

SQL rules:
- Use SELECT/WITH only.
- Prefer COUNT, AVG, SUM, MIN, MAX, GROUP BY, and DISTINCT.
- Use fully-qualified names when writing SQL.
- Do not use SELECT *.
- Do not ask for individual animal/person/source-file records.
- Do not fabricate values that did not appear in tool results.

Data dictionary:
"""


TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "List available GEMS tables with business descriptions.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "Return columns, dtypes, descriptions, sample size, and null percentages.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Raw table name."}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_aggregate_query",
            "description": (
                "Run a validated aggregate SELECT. Must aggregate/group/distinct, "
                "or be a LIMIT <= 50 preview without PII/redacted columns."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "Single aggregate SELECT/WITH query."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["sql"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plot",
            "description": "Create a chart spec from the most recent aggregate result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {"type": "string", "enum": ["bar", "line", "scatter"]},
                    "x": {"type": "string"},
                    "y": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["chart_type", "x", "y"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: Any


def _dictionary_table(name: str) -> dict | None:
    dictionary = _load_data_dictionary()
    for table in dictionary.get("tables", []):
        if table.get("name") == name:
            return table
    return None


def _describe_table(data: GemsData, name: str) -> dict:
    schema = data.get_schema(name)
    info = _dictionary_table(name) or {}
    sample_size = data.run_aggregate_query(
        f"SELECT COUNT(*) AS row_count FROM `{data.cfg['catalog']}`.`{data.cfg['schema']}`.`{name}`"
    )
    columns = []
    null_parts = [
        f"AVG(CASE WHEN `{col['name']}` IS NULL THEN 1 ELSE 0 END) AS `{col['name']}__null_pct`"
        for col in schema[:80]
    ]
    null_result = {}
    if null_parts:
        null_query = (
            "SELECT "
            + ", ".join(null_parts)
            + f" FROM `{data.cfg['catalog']}`.`{data.cfg['schema']}`.`{name}`"
        )
        null_result = data.run_aggregate_query(null_query)
    null_row = (null_result.get("rows") or [{}])[0] if isinstance(null_result, dict) else {}
    dict_cols = {col.get("name"): col for col in info.get("columns", [])}
    for col in schema:
        meta = dict_cols.get(col["name"], {})
        null_pct = null_row.get(f"{col['name']}__null_pct")
        columns.append(
            {
                "name": col["name"],
                "dtype": col["type"],
                "description": meta.get("description", ""),
                "unit": meta.get("unit", ""),
                "null_pct": null_pct,
            }
        )
    row_count = None
    if isinstance(sample_size, dict) and sample_size.get("rows"):
        row_count = sample_size["rows"][0].get("row_count")
    return {
        "name": name,
        "description": info.get("description", ""),
        "sample_size": row_count,
        "columns": columns,
    }


def _execute_tool(data: GemsData, name: str, args: dict, state: dict) -> Any:
    try:
        if name == "list_tables":
            dictionary = _load_data_dictionary()
            descriptions = {
                table.get("name"): table.get("description", "")
                for table in dictionary.get("tables", [])
            }
            return [
                {"name": table, "label": display_name(table), "description": descriptions.get(table, "")}
                for table in data.list_tables()
            ]
        if name == "describe_table":
            table = args.get("name", "")
            if not table:
                return {"error": True, "message": "Missing 'name' argument"}
            return _describe_table(data, table)
        if name == "run_aggregate_query":
            sql = args.get("sql", "")
            limit = int(args.get("limit", 50) or 50)
            result = data.run_aggregate_query(sql, limit=limit)
            if not result.get("error"):
                state["last_result"] = result
            return result
        if name == "plot":
            spec = {
                "chart_type": args.get("chart_type", "bar"),
                "x": args.get("x", ""),
                "y": args.get("y", ""),
                "title": args.get("title", ""),
                "source": "last_aggregate_result",
            }
            state["last_plot"] = spec
            return {"plot_spec": spec}
        return {"error": True, "message": f"Unknown tool: {name}"}
    except Exception as e:
        return {"error": True, "message": str(e)}


def _trim_tool_result_for_llm(result: Any, max_chars: int = 30_000) -> str:
    try:
        payload = json.dumps(result, default=str)
    except Exception:
        payload = str(result)
    if os.environ.get("GEMS_CHAT_DEBUG_LLM_PAYLOADS", "").lower() in {"1", "true", "yes"}:
        print(f"[CHAT_DEBUG] outbound tool payload to LLM: {payload[:max_chars]}")
    if len(payload) > max_chars:
        return payload[:max_chars] + f'...","_truncated":true,"_original_chars":{len(payload)}}}'
    return payload


def run_agent(
    user_message: str,
    history: list[dict],
    data: GemsData,
    model: str | None = None,
    max_iters: int = 8,
) -> dict:
    client = get_llm_client()
    model_name = model or get_llm_model()

    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM_PROMPT + _dictionary_prompt()}
    ]
    for turn in history:
        messages.append({"role": turn["role"], "content": turn.get("content", "")})
    messages.append({"role": "user", "content": user_message})

    tool_calls_log: list[ToolCall] = []
    state: dict = {}

    for _ in range(max_iters):
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=TOOLS,
            temperature=0.2,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            return {
                "answer": (msg.content or "").strip(),
                "tool_calls": tool_calls_log,
                "plot_spec": state.get("last_plot"),
            }

        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _execute_tool(data, tc.function.name, args, state)
            tool_calls_log.append(ToolCall(name=tc.function.name, arguments=args, result=result))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _trim_tool_result_for_llm(result),
                }
            )

    return {
        "answer": "I reached the maximum number of tool calls. Please try a more specific aggregate question.",
        "tool_calls": tool_calls_log,
        "plot_spec": state.get("last_plot"),
    }
