"""Download page.

Two modes:
- Full download of a table as CSV (no row cap — the whole thing).
- Incremental download: pick a watermark column; only rows newer than the
  last value stored for this user are returned, and the watermark is
  updated after a successful download.

Per-user watermark state lives in Azure Table Storage. If that isn't
configured, only full downloads are offered. Internal/workflow columns are
stripped from every CSV — the user never sees them.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

_DASHBOARD_ROOT = Path(__file__).resolve().parent
load_dotenv(_DASHBOARD_ROOT / ".env", override=True)
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from gems_auth import require_authorized_user  # noqa: E402
from gems_data import GemsData, watermark_candidates  # noqa: E402
from gems_ui import page_header, sidebar_user  # noqa: E402
from gems_watermarks import WatermarkStore  # noqa: E402

st.set_page_config(page_title="Download · GEMS", layout="wide", page_icon="⬇️")
page_header(
    "Download CSV",
    "Grab the whole table, or only the new rows since your last download.",
)

user = require_authorized_user()
sidebar_user(user)

try:
    data = GemsData()
except Exception as e:
    st.error(f"Data source not configured: {e}")
    st.stop()

store = WatermarkStore()
if not store.enabled:
    st.info(
        "Incremental download is disabled because `AZURE_TABLES_CONNECTION_STRING` "
        "is not set. Full downloads still work."
    )

pairs = data.list_tables_display()
if not pairs:
    st.warning("No tables available.")
    st.stop()

display_to_internal = {d: i for d, i in pairs}
chosen_display = st.selectbox("Table", list(display_to_internal.keys()))
table = display_to_internal[chosen_display]

with st.spinner("Loading schema..."):
    try:
        schema = data.get_schema(table)
    except Exception as e:
        st.error(f"Schema lookup failed: {e}")
        st.stop()

candidates = watermark_candidates(schema)

mode_options = ["Full table (all rows)"]
if store.enabled and candidates:
    mode_options.append("Only new rows since my last download")

mode = st.radio("Download mode", mode_options, horizontal=True)

since_col: str | None = None
since_value: str | None = None
value_type: str = "string"

if mode == "Only new rows since my last download":
    col_labels = [f"{c['name']}  ({c['type']})" for c in candidates]
    idx = st.selectbox(
        "Watermark column",
        list(range(len(candidates))),
        format_func=lambda i: col_labels[i],
    )
    chosen = candidates[idx]
    since_col = chosen["name"]
    col_type = str(chosen["type"]).lower()
    if any(k in col_type for k in ("timestamp", "date")):
        value_type = "timestamp"
    elif any(k in col_type for k in ("int", "long", "bigint")):
        value_type = "bigint"
    else:
        value_type = "string"

    prev = store.get(user, table, since_col)
    if prev and prev.get("lastValue"):
        since_value = str(prev["lastValue"])
        st.caption(
            f"Last watermark on `{since_col}`: `{since_value}` "
            f"(updated {prev.get('updatedAt', 'n/a')})."
        )
    else:
        st.caption(
            "No previous download recorded for this table and column. The "
            "first download fetches everything and then starts tracking new "
            "rows."
        )

st.markdown(
    '<div class="gems-muted">Internal workflow columns (such as '
    "<code>workbookFile</code>, <code>ingestRunId</code>) are stripped from "
    "the CSV automatically.</div>",
    unsafe_allow_html=True,
)

if st.button("Prepare download", type="primary"):
    with st.spinner("Querying Databricks..."):
        try:
            raw = data.export_csv(
                table,
                since_col=since_col if since_value else None,
                since_value=since_value,
            )
        except Exception as e:
            st.error(f"Download failed: {e}")
            st.stop()

    size_mb = len(raw) / (1024 * 1024)
    st.success(f"CSV ready — {len(raw):,} bytes ({size_mb:,.2f} MB).")
    st.download_button(
        label=f"Save {chosen_display}.csv",
        data=raw,
        file_name=f"{chosen_display}.csv",
        mime="text/csv",
    )

    if since_col:
        try:
            df = pd.read_csv(io.BytesIO(raw))
            if since_col in df.columns and len(df) > 0:
                new_max = df[since_col].max()
                store.set(
                    user,
                    table,
                    since_col,
                    str(new_max),
                    value_type=value_type,
                )
                st.info(
                    f"Watermark on `{since_col}` updated to `{new_max}`. "
                    "Next download will only include rows after this value."
                )
            elif len(df) == 0:
                st.caption("No new rows since your last download.")
        except Exception as e:
            st.warning(f"Could not update watermark automatically: {e}")
