"""OpenAI tool-calling agent for the Chat page (Genie-style).

The model gets three tools: list_tables, get_schema, run_sql. It decides when
to call them, and we run a short loop (max N iterations) until the model
returns a final message with no more tool calls.

All SQL is validated inside the data layer (gems_data._validate_select_sql),
so even if the model emits something unsafe we're protected: SELECT/WITH only,
single statement, allowlisted tables or CTE names, outer LIMIT wrap.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from gems_data import GemsData

_SYSTEM_PROMPT = """You are a data analyst assistant for the GEMS project
(animal science / GreenFeed research data at Cornell).

You have READ-ONLY access to Delta tables in `gems_catalog.gold_v1` via three tools:
- list_tables(): returns the names of the tables you are allowed to query.
- get_schema(table): returns column names and types for one table.
- run_sql(sql, limit): runs a single SELECT (or WITH ... SELECT) and returns rows.

How to answer:
1. If you don't know what's available, call list_tables first.
2. Before writing SQL, call get_schema on the tables you plan to use so you know
   the real column names and types.
3. Write a SELECT that uses fully-qualified names: gems_catalog.gold_v1.<table>.
4. Call run_sql. If the server returns an error, read it and fix the SQL.
5. Summarize the result in plain English. Mention row counts, aggregations,
   caveats (small n, missing values). If the server flagged `truncated: true`,
   tell the user more rows exist.

Rules:
- Only SELECT / WITH. No DDL, no DML. The server will reject anything else.
- Always use the `gems_catalog.gold_v1` prefix on tables.
- Prefer aggregations over dumping raw rows when the question is quantitative.
- Keep limits reasonable (default 1000; bump up only when needed).
- Do not fabricate values that did not appear in a tool result.
- If the question cannot be answered from these tables, say so.
"""


TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_tables",
            "description": "List the gold tables this API key is allowed to query.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_schema",
            "description": "Return the columns and types for a single allowlisted table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "Table name only, no catalog/schema prefix.",
                    }
                },
                "required": ["table"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Run a read-only SQL query (SELECT or WITH ... SELECT) against "
                "the gold tables and return the rows. Server enforces SELECT-only "
                "and the allowlist, and applies a row cap."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A single SELECT or WITH ... SELECT statement.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows to return (default 1000; server caps hard).",
                        "minimum": 1,
                        "maximum": 100000,
                    },
                },
                "required": ["sql"],
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


def _execute_tool(data: GemsData, name: str, args: dict) -> Any:
    try:
        if name == "list_tables":
            return {"tables": data.list_tables()}
        if name == "get_schema":
            table = args.get("table", "")
            if not table:
                return {"error": True, "message": "Missing 'table' argument"}
            return {"table": table, "columns": data.get_schema(table)}
        if name == "run_sql":
            sql = args.get("sql", "")
            limit = int(args.get("limit", 1000) or 1000)
            return data.run_sql(sql, limit=limit)
        return {"error": True, "message": f"Unknown tool: {name}"}
    except Exception as e:
        return {"error": True, "message": str(e)}


def _trim_tool_result_for_llm(result: Any, max_chars: int = 30_000) -> str:
    """Serialize a tool result for the LLM, trimming if it's absurdly large."""
    try:
        s = json.dumps(result, default=str)
    except Exception:
        s = str(result)
    if len(s) > max_chars:
        return s[:max_chars] + f'...","_truncated":true,"_original_chars":{len(s)}}}'
    return s


def run_agent(
    user_message: str,
    history: list[dict],
    data: GemsData,
    model: str | None = None,
    max_iters: int = 8,
) -> dict:
    """Run one user turn of the agent.

    `history` is a list of {"role": "user"|"assistant", "content": str} from prior
    turns (tool calls are NOT persisted across turns; the LLM re-derives them as
    needed from its own textual answers).

    Returns {"answer": str, "tool_calls": [ToolCall, ...]}.
    """
    from openai import OpenAI

    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=key)
    model_name = model or os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o")

    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for turn in history:
        messages.append({"role": turn["role"], "content": turn.get("content", "")})
    messages.append({"role": "user", "content": user_message})

    tool_calls_log: list[ToolCall] = []

    for _ in range(max_iters):
        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=TOOLS,
            temperature=0.2,
        )
        msg = resp.choices[0].message

        if not msg.tool_calls:
            return {"answer": (msg.content or "").strip(), "tool_calls": tool_calls_log}

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
            result = _execute_tool(data, tc.function.name, args)
            tool_calls_log.append(
                ToolCall(name=tc.function.name, arguments=args, result=result)
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": _trim_tool_result_for_llm(result),
                }
            )

    return {
        "answer": (
            "I reached the maximum number of tool calls without producing a final "
            "answer. Please try a more specific question, or narrow the table/"
            "columns you're asking about."
        ),
        "tool_calls": tool_calls_log,
    }
