"""Explore & Visualize page.

Three sections:

1. **Inspect a table** — pick one table, see its columns and a preview.
2. **Descriptive statistics** — pick a column from the inspected table and
   see count/mean/median/quantiles/skew/kurtosis (for numeric columns) or
   value counts and a bar chart (for categorical columns). Uses every row.
3. **Quick chart** — pick one OR several tables, join them on a shared key,
   then plot any columns from the merged dataset. Charts use every row (no
   sampling). The AI assistant interprets the chart from summary statistics
   only; the raw data is never sent to the model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

_DASHBOARD_ROOT = Path(__file__).resolve().parent
load_dotenv(_DASHBOARD_ROOT / ".env", override=True)
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from gems_ai import interpret_plot  # noqa: E402
from gems_auth import get_current_user  # noqa: E402
from gems_data import (  # noqa: E402
    GemsData,
    REDACTED_COLUMNS,
    _coerce_numeric,
    display_name,
)
from gems_ui import page_header, sidebar_user  # noqa: E402

st.set_page_config(page_title="Explore · GEMS", layout="wide", page_icon="🔎")
page_header(
    "Explore & Visualize",
    "Browse any table, preview rows, and build a chart over the full "
    "dataset — optionally across several joined tables.",
)

user = get_current_user()
sidebar_user(user)

try:
    data = GemsData()
except Exception as e:
    st.error(f"Data source not configured: {e}")
    st.stop()

pairs = data.list_tables_display()
if not pairs:
    st.warning("No tables available. Check `ALLOWED_TABLES` in the environment.")
    st.stop()

display_to_internal = {d: i for d, i in pairs}
display_names = list(display_to_internal.keys())


# ---------------------------------------------------------------------------
# Section 1 — Inspect a single table
# ---------------------------------------------------------------------------

st.markdown("### 1 · Inspect a table")

chosen_display = st.selectbox("Table", display_names)
table = display_to_internal[chosen_display]

with st.spinner("Loading schema..."):
    try:
        schema = data.get_schema(table)
    except Exception as e:
        st.error(f"Schema lookup failed: {e}")
        st.stop()

left, right = st.columns([1, 1])

with left:
    st.markdown("**Columns**")
    st.caption(f"`{chosen_display}` (Delta table `{table}`).")
    st.dataframe(pd.DataFrame(schema), use_container_width=True, hide_index=True)

with right:
    preview_limit = st.slider(
        "Preview rows",
        min_value=50,
        max_value=2000,
        value=200,
        step=50,
    )
    with st.spinner("Loading preview..."):
        try:
            preview = data.preview(table, limit=preview_limit)
        except Exception as e:
            st.error(f"Preview failed: {e}")
            st.stop()

    preview_df = pd.DataFrame(preview.get("rows", []))
    for c in preview_df.columns:
        try:
            preview_df[c] = pd.to_numeric(preview_df[c])
        except (ValueError, TypeError):
            pass
    st.markdown("**Preview**")
    st.dataframe(preview_df, use_container_width=True)


# ---------------------------------------------------------------------------
# Section 2 — Descriptive statistics for a single column
# ---------------------------------------------------------------------------

st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)
st.markdown("### 2 · Descriptive statistics")
st.caption(
    f"Summary of a single column from `{chosen_display}`. Uses every row — "
    "change the table above to describe a different one."
)


@st.cache_data(ttl=300, show_spinner=False)
def _load_full_table(_data: GemsData, t: str) -> pd.DataFrame:
    """Load a full single table (already redacted + numeric-coerced)."""
    return _data.load_dataframe(t)


desc_cols = [c["name"] for c in schema if c["name"] not in REDACTED_COLUMNS]
if not desc_cols:
    st.info("No columns available to describe.")
else:
    desc_col = st.selectbox("Column", desc_cols, key="desc_col")

    with st.spinner("Loading full table..."):
        try:
            df_desc = _load_full_table(data, table)
        except Exception as e:
            st.error(f"Load failed: {e}")
            df_desc = None

    if df_desc is not None and desc_col in df_desc.columns:
        s = df_desc[desc_col]
        total = int(len(s))
        n_missing = int(s.isna().sum())
        n_present = total - n_missing

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("N total", f"{total:,}")
        m2.metric("N present", f"{n_present:,}")
        m3.metric("N missing", f"{n_missing:,}")
        m4.metric(
            "% missing",
            f"{(n_missing / total * 100) if total else 0:.1f}%",
        )

        is_num = pd.api.types.is_numeric_dtype(s)

        if is_num:
            s_clean = pd.to_numeric(s, errors="coerce").dropna()
            desc = s_clean.describe()
            try:
                skew_val = float(s_clean.skew())
            except Exception:
                skew_val = float("nan")
            try:
                kurt_val = float(s_clean.kurt())
            except Exception:
                kurt_val = float("nan")

            stats_df = pd.DataFrame(
                {
                    "statistic": [
                        "mean",
                        "std",
                        "min",
                        "25%",
                        "50% (median)",
                        "75%",
                        "max",
                        "IQR",
                        "skewness",
                        "kurtosis",
                    ],
                    "value": [
                        desc.get("mean"),
                        desc.get("std"),
                        desc.get("min"),
                        desc.get("25%"),
                        desc.get("50%"),
                        desc.get("75%"),
                        desc.get("max"),
                        (desc.get("75%") - desc.get("25%"))
                        if desc.get("75%") is not None and desc.get("25%") is not None
                        else None,
                        skew_val,
                        kurt_val,
                    ],
                }
            )
            stats_df["value"] = pd.to_numeric(
                stats_df["value"], errors="coerce"
            ).round(4)
            st.dataframe(stats_df, use_container_width=True, hide_index=True)

            hc1, hc2 = st.columns(2)
            with hc1:
                fig_hist = px.histogram(df_desc, x=desc_col, nbins=50)
                fig_hist.update_layout(
                    margin=dict(l=10, r=10, t=35, b=10),
                    height=320,
                    title="Distribution",
                )
                st.plotly_chart(fig_hist, use_container_width=True)
            with hc2:
                fig_box = px.box(df_desc, y=desc_col, points="outliers")
                fig_box.update_layout(
                    margin=dict(l=10, r=10, t=35, b=10),
                    height=320,
                    title="Boxplot",
                )
                st.plotly_chart(fig_box, use_container_width=True)
        else:
            n_unique = int(s.nunique(dropna=True))
            st.caption(f"Treated as categorical · unique non-null values: **{n_unique:,}**")

            vc = s.value_counts(dropna=False).head(30)
            vc_df = (
                vc.rename_axis(desc_col).reset_index(name="count")
            )
            vc_df["percent"] = (
                vc_df["count"] / total * 100
            ).round(2) if total else 0.0
            st.dataframe(vc_df, use_container_width=True, hide_index=True)

            if not vc_df.empty:
                fig_cat = px.bar(vc_df, x=desc_col, y="count")
                fig_cat.update_layout(
                    margin=dict(l=10, r=10, t=35, b=10),
                    height=360,
                    title=f"Top {len(vc_df)} values",
                )
                st.plotly_chart(fig_cat, use_container_width=True)


# ---------------------------------------------------------------------------
# Section 3 — Quick chart (optionally across multiple joined tables)
# ---------------------------------------------------------------------------

st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)
st.markdown("### 3 · Quick chart")
st.caption(
    "Pick one or several tables and the chart will use every row in the "
    "(joined) dataset — no sampling. With two or more tables, choose a join "
    "key that exists in all of them (for example `animalIdentifier` or "
    "`studyID`)."
)


selected_displays = st.multiselect(
    "Tables to use",
    display_names,
    default=[chosen_display],
    help="Start with the table you were inspecting above, or add more to "
    "join and chart columns from multiple sources.",
    key="chart_tables",
)
if not selected_displays:
    st.info("Pick at least one table.")
    st.stop()

selected_tables = [display_to_internal[d] for d in selected_displays]

# Load all schemas so we can compute column candidates + join keys.
schemas: dict[str, list[dict]] = {}
try:
    with st.spinner("Reading schemas..."):
        for t in selected_tables:
            schemas[t] = data.get_schema(t)
except Exception as e:
    st.error(f"Schema lookup failed: {e}")
    st.stop()

join_keys: list[str] = []
join_how = "outer"
if len(selected_tables) >= 2:
    col_sets = [{c["name"] for c in schemas[t]} for t in selected_tables]
    common = sorted(set.intersection(*col_sets))
    if not common:
        st.error(
            "Those tables share no columns — they cannot be joined. Pick "
            "tables that share at least one identifier column."
        )
        st.stop()
    preferred_order = [
        "animalidentifier",
        "studyid",
        "date",
        "measurementdate",
        "treatment",
        "sourcesheet",
    ]
    preferred = [c for c in common if c.lower() in preferred_order]
    likely_keys = preferred + [
        c
        for c in common
        if c not in preferred
        and any(k in c.lower() for k in ("id", "identifier", "code", "date", "treatment"))
    ]
    default_keys = likely_keys[:2] if likely_keys else common[:1]
    join_keys = st.multiselect(
        "Join on",
        options=common,
        default=default_keys,
        help=(
            "Choose one or more keys. Common choices are `animalIdentifier`, "
            "`studyID`, date columns, and treatment columns when available."
        ),
        key="chart_join_keys",
    )
    join_how = st.selectbox(
        "Join type",
        options=["outer", "inner", "left"],
        index=0,
        help=(
            "`outer` keeps all rows from all tables. `inner` keeps only matched rows."
        ),
        key="chart_join_how",
    )
    if not join_keys:
        st.info("Pick at least one join key.")
        st.stop()


# Build the list of selectable columns from the merged schema. Column names
# that appear in more than one table are prefixed with `<table>__` so the
# dropdowns match what the join actually produces.
def _merged_column_names() -> list[str]:
    seen: dict[str, int] = {}
    for t in selected_tables:
        for c in schemas[t]:
            seen[c["name"]] = seen.get(c["name"], 0) + 1

    out: list[str] = []
    for t in selected_tables:
        short = display_name(t)
        for c in schemas[t]:
            name = c["name"]
            if name in REDACTED_COLUMNS:
                continue
            if name in join_keys:
                if name in out:
                    continue
                out.append(name)
                continue
            if len(selected_tables) > 1 and seen.get(name, 0) > 1:
                out.append(f"{short}__{name}")
            else:
                out.append(name)
    # de-dup while preserving order
    deduped: list[str] = []
    for n in out:
        if n not in deduped:
            deduped.append(n)
    return deduped


column_candidates = _merged_column_names()

chart_type = st.selectbox(
    "Chart type",
    ["scatter", "line", "bar", "histogram", "box"],
    key="chart_type",
)

chart_kwargs: dict = {}
if chart_type in ("scatter", "line"):
    chart_kwargs = {
        "x": st.selectbox("X", column_candidates, key="x"),
        "y": st.selectbox(
            "Y",
            column_candidates,
            key="y",
            index=min(1, max(0, len(column_candidates) - 1)),
        ),
    }
    color = st.selectbox(
        "Color (optional)", ["(none)"] + column_candidates, key="color"
    )
    chart_kwargs["color"] = None if color == "(none)" else color
elif chart_type == "bar":
    chart_kwargs = {
        "x": st.selectbox("X", column_candidates, key="bx"),
        "y": st.selectbox("Y", column_candidates, key="by"),
    }
elif chart_type == "histogram":
    chart_kwargs = {"x": st.selectbox("X", column_candidates, key="hx")}
elif chart_type == "box":
    chart_kwargs = {
        "x": st.selectbox("Group (X)", column_candidates, key="ox"),
        "y": st.selectbox("Value (Y)", column_candidates, key="oy"),
    }


def _normalize_join_key(s: pd.Series) -> pd.Series:
    """Normalize join keys across mixed object/float/string sources."""
    out = s.astype("string").str.strip()
    out = out.str.replace(r"\.0+$", "", regex=True)
    return out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})


@st.cache_data(ttl=300, show_spinner=False)
def _load_joined(
    _data: GemsData, tbls: tuple[str, ...], keys: tuple[str, ...], how: str
) -> pd.DataFrame:
    """Load & inner-join the given tables. Cached on (tables, join_keys)."""
    frames: list[pd.DataFrame] = []
    # Recompute which columns collide — must match the dropdown labels.
    table_list = list(tbls)
    schema_cols: dict[str, set[str]] = {
        t: {c["name"] for c in _data.get_schema(t)} for t in table_list
    }
    for t in table_list:
        df_t = _data.load_dataframe(t)
        df_t = df_t.loc[:, ~df_t.columns.duplicated()]
        for k in keys:
            if k in df_t.columns:
                df_t[k] = _normalize_join_key(df_t[k])
        if len(table_list) > 1:
            short = display_name(t)
            rename_map: dict[str, str] = {}
            for c in df_t.columns:
                if c in keys:
                    continue
                in_others = sum(
                    1 for other in table_list
                    if other != t and c in schema_cols[other]
                )
                if in_others > 0:
                    rename_map[c] = f"{short}__{c}"
            if rename_map:
                df_t = df_t.rename(columns=rename_map)
        frames.append(df_t)

    if len(frames) == 1:
        out = frames[0]
    else:
        out = frames[0]
        for df_t in frames[1:]:
            out = out.merge(df_t, on=list(keys), how=how)

    # Scrub any redacted column that leaked through (incl. renamed forms).
    out = out.drop(
        columns=[
            c for c in out.columns
            if c in REDACTED_COLUMNS
            or c.split("__")[-1] in REDACTED_COLUMNS
        ],
        errors="ignore",
    )
    return _coerce_numeric(out)


if st.button("Render chart", type="primary"):
    with st.spinner("Loading data from Databricks..."):
        try:
            df_full = _load_joined(
                data, tuple(selected_tables), tuple(join_keys), join_how
            )
        except Exception as e:
            st.error(f"Load failed: {e}")
            st.stop()

    if df_full.empty:
        st.warning(
            "Join produced 0 rows — the selected tables have no matching "
            "values on the chosen join key(s)."
        )
        st.stop()

    used_cols = [v for v in chart_kwargs.values() if v is not None]
    missing = [c for c in used_cols if c not in df_full.columns]
    if missing:
        st.error(
            f"Column(s) not found in the joined dataframe: {missing}. "
            "This usually means the join renamed them. Re-open the chart "
            "dropdowns — the labels should now match what the data has."
        )
        st.stop()

    sub = df_full[used_cols].copy() if used_cols else df_full
    # Plotly ignores NaNs in x/y, but drop fully-null chart rows explicitly so
    # "outer join + keep all rows" still renders only valid points.
    if used_cols:
        sub = sub.dropna(how="any", subset=used_cols)
        if sub.empty:
            st.warning(
                "No plottable rows after filtering null values in selected chart columns."
            )
            st.stop()

    st.caption(
        f"Rendering from **{len(df_full):,}** rows across "
        f"**{len(selected_displays)}** table(s). "
        f"Columns used: {', '.join(used_cols) or '(none)'}."
    )

    fig = None
    try:
        if chart_type == "scatter":
            fig = px.scatter(sub, render_mode="webgl", **chart_kwargs)
        elif chart_type == "line":
            fig = px.line(sub, **chart_kwargs)
        elif chart_type == "bar":
            fig = px.bar(sub, **chart_kwargs)
        elif chart_type == "histogram":
            fig = px.histogram(sub, **chart_kwargs)
        elif chart_type == "box":
            fig = px.box(sub, **chart_kwargs)
    except Exception as e:
        st.error(f"Chart render error: {e}")

    if fig is not None:
        fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), height=520)
        st.session_state["last_chart_fig"] = fig.to_dict()

        try:
            describe = sub.describe(include="all").round(3).to_string()
        except Exception:
            describe = "(summary stats unavailable)"
        st.session_state["last_chart_context"] = {
            "tables": selected_displays,
            "join_keys": join_keys,
            "join_how": join_how,
            "chart_type": chart_type,
            "n_rows": len(df_full),
            "columns_used": used_cols,
            "describe": describe,
        }


# ---------------------------------------------------------------------------
# AI interpretation
# ---------------------------------------------------------------------------

ctx = st.session_state.get("last_chart_context")
fig_dict = st.session_state.get("last_chart_fig")
if fig_dict:
    try:
        st.plotly_chart(go.Figure(fig_dict), use_container_width=True)
    except Exception:
        pass
if ctx:
    st.markdown("#### AI interpretation")
    st.caption("Only summary statistics are sent to OpenAI — never raw rows.")
    if st.button("Interpret this chart"):
        prompt = (
            f"Tables: {ctx['tables']}\n"
            f"Join keys: {ctx.get('join_keys') or '(single table)'}\n"
            f"Join type: {ctx.get('join_how') or '(single table)'}\n"
            f"Chart type: {ctx['chart_type']}\n"
            f"Rows used: {ctx['n_rows']:,}\n"
            f"Selected columns: {ctx['columns_used']}\n"
            f"Summary statistics:\n{ctx['describe']}"
        )
        with st.spinner("Asking AI..."):
            try:
                st.markdown(interpret_plot(prompt))
            except Exception as e:
                st.error(f"AI call failed: {e}")
