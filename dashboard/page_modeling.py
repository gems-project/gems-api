"""Modeling page.

Fit OLS or linear-mixed models with full flexibility:
- Pick columns from a single table OR from several joined tables.
- Multiple predictor variables (multiple regression).
- Multiple random slopes on one grouping factor (random effects).
- Every row in the (joined) table is used — no row cap.

The AI is used only to interpret results. All statistics are computed by
statsmodels (OLS / MixedLM) inside this process.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

_DASHBOARD_ROOT = Path(__file__).resolve().parent
load_dotenv(_DASHBOARD_ROOT / ".env", override=True)
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from gems_ai import interpret_model  # noqa: E402
from gems_auth import require_authorized_user  # noqa: E402
from gems_data import (  # noqa: E402
    GemsData,
    REDACTED_COLUMNS,
    display_name,
    _coerce_numeric,
)
from gems_stats import (  # noqa: E402
    build_formula,
    build_re_formula,
    coefficient_table,
    fit_mixedlm_multi,
    fit_ols,
    summary_dict,
)
from gems_ui import page_header, sidebar_user  # noqa: E402


def _fmt(v, as_int: bool = False, sig: bool = False) -> str:
    """Format a number for a Streamlit st.metric; return '—' for None/NaN."""
    if v is None:
        return "—"
    try:
        f = float(v)
    except Exception:
        return str(v)
    if f != f:  # NaN
        return "—"
    if as_int:
        return f"{int(round(f)):,}"
    if sig:
        return f"{f:.3g}"
    absf = abs(f)
    if absf >= 1000 or (absf < 0.01 and absf != 0):
        return f"{f:.3g}"
    return f"{f:.4f}"


st.set_page_config(page_title="Modeling · GEMS", layout="wide", page_icon="📈")
page_header(
    "Linear and Linear-Mixed Models",
    "Fit regressions across one or more joined tables, with AI-written "
    "interpretations of the results.",
)

user = require_authorized_user()
sidebar_user(user)

try:
    data = GemsData()
except Exception as e:
    st.error(f"Data source not configured: {e}")
    st.stop()

pairs = data.list_tables_display()
if not pairs:
    st.warning("No tables available.")
    st.stop()

display_to_internal = {d: i for d, i in pairs}
display_names = list(display_to_internal.keys())


# ---------------------------------------------------------------------------
# Section 1 — Data selection (one or many tables, optional join)
# ---------------------------------------------------------------------------

st.markdown("### 1 · Choose data")
st.caption(
    "You can pick a single table, or several tables to join on a shared key "
    "(for example `animalIdentifier` or `studyID`). All rows from each table "
    "are loaded — no sampling."
)

selected_displays = st.multiselect(
    "Tables to use",
    display_names,
    default=display_names[:1],
    help="Select one table for a simple model, or multiple tables to build "
    "an inner join on one or more shared columns.",
)
if not selected_displays:
    st.info("Pick at least one table.")
    st.stop()

selected_tables = [display_to_internal[d] for d in selected_displays]

# Inspect schemas to find join candidates.
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
    # Intersection of column names across all chosen tables.
    col_sets = [{c["name"] for c in schemas[t]} for t in selected_tables]
    common = sorted(set.intersection(*col_sets))
    if not common:
        st.error(
            "Those tables share no columns — they cannot be joined. Pick tables "
            "that have at least one common identifier (for example "
            "`animalIdentifier`)."
        )
        st.stop()

    # Reasonable defaults: anything that looks like an identifier column.
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
            "Choose one or more keys. Common keys are `animalIdentifier`, "
            "`studyID`, date columns, and treatment columns when available."
        ),
    )
    join_how = st.selectbox(
        "Join type",
        options=["outer", "inner", "left"],
        index=0,
        help="`outer` keeps all rows; rows missing model variables are dropped at fit time.",
    )
    if not join_keys:
        st.info("Pick at least one join key.")
        st.stop()


def _normalize_join_key(s: pd.Series) -> pd.Series:
    out = s.astype("string").str.strip()
    out = out.str.replace(r"\.0+$", "", regex=True)
    return out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})


def _load_joined() -> pd.DataFrame:
    frames = []
    for t in selected_tables:
        df_t = data.load_dataframe(t)
        df_t = df_t.loc[:, ~df_t.columns.duplicated()]
        for k in join_keys:
            if k in df_t.columns:
                df_t[k] = _normalize_join_key(df_t[k])
        short = display_name(t)
        # For non-key columns that appear in more than one table, prefix with
        # the table name so the merge doesn't lose them or collide.
        if len(selected_tables) > 1:
            rename_map = {}
            for c in df_t.columns:
                if c in join_keys:
                    continue
                # Only rename if another table also has this column.
                in_others = sum(
                    1 for other_t in selected_tables if other_t != t
                    and c in {col["name"] for col in schemas[other_t]}
                )
                if in_others > 0:
                    rename_map[c] = f"{short}__{c}"
            if rename_map:
                df_t = df_t.rename(columns=rename_map)
        frames.append(df_t)

    if len(frames) == 1:
        return frames[0]

    out = frames[0]
    for df_t in frames[1:]:
        out = out.merge(df_t, on=join_keys, how=join_how)
    return out


if st.button("Load data", type="primary"):
    with st.spinner("Loading rows from Databricks (this may take a moment)..."):
        try:
            df = _load_joined()
        except Exception as e:
            st.error(f"Data load failed: {e}")
            st.stop()
    # Belt-and-suspenders: drop any redacted column that survived the rename
    # (e.g. "bodyweight__Expression"), and coerce Decimal object columns to
    # proper numeric dtypes so regressions can actually see them.
    df = df.drop(
        columns=[
            c for c in df.columns
            if c in REDACTED_COLUMNS
            or c.split("__")[-1] in REDACTED_COLUMNS
        ],
        errors="ignore",
    )
    df = _coerce_numeric(df)
    st.session_state["model_df"] = df
    st.session_state["model_tables"] = selected_displays
    st.session_state["model_join_keys"] = join_keys
    # Reset any previous results.
    for k in ("last_model_summary", "last_model_spec", "last_model_text"):
        st.session_state.pop(k, None)

df = st.session_state.get("model_df")
if df is None or df.empty:
    st.info("Click **Load data** to continue.")
    st.stop()

c_a, c_b, c_c = st.columns(3)
c_a.metric("Rows loaded", f"{len(df):,}")
c_b.metric("Columns", f"{len(df.columns):,}")
c_c.metric("Tables joined", f"{len(selected_displays)}")

with st.expander("Preview first 50 rows"):
    st.dataframe(df.head(50), use_container_width=True)

with st.expander("Column dtypes (for debugging)"):
    dtype_df = pd.DataFrame(
        {
            "column": df.columns,
            "dtype": [str(df[c].dtype) for c in df.columns],
            "is_numeric": [
                pd.api.types.is_numeric_dtype(df[c])
                or (
                    isinstance(df[c].dtype, pd.ArrowDtype)
                    and df[c].dtype.kind in ("i", "u", "f", "b")
                )
                for c in df.columns
            ],
        }
    )
    st.dataframe(dtype_df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Section 2 — Model specification
# ---------------------------------------------------------------------------

st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)
st.markdown("### 2 · Specify the model")

all_cols = list(df.columns)


def _is_numeric_series(s: pd.Series) -> bool:
    """True for numpy numeric dtypes AND numeric Arrow-backed dtypes."""
    if pd.api.types.is_numeric_dtype(s):
        return True
    if isinstance(s.dtype, pd.ArrowDtype):
        try:
            return s.dtype.kind in ("i", "u", "f", "b")
        except Exception:
            return False
    return False


numeric_cols = [c for c in all_cols if _is_numeric_series(df[c])]

if not numeric_cols:
    st.error(
        "No numeric columns available — cannot fit a regression on this "
        "data selection. Expand the **Column dtypes** expander above to see "
        "what was actually loaded."
    )
    st.stop()

model_kind = st.radio(
    "Model type",
    ["Linear regression (OLS)", "Linear mixed model (LMM)"],
    horizontal=True,
)

response = st.selectbox(
    "Response variable (y) — must be numeric", numeric_cols
)
predictors = st.multiselect(
    "Predictor variable(s) (x)",
    [c for c in all_cols if c != response],
    help="Select one for simple regression, or several for multiple regression. "
    "Categorical columns are encoded automatically.",
)
st.caption(
    "ID columns whose values are pure digits (for example `animalIdentifier = "
    "\"1099\"`) are treated as numeric after conversion. For those, use the "
    "grouping variable below (random effects) rather than a fixed-effect "
    "predictor."
)
intercept = st.checkbox("Include intercept", value=True)

group_cols: list[str] = []
random_slopes: list[str] = []
re_structure: str = "Single"

if model_kind == "Linear mixed model (LMM)":
    possible_groups = [c for c in all_cols if c not in [response] + predictors]
    if not possible_groups:
        st.warning(
            "No column left to use as a grouping variable. Deselect at least "
            "one predictor to free up a column."
        )
        st.stop()
    group_cols = st.multiselect(
        "Grouping variable(s) — each gets a random intercept",
        possible_groups,
        help=(
            "Pick one variable for a simple random intercept, or several for "
            "nested / crossed random intercepts. Typical choices are "
            "subject / animal / study / pen / site identifiers."
        ),
    )

    if len(group_cols) >= 2:
        re_structure = st.radio(
            "Random-effect structure",
            ["Nested", "Crossed"],
            horizontal=True,
            help=(
                "**Nested**: later factor is nested within the previous — "
                "e.g. `animal` within `study`. Pick the outer factor first.\n\n"
                "**Crossed**: each factor is independent — e.g. the same "
                "animals visit multiple GreenFeed units, and the same units "
                "are visited by multiple animals."
            ),
        )
        st.caption(
            f"Interpreting `{group_cols[0]}` as the primary factor"
            + (
                f"; `{', '.join(group_cols[1:])}` "
                + ("nested within it." if re_structure == "Nested" else "as crossed with it.")
            )
        )
    else:
        re_structure = "Single"

    slopes_allowed = re_structure != "Crossed"
    if slopes_allowed:
        slope_help = (
            f"Each chosen variable gets a random slope within every level of "
            f"`{group_cols[0]}` (the primary grouping factor)."
            if group_cols
            else "Each chosen variable gets a random slope within every group."
        )
        random_slopes = st.multiselect(
            "Random slopes (optional) — must be among the predictors",
            predictors,
            help=slope_help,
        )
    else:
        random_slopes = []
        st.caption(
            "Random slopes are disabled for crossed random effects (the primary "
            "group is artificial). Switch to **Nested** if you need slopes."
        )

    if not group_cols:
        st.info("Pick at least one grouping variable.")
        st.stop()

if not predictors:
    st.info("Pick at least one predictor to fit a model.")
    st.stop()

formula = build_formula(response, predictors, intercept=intercept)
re_formula = build_re_formula(random_slopes) if model_kind.startswith("Linear mixed") else None

st.code(formula, language="text")
if model_kind == "Linear mixed model (LMM)":
    re_note = re_formula or "~ 1"
    if len(group_cols) == 1:
        st.caption(f"Random effects: `{re_note}` grouped by `{group_cols[0]}`.")
    elif re_structure == "Nested":
        inner = ", ".join(group_cols[1:])
        st.caption(
            f"Random effects: `{re_note}` grouped by `{group_cols[0]}` "
            f"(outer) with nested intercepts for `{inner}`."
        )
    else:
        st.caption(
            f"Random effects: crossed random intercepts for "
            f"`{', '.join(group_cols)}`."
        )


# ---------------------------------------------------------------------------
# Section 3 — Fit & display
# ---------------------------------------------------------------------------

st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)
st.markdown("### 3 · Fit and inspect the model")

if st.button("Fit model", type="primary"):
    needed = [response] + predictors + list(group_cols)
    fit_df = df[needed].dropna().copy()
    if len(fit_df) < max(10, 2 * len(predictors)):
        st.warning(
            f"Only {len(fit_df)} complete rows after dropping missing values — "
            "the model may be unreliable."
        )
    for g in group_cols:
        n_g = int(fit_df[g].nunique(dropna=True))
        if n_g < 2:
            st.error(
                f"Grouping variable `{g}` has {n_g} level(s) after dropping "
                "missing values — a mixed model needs at least 2 groups. "
                "Pick a different grouping variable or use OLS."
            )
            st.stop()
        if n_g < 5:
            st.warning(
                f"Only {n_g} groups in `{g}` — mixed-model variance-component "
                "estimates can be unstable with so few groups. Interpret the "
                "random-effect estimates for this factor with caution."
            )
    with st.spinner(f"Fitting model on {len(fit_df):,} rows..."):
        try:
            if model_kind == "Linear regression (OLS)":
                model = fit_ols(fit_df, formula)
                kind = "OLS"
            else:
                nested_flag = (re_structure != "Crossed")
                model = fit_mixedlm_multi(
                    fit_df,
                    formula,
                    group_cols=group_cols,
                    nested=nested_flag,
                    re_formula=re_formula,
                )
                kind = "MixedLM"
        except Exception as e:
            st.error(f"Model fitting failed: {e}")
            st.stop()

    st.session_state["last_model_summary"] = summary_dict(model, kind)
    st.session_state["last_model_text"] = model.summary().as_text()
    st.session_state["last_model_coefs"] = coefficient_table(model)
    st.session_state["last_model_spec"] = {
        "kind": kind,
        "tables": selected_displays,
        "join_keys": join_keys,
        "formula": formula,
        "group_cols": group_cols,
        "re_structure": re_structure if model_kind.startswith("Linear mixed") else None,
        "re_formula": re_formula,
        "n_obs": int(len(fit_df)),
    }


summary = st.session_state.get("last_model_summary")
spec = st.session_state.get("last_model_spec")
model_text = st.session_state.get("last_model_text")
coefs_df = st.session_state.get("last_model_coefs")

if summary and spec:
    kind = spec["kind"]
    stats = summary.get("fit_statistics", {})

    st.markdown("#### Fit statistics")
    st.caption(
        "These are the headline goodness-of-fit numbers — R² and the "
        "F-statistic describe how much of y the model explains; AIC/BIC and "
        "log-likelihood are used to compare models (lower AIC/BIC = better)."
    )

    mcol1, mcol2, mcol3, mcol4 = st.columns(4)
    if kind == "OLS":
        mcol1.metric("R²", _fmt(stats.get("rsquared")))
        mcol2.metric("Adjusted R²", _fmt(stats.get("rsquared_adj")))
        mcol3.metric("F-statistic", _fmt(stats.get("fvalue")))
        mcol4.metric("F p-value", _fmt(stats.get("f_pvalue"), sig=True))
    else:
        mcol1.metric(
            "Pseudo-R² (marginal)",
            _fmt(stats.get("pseudo_r2_marginal")),
            help="Variance explained by fixed effects only (Nakagawa).",
        )
        mcol2.metric(
            "Pseudo-R² (conditional)",
            _fmt(stats.get("pseudo_r2_conditional")),
            help="Variance explained by fixed + random effects (Nakagawa).",
        )
        mcol3.metric("Log-likelihood", _fmt(stats.get("llf")))
        mcol4.metric("Scale (residual var.)", _fmt(stats.get("scale")))

    mcol5, mcol6, mcol7, mcol8 = st.columns(4)
    mcol5.metric("Observations", _fmt(stats.get("nobs"), as_int=True))
    mcol6.metric("Model df", _fmt(stats.get("df_model"), as_int=True))
    mcol7.metric("AIC", _fmt(stats.get("aic")))
    mcol8.metric("BIC", _fmt(stats.get("bic")))

    if kind == "MixedLM":
        vc_rows = summary.get("variance_components") or []
        if vc_rows:
            st.markdown("#### Variance components")
            st.caption(
                "Variance attributed to each random effect. `std_dev` is on "
                "the response's scale — compare it to the residual standard "
                "deviation to judge how much each factor matters."
            )
            vc_df = pd.DataFrame(vc_rows)
            for col in ("variance", "std_dev"):
                if col in vc_df.columns:
                    vc_df[col] = pd.to_numeric(vc_df[col], errors="coerce")
            st.dataframe(
                vc_df.style.format(
                    {"variance": "{:.4g}", "std_dev": "{:.4g}"}
                ),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("#### Coefficients")
    st.caption(
        "Estimate, standard error, t/z-statistic, p-value, and 95% "
        "confidence interval for every model term."
    )
    show_all = st.checkbox(
        "Show every level (including each category of a factor)",
        value=False,
        help="Many categorical levels can make this table huge. Off by "
        "default — then only the top rows by |t/z| are shown.",
    )

    coefs = coefs_df if isinstance(coefs_df, pd.DataFrame) else pd.DataFrame()
    if not coefs.empty:
        if show_all:
            st.dataframe(
                coefs.style.format(
                    {
                        "estimate": "{:.4g}",
                        "std_error": "{:.4g}",
                        "t_or_z": "{:.3f}",
                        "p_value": "{:.3g}",
                        "ci_low": "{:.4g}",
                        "ci_high": "{:.4g}",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            top = coefs.assign(
                _abs=coefs["t_or_z"].abs().fillna(0)
            ).sort_values("_abs", ascending=False).head(20).drop(columns="_abs")
            st.dataframe(
                top.style.format(
                    {
                        "estimate": "{:.4g}",
                        "std_error": "{:.4g}",
                        "t_or_z": "{:.3f}",
                        "p_value": "{:.3g}",
                        "ci_low": "{:.4g}",
                        "ci_high": "{:.4g}",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                f"Showing top 20 of {len(coefs)} terms by |t/z|. Check the box "
                "above to see every level."
            )

    with st.expander("Full statsmodels summary (textual)"):
        st.text(model_text or "(no summary)")

    st.markdown("#### AI interpretation")
    st.caption(
        "The AI receives the summary JSON (coefficients, p-values, fit "
        "statistics) — **not the raw data** — and writes a plain-English "
        "interpretation. statsmodels did all the math."
    )
    if st.button("Interpret this model"):
        context = (
            "Model specification:\n"
            + json.dumps(spec, indent=2)
            + "\n\nFitted results:\n"
            + json.dumps(summary, indent=2, default=str)
        )
        with st.spinner("Asking AI..."):
            try:
                st.markdown(interpret_model(context))
            except Exception as e:
                st.error(f"AI call failed: {e}")
