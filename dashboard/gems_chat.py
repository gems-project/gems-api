"""Tool-calling chat agent with aggregate-only data guardrails."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from gems_data import GemsData, display_name
from llm_client import chat_completion, get_llm_client, get_llm_model

_VISUAL_INTENT = re.compile(
    r"\b(plot|chart|graph|figure|distribution|histogram|box\s*plot|boxplot|"
    r"scatter|bar\s*chart|line\s*chart|visuali[sz]e|trend|frequency)\b",
    re.IGNORECASE,
)

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


_SYSTEM_PROMPT = """You are a SQL data assistant for the GEMS database on Databricks.

CRITICAL SQL RULES:
- ALWAYS use fully-qualified table names with backticks: `gems_catalog`.`gold_v1`.`<table_name>`
- The ONLY allowed schema is `gold_v1`. NEVER write `gold.`, `silver.`, `bronze.`, `gems_schema.`, or any other schema prefix.
- The list_tables tool returns objects with a `full_name` field — use that string directly in your SELECTs.
- SELECT statements only. No DDL, no DML, no system commands.
- Always add LIMIT to large result sets unless the user asks for a specific aggregate.

When you receive a tool error like 'Schema X is not allowed' or 'TABLE_OR_VIEW_NOT_FOUND', do NOT retry the same shape. Re-read the list_tables output and use the full_name field exactly as given.

You are a data analyst assistant for the GEMS project
(animal science / GreenFeed research data at Cornell).

ABSOLUTE PRIVACY RULE:
- Never request, expose, summarize, or reproduce row-level data.
- You may only use schema, column descriptions, table summaries, and aggregated
  or computed query results.
- If a question requires individual row-level records, refuse and suggest an
  aggregate alternative.
- If the requested table/domain is not available from list_tables, say that the
  table is not currently available in this dashboard's allowlist and do not retry
  made-up table names.

Use tools to answer questions:
1. list_tables() to see available tables.
2. describe_table(name) before SQL so you know real columns, dtypes, sample size,
   and null percentages.
3. run_aggregate_query(sql) only for aggregate/statistical summaries. The server
   rejects unsafe SQL and row dumps.
4. plot(spec) or render_chart(...) after an aggregate result exists.
5. When the user asks for a chart, distribution, figure, or visualization, you MUST
   run_aggregate_query first, then call plot or render_chart in the same turn when possible.

SQL rules:
- Use SELECT/WITH only.
- Prefer COUNT, AVG, SUM, MIN, MAX, GROUP BY, and DISTINCT.
- Use the exact `full_name` string from list_tables for table references.
- Do not use SELECT *.
- Do not ask for individual animal/person/source-file records.
- Do not fabricate values that did not appear in tool results.
- End with 1-2 short follow-up question suggestions when helpful (e.g. "Would you like
  a bar chart by contributor?").
- If you are unsure, say what is missing instead of guessing.

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
                "properties": {"name": {"type": "string", "description": "Raw table name from the table field, not full_name."}},
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
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line", "scatter", "histogram", "box", "pie"],
                    },
                    "x": {"type": "string"},
                    "y": {"type": "string"},
                    "title": {"type": "string"},
                },
                "required": ["chart_type", "x", "y"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_chart",
            "description": (
                "Auto-build a chart from the latest aggregate query using column names. "
                "Use when the user wants a figure but has not specified x/y."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "line", "scatter", "histogram", "box", "pie"],
                    },
                    "title": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_last_result",
            "description": (
                "Return a compact JSON summary of the latest aggregate query "
                "(row count, column names, numeric ranges) for self-checking."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
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


def _full_table_name(data: GemsData, name: str) -> str:
    return f"`{data.cfg['catalog']}`.`{data.cfg['schema']}`.`{name}`"


def _is_numeric(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _auto_chart_spec(result: dict, chart_type: str = "bar", title: str = "") -> dict | None:
    rows = result.get("rows") or []
    if not rows:
        return None
    columns = list(rows[0].keys())
    if not columns:
        return None
    numeric = [c for c in columns if _is_numeric(rows[0].get(c))]
    categorical = [c for c in columns if c not in numeric]
    if chart_type == "histogram" and numeric:
        col = numeric[0]
        return {
            "chart_type": "histogram",
            "x": col,
            "y": col,
            "title": title,
            "source": "last_aggregate_result",
        }
    if len(columns) < 2 or not numeric:
        return None
    y = numeric[-1]
    x = categorical[0] if categorical else columns[0]
    if x == y and len(columns) > 2:
        x = next((c for c in columns if c != y), x)
    return {
        "chart_type": chart_type,
        "x": x,
        "y": y,
        "title": title,
        "source": "last_aggregate_result",
    }


def _summarize_result(result: dict) -> dict:
    rows = result.get("rows") or []
    if not rows:
        return {"row_count": 0, "columns": [], "sample": []}
    columns = list(rows[0].keys())
    summary: dict[str, Any] = {"row_count": len(rows), "columns": columns, "sample": rows[:5]}
    for col in columns:
        vals = [r.get(col) for r in rows if r.get(col) is not None]
        if vals and all(_is_numeric(v) for v in vals[:20]):
            nums = [float(v) for v in vals]
            summary[f"{col}_min"] = min(nums)
            summary[f"{col}_max"] = max(nums)
    return summary


def _verify_answer(
    user_message: str,
    draft_answer: str,
    tool_calls_log: list["ToolCall"],
    model_name: str,
) -> str:
    """Second-pass check: answer must be supported by tool evidence only."""
    evidence = []
    has_aggregate = False
    for tc in tool_calls_log:
        if tc.name == "run_aggregate_query":
            has_aggregate = True
        if tc.name in {"run_aggregate_query", "summarize_last_result"}:
            evidence.append({"tool": tc.name, "result": tc.result})
    if not has_aggregate or not evidence:
        return draft_answer

    prompt = (
        "You are a strict fact-checker. Given the user question, tool evidence, and a draft answer, "
        "return ONLY valid JSON: {\"ok\": true/false, \"revised_answer\": \"...\"}. "
        "Set ok=false if the draft cites numbers, tables, or trends not supported by evidence. "
        "If ok=false, revised_answer must fix or qualify the answer and say what is unknown.\n\n"
        f"User question: {user_message}\n\n"
        f"Tool evidence: {json.dumps(evidence, default=str)[:25_000]}\n\n"
        f"Draft answer: {draft_answer}"
    )
    try:
        resp = chat_completion(
            model=model_name,
            messages=[
                {"role": "system", "content": "Respond with JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=1200,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) >= 2 else raw
            if raw.lstrip().lower().startswith("json"):
                raw = raw.split("\n", 1)[-1]
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start : end + 1]
        parsed = json.loads(raw)
        if parsed.get("ok"):
            return draft_answer
        revised = (parsed.get("revised_answer") or "").strip()
        if revised:
            return revised + "\n\n*(Answer adjusted after data verification.)*"
    except Exception:
        return draft_answer
    return draft_answer


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
        "full_name": _full_table_name(data, name),
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
                {
                    "full_name": _full_table_name(data, table),
                    "table": table,
                    "label": display_name(table),
                    "description": descriptions.get(table, ""),
                }
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
        if name == "render_chart":
            last = state.get("last_result") or {}
            if last.get("error"):
                return last
            chart_type = args.get("chart_type") or "bar"
            spec = _auto_chart_spec(last, chart_type=chart_type, title=args.get("title", ""))
            if not spec:
                return {"error": True, "message": "No aggregate result to chart. Run run_aggregate_query first."}
            state["last_plot"] = spec
            return {"plot_spec": spec}
        if name == "summarize_last_result":
            last = state.get("last_result") or {}
            if last.get("error"):
                return last
            return _summarize_result(last)
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
    max_iters: int = 15,
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
        try:
            resp = chat_completion(
                model=model_name,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as e:
            return {
                "answer": (
                    "Chat agent failed before it could query data. "
                    f"Model `{model_name}` returned: {e}"
                ),
                "tool_calls": tool_calls_log,
                "plot_spec": state.get("last_plot"),
            }
        msg = resp.choices[0].message

        if not msg.tool_calls:
            answer = (msg.content or "").strip()
            if _VISUAL_INTENT.search(user_message) and not state.get("last_plot"):
                auto = _auto_chart_spec(state.get("last_result") or {}, chart_type="bar")
                if auto:
                    state["last_plot"] = auto
            answer = _verify_answer(user_message, answer, tool_calls_log, model_name)
            return {
                "answer": answer,
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

    answer = "I reached the maximum number of tool calls. Please try a more specific aggregate question."
    if _VISUAL_INTENT.search(user_message) and not state.get("last_plot"):
        auto = _auto_chart_spec(state.get("last_result") or {}, chart_type="bar")
        if auto:
            state["last_plot"] = auto
    return {
        "answer": answer,
        "tool_calls": tool_calls_log,
        "plot_spec": state.get("last_plot"),
    }
