"""Chat page — ask questions about the gold tables in natural language.

Uses OpenAI tool-calling. The LLM can list tables, inspect schemas, and run
SELECT queries through the standalone data layer. Every SQL call is validated
client-side (SELECT-only, allowlisted tables, no DDL/DML). Internal workflow
columns are stripped from every schema and result returned to the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

_DASHBOARD_ROOT = Path(__file__).resolve().parent
load_dotenv(_DASHBOARD_ROOT / ".env", override=True)
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from gems_auth import get_current_user  # noqa: E402
from gems_chat import run_agent  # noqa: E402
from gems_data import GemsData  # noqa: E402
from gems_ui import page_header, sidebar_user  # noqa: E402

st.set_page_config(page_title="Chat · GEMS", layout="wide", page_icon="💬")
page_header(
    "Chat with your data",
    "Ask questions in plain English — the assistant lists tables, reads "
    "schemas, and runs read-only SELECT queries against the GEMS warehouse.",
)

user = get_current_user()
sidebar_user(user)

st.sidebar.markdown("**Example questions**")
st.sidebar.caption("- What tables are available and what do they contain?")
st.sidebar.caption("- How many rows are in bodyweight?")
st.sidebar.caption("- Average body weight per contributor, sorted descending.")
st.sidebar.caption("- Which animals have the most respiration-chamber measurements?")

if st.sidebar.button("Clear conversation"):
    st.session_state.pop("chat_history", None)
    st.rerun()

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

try:
    data = GemsData()
except Exception as e:
    st.error(f"Data source not configured: {e}")
    st.stop()


def _render_tool_calls(tool_calls: list) -> None:
    if not tool_calls:
        return
    with st.expander(f"Tool calls ({len(tool_calls)})"):
        for i, tc in enumerate(tool_calls, start=1):
            name = tc["name"] if isinstance(tc, dict) else tc.name
            args = tc["arguments"] if isinstance(tc, dict) else tc.arguments
            result = tc["result"] if isinstance(tc, dict) else tc.result

            st.markdown(f"**{i}. `{name}`**")
            if args:
                st.code(
                    args.get("sql") if name == "run_sql" and "sql" in args else str(args),
                    language="sql" if name == "run_sql" else "json",
                )

            if isinstance(result, dict) and result.get("error"):
                st.error(
                    f"Tool error: {result.get('detail') or result.get('message')}"
                )
            elif isinstance(result, dict) and "rows" in result:
                rows = result.get("rows", [])
                if rows:
                    df = pd.DataFrame(rows)
                    st.caption(
                        f"{result.get('row_count', len(rows))} row(s)"
                        + (" (truncated)" if result.get("truncated") else "")
                    )
                    st.dataframe(df, use_container_width=True)
                else:
                    st.caption("0 rows returned")
            elif isinstance(result, dict) and "tables" in result:
                st.write(result["tables"])
            elif isinstance(result, dict) and "columns" in result and "table" in result:
                st.caption(f"Schema of `{result['table']}`")
                st.dataframe(pd.DataFrame(result["columns"]), use_container_width=True)
            else:
                st.json(result if isinstance(result, dict) else {"value": str(result)})


for turn in st.session_state.chat_history:
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])
        if turn["role"] == "assistant":
            _render_tool_calls(turn.get("tool_calls", []))


user_msg = st.chat_input("Ask a question about the data")
if user_msg:
    with st.chat_message("user"):
        st.markdown(user_msg)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            openai_history = [
                {"role": t["role"], "content": t["content"]}
                for t in st.session_state.chat_history
            ]
            try:
                result = run_agent(user_msg, openai_history, data)
                answer = result["answer"]
                tool_calls = result["tool_calls"]
            except Exception as e:
                answer = f"Error: {e}"
                tool_calls = []

        st.markdown(answer or "_(no answer)_")
        tc_serialized = [
            {"name": tc.name, "arguments": tc.arguments, "result": tc.result}
            for tc in tool_calls
        ]
        _render_tool_calls(tc_serialized)

    st.session_state.chat_history.append({"role": "user", "content": user_msg})
    st.session_state.chat_history.append(
        {
            "role": "assistant",
            "content": answer,
            "tool_calls": tc_serialized,
        }
    )
