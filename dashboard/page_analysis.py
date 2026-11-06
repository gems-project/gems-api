"""Analysis page — GEMS data as built from the knowledge graph (kg_v1 analysis tables).

The cohort in the sidebar (facets with live counts, required measurements, dates, open data) feeds every
tab: Overview (what is in the cohort, when and how each study was measured), Model (a measurement by
treatment, optional outlier screening, a linear mixed model with random intercepts for study and animal
within study) and Export (the cohort recipe, and the cohort's tables for users on the API access list;
every download is logged). Every number comes from pandas/statsmodels in this process.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from dotenv import load_dotenv

_DASHBOARD_ROOT = Path(__file__).resolve().parent
load_dotenv(_DASHBOARD_ROOT / ".env", override=True)
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from gems_analysis import (  # noqa: E402
    CALCULATED,
    FACETS,
    HAS,
    RULES_DEFAULT,
    UNKNOWN,
    VISIT_PRESETS,
    active_visit_steps,
    animal_period_means,
    apply_daily_gases,
    box_stats,
    calculated_dictionary,
    calculated_labels,
    cohort_days,
    column_labels,
    covariate_slopes,
    facet_counts,
    facet_masks,
    load_dataset,
    load_visits,
    model_code,
    numeric_columns,
    plot_sample,
    records_needed,
    reliability_curve,
    repeatability,
    repeatability_frame,
    robust_outliers,
    source_label,
    study_overview,
    study_treatment_means,
    synthetic_dir,
    treatment_effects,
    unverified_numbers,
    variance_shares,
    visit_attrition,
    visit_rules_result,
    visit_step_label,
)
from gems_ai import interpret_model  # noqa: E402
from gems_auth import get_current_user_info, require_authorized_user  # noqa: E402
from gems_downloads import log_download  # noqa: E402
from gems_stats import (  # noqa: E402
    build_formula,
    coefficient_table,
    fit_mixedlm_multi,
    fit_statistics,
    variance_components,
)
from gems_ui import page_header, sidebar_user  # noqa: E402
from permissions import has_api_access  # noqa: E402

st.set_page_config(page_title="Analysis · GEMS", layout="wide", page_icon="🧪")
page_header(
    "Analysis",
    "Data built from the GEMS knowledge graph: choose a cohort in the sidebar, see what it holds, "
    "fit a mixed model, and export the cohort.",
)
# This page only: use the full window width and the compact look of the kg_map_claude workbench.
st.markdown("""<style>
  .block-container, [data-testid="stMainBlockContainer"] {
    max-width: none !important; padding-left: 2.2rem !important; padding-right: 2.2rem !important; }
  [data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.6rem; }
  .ga-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin: 2px 0 10px; }
  .ga-tile { border: 1px solid rgba(128,128,128,.25); border-radius: 10px; padding: 9px 12px; background: rgba(128,128,128,.04); }
  .ga-tile .l { font-size: 12px; opacity: .75; }
  .ga-tile .v { font-size: 22px; font-weight: 600; margin-top: 2px; line-height: 1.25; }
  .ga-tile .d { font-size: 12px; opacity: .6; }
  .ga-h { font-size: 15px; font-weight: 600; margin: 14px 0 0; }
  .ga-sub { font-size: 12.5px; opacity: .75; margin: 0 0 6px; }
</style>""", unsafe_allow_html=True)

# Chart colours from the kg_map_claude palette (categorical series, neutral "other", one-hue ramp).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
OTHER, EMPTY, CRITICAL = "#b4b2aa", "#e1e0d9", "#d03b3b"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
PRODUCTION_COLORS = {"Dairy": SERIES[0], "Beef": SERIES[1], UNKNOWN: OTHER}


def heading(title: str, sub: str = "") -> None:
    st.markdown(f'<div class="ga-h">{escape(title)}</div>' + (f'<div class="ga-sub">{sub}</div>' if sub else ""),
                unsafe_allow_html=True)


def tiles(items: list[tuple[str, str, str]]) -> None:
    """Compact number tiles: (label, value, detail)."""
    cells = "".join(f'<div class="ga-tile"><div class="l">{escape(l)}</div><div class="v">{escape(v)}</div>'
                    f'<div class="d">{escape(d)}</div></div>' for l, v, d in items)
    st.markdown(f'<div class="ga-tiles">{cells}</div>', unsafe_allow_html=True)


def _ci_text(low, high) -> str:
    return "one animal, no interval" if pd.isna(low) or pd.isna(high) else f"{low:.2f} to {high:.2f}"


def means_figure(means: pd.DataFrame, x_label: str) -> go.Figure:
    """Forest-plot style: one row per study and treatment, the mean over animals and its 95% CI.
    Treatment labels are study-specific, so each row names its own study and treatment."""
    m = means.sort_values(["studyId", "Treatment"], key=lambda s: s.astype(str)).reset_index(drop=True)
    ci = [_ci_text(lo, hi) for lo, hi in zip(m["ci_low"], m["ci_high"])]
    fig = go.Figure(go.Scatter(
        x=m["mean"], y=[m["studyId"].astype(str).tolist(), m["Treatment"].astype(str).tolist()], mode="markers",
        marker={"color": SERIES[0], "size": 7},
        error_x={"type": "data", "symmetric": False, "array": (m["ci_high"] - m["mean"]).tolist(),
                 "arrayminus": (m["mean"] - m["ci_low"]).tolist(), "thickness": 1.5, "width": 0, "color": SERIES[0]},
        customdata=list(zip(m["studyId"].astype(str), m["Treatment"].astype(str), m["animals"], m["records"], ci)),
        hovertemplate="Study %{customdata[0]} · %{customdata[1]}<br>Mean %{x:.2f}<br>95% CI %{customdata[4]}"
                      "<br>%{customdata[2]} animals · %{customdata[3]} records<extra></extra>"))
    fig.update_yaxes(autorange="reversed", title=None)
    fig.update_xaxes(title=x_label)
    return fig


def distribution_figure(stats_df: pd.DataFrame, y_label: str, production: dict) -> go.Figure:
    """One box per study, sorted by median and coloured by production type. Whiskers reach the most extreme
    values within 1.5 × IQR; the box statistics are computed in Python (see box_stats)."""
    s = stats_df.sort_values("median").reset_index(drop=True)
    prod = s["studyId"].astype(str).map(production).fillna(UNKNOWN)
    fig = go.Figure()
    for p in list(dict.fromkeys([*PRODUCTION_COLORS, *prod])):
        part = s[prod == p]
        if part.empty:
            continue
        fig.add_trace(go.Box(x=part["studyId"].astype(str), q1=part["q1"], median=part["median"], q3=part["q3"],
                             lowerfence=part["lowerfence"], upperfence=part["upperfence"], name=str(p),
                             marker_color=PRODUCTION_COLORS.get(p, OTHER), line={"width": 1}, boxpoints=False))
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=s["studyId"].astype(str).tolist(),
                     title=None)
    fig.update_yaxes(title=y_label)
    return fig


def covariate_figure(points: pd.DataFrame, x: str, y: str, slopes: dict, x_label: str, y_label: str) -> go.Figure:
    """Records (a sample, grey), each study's own line (thin) and the line through all records (bold)."""
    fig = go.Figure(go.Scatter(x=points[x], y=points[y], mode="markers", name="Records (sample)",
                               marker={"color": OTHER, "size": 4, "opacity": 0.45}, hoverinfo="skip"))
    lines = slopes["lines"]
    if len(lines):
        xs, ys = [], []
        for r in lines.itertuples():
            xs += [r.x0, r.x1, None]
            ys += [r.intercept + r.slope * r.x0, r.intercept + r.slope * r.x1, None]
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="Each study's line", hoverinfo="skip",
                                 line={"color": SERIES[0], "width": 1}, opacity=0.7))
    if not np.isnan(slopes["overall"]):
        x0, x1, a, b = slopes["xmin"], slopes["xmax"], slopes["intercept"], slopes["overall"]
        fig.add_trace(go.Scatter(x=[x0, x1], y=[a + b * x0, a + b * x1], mode="lines", name="All records pooled",
                                 line={"color": SERIES[1], "width": 3}, hoverinfo="skip"))
    fig.update_xaxes(title=x_label)
    fig.update_yaxes(title=y_label)
    return fig


def effects_figure(effects: pd.DataFrame, x_label: str) -> go.Figure:
    """Each treatment's estimated difference from the reference with its 95% CI; dashed line = no difference."""
    ci = [_ci_text(lo, hi) for lo, hi in zip(effects["ci_low"], effects["ci_high"])]
    fig = go.Figure(go.Scatter(
        x=effects["estimate"], y=effects["treatment"].astype(str), mode="markers",
        marker={"color": SERIES[1], "size": 9, "symbol": "square"},
        error_x={"type": "data", "symmetric": False, "array": (effects["ci_high"] - effects["estimate"]).tolist(),
                 "arrayminus": (effects["estimate"] - effects["ci_low"]).tolist(), "thickness": 2, "width": 0,
                 "color": SERIES[1]},
        customdata=ci, hovertemplate="%{y}: %{x:.2f}<br>95% CI %{customdata}<extra></extra>"))
    fig.add_vline(x=0, line_dash="dash", line_color=OTHER)
    values = pd.concat([effects["estimate"], effects["ci_low"], effects["ci_high"]]).dropna()
    lo, hi = min(0.0, float(values.min())), max(0.0, float(values.max()))  # always show the zero line with room
    pad = (hi - lo) * 0.08 or 1.0
    fig.update_yaxes(type="category", autorange="reversed", title=None)
    fig.update_xaxes(title=x_label, range=[lo - pad, hi + pad])
    return fig


def variance_figure(shares: pd.DataFrame) -> go.Figure:
    """One stacked bar: how the total variation splits between the random effects and the residual."""
    fills, inks = [SEQ[5], SEQ[3], SEQ[1], SEQ[0]], ["#ffffff", "#ffffff", SEQ[6], SEQ[6]]
    fig = go.Figure()
    for i, r in enumerate(shares.itertuples()):
        fig.add_bar(x=[r.share], y=[""], orientation="h", name=f"{r.name} ({r.share:.0f}%)",
                    marker_color=fills[i % 4], text=[f"{r.share:.0f}%"], textposition="inside",
                    insidetextanchor="middle", textfont={"color": inks[i % 4]},
                    hovertemplate=f"{r.name}: %{{x:.1f}}% of the variance<extra></extra>")
    fig.update_layout(barmode="stack", legend={"traceorder": "normal"})  # legend in the bar's left-to-right order
    fig.update_xaxes(range=[0, 100], ticksuffix="%", title=None)
    fig.update_yaxes(visible=False)
    return fig


@st.cache_data(max_entries=8, show_spinner=False)
def fit_results(model_data: pd.DataFrame, formula: str, group_cols: tuple, reference: str) -> dict:
    """Fit once per model and data: redraws, tab switches and refits of the same model reuse the result.
    `reference` is part of the cache key because it sets how the treatments are coded."""
    model = fit_mixedlm_multi(model_data, formula, list(group_cols), nested=True)
    return {"stats": fit_statistics(model, "mixed"), "converged": bool(getattr(model, "converged", False)),
            "coefs": coefficient_table(model), "variances": variance_components(model)}


# px.timeline is slow-ish; kept per cohort (st.cache_data returns a copy, so show() may edit it).
@st.cache_data(max_entries=16, show_spinner=False)
def timeline_figure(tl: pd.DataFrame) -> go.Figure:
    fig = px.timeline(tl.sort_values("Start"), x_start="Start", x_end="End", y="Study", color="Type",
                      color_discrete_map=PRODUCTION_COLORS,
                      hover_data={"Animals": True, "Animal-days": True, "Location": True})
    fig.update_yaxes(autorange="reversed", title=None, type="category")
    return fig


def show(fig: go.Figure, height: int, top: int = 28, legend_y: float = 1.0) -> None:
    """Common layout; panels with their own titles need a larger `top` and a higher `legend_y`."""
    fig.update_layout(height=height, margin={"l": 8, "r": 8, "t": top, "b": 8}, font={"size": 12},
                      legend={"orientation": "h", "y": legend_y, "yanchor": "bottom", "x": 0, "title_text": ""})
    st.plotly_chart(fig, use_container_width=True)


user = require_authorized_user()
sidebar_user(user)
user_info = get_current_user_info()

source = source_label()
if synthetic_dir():
    st.warning("Synthetic test data: every value is invented. This is not GEMS data.", icon="🧪")
if st.sidebar.button("Reload data", help="Load the kg_v1 tables again, e.g. after the KG job has run."):
    load_dataset.clear()
    load_visits.clear()
    visit_rules_result.clear()
    st.rerun()

try:
    # Loaded and prepared once, then shared by every user and click (see load_dataset). Never change
    # these frames in place.
    dataset = load_dataset(source)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not load the analysis tables from {source}: {exc}")
    st.stop()
st.sidebar.caption(f"Source: {source} · loaded {dataset['loadedAt']:%Y-%m-%d %H:%M} UTC")

tables = dataset["tables"]
studies, animals_t, days, dictionary = tables["kgStudy"], tables["kgAnimal"], tables["kgAnimalDay"], tables["kgDataDictionary"]
if days.empty:
    st.info("There are no analysis tables yet. They appear after the KG job has run.")
    st.stop()

# ---------------------------------------------------------------------------
# Cohort (sidebar): feeds every tab
# ---------------------------------------------------------------------------
animals, additives = dataset["animals"], dataset["additives"]

st.sidebar.markdown("### Cohort")
st.sidebar.caption("Every tab uses this selection. The number after each choice counts the animals "
                   "that also match your other choices.")
PRESETS = {
    "All with CH4": ({}, ["CH4"]),
    "Dairy · CH4 + DMI + milk": ({"production": ["Dairy"]}, ["CH4", "Dry matter intake", "Milk yield"]),
    "Beef · CH4 + DMI": ({"production": ["Beef"]}, ["CH4", "Dry matter intake"]),
}


def _clear_cohort() -> None:
    for k in [f"facet_{f}" for f in FACETS] + ["cohort_has", "cohort_access", "cohort_dates"]:
        st.session_state.pop(k, None)


def _apply_preset() -> None:
    name = st.session_state.get("cohort_preset")
    if not name:
        return
    facets, needs = PRESETS[name]
    _clear_cohort()
    for key, vals in facets.items():
        present = set(animals[FACETS[key][1]])
        st.session_state[f"facet_{key}"] = [v for v in vals if v in present]
    st.session_state["cohort_has"] = needs
    st.session_state["cohort_preset"] = None


st.sidebar.pills("Quick start", list(PRESETS), key="cohort_preset", on_change=_apply_preset)
st.sidebar.button("Clear choices", on_click=_clear_cohort)
if "cohort_access" not in st.session_state:
    st.session_state["cohort_access"] = "All"
access = st.sidebar.segmented_control("Data access", ["All", "Open", "Not open"], key="cohort_access",
                                      help="Open: studies the contributors agreed to share openly (DUA summary).") or "All"
if "cohort_has" not in st.session_state:
    st.session_state["cohort_has"] = ["CH4"]
has = st.sidebar.multiselect("Must have", list(HAS), key="cohort_has",
                             help="Keep animals with at least one day of each of these measurements.")
base = pd.Series(True, index=animals.index)
for label in has:
    base &= animals[label] > 0
if access != "All":
    base &= animals["isOpenData"] == (access == "Open")

chosen = {k: list(st.session_state.get(f"facet_{k}", [])) for k in FACETS}
masks = facet_masks(animals, additives, chosen)
counts = facet_counts(animals, additives, masks, base)
more = st.sidebar.expander("More filters", expanded=any(chosen[k] for k in ("breed", "location", "additive")))
for key, (label, col) in FACETS.items():
    values = sorted(set(additives[col]) if key == "additive" else set(animals[col]), key=str)
    n = counts[key]
    where = more if key in ("breed", "location", "additive") else st.sidebar
    where.multiselect(label, values, key=f"facet_{key}",
                      format_func=lambda v, n=n: f"{v} ({int(n.get(v, 0)):,})")

day_min, day_max = days["Date"].min().date(), days["Date"].max().date()
dates = st.sidebar.date_input("Dates", value=(day_min, day_max), min_value=day_min, max_value=day_max,
                              key="cohort_dates")
dates = tuple(dates) if isinstance(dates, (tuple, list)) else (dates,)
date_from = dates[0] if dates and dates[0] != day_min else None
date_to = dates[1] if len(dates) > 1 and dates[1] != day_max else None

ok = base.copy()
for m in masks.values():
    ok &= m
# The days already carry the calculated variables (methane yield, FPCM, ...; see load_dataset).
cdays = cohort_days(days, set(animals.loc[ok, "animal_id"]), date_from, date_to, chosen["additive"])
cohort_studies = sorted(cdays["studyId"].astype(str).unique())
cohort_animals = set(cdays["animal_id"])
st.sidebar.markdown(f"**{len(cohort_studies)}** studies · **{len(cohort_animals):,}** animals · "
                    f"**{len(cdays):,}** animal-days")

# GreenFeed visit rules (set in the QC tab) decide which visits make each day's CH4, for every tab.
QC_KEYS = {rule: f"qc_{rule}" for rule in RULES_DEFAULT}
visit_rules = {rule: st.session_state.get(key, RULES_DEFAULT[rule]) for rule, key in QC_KEYS.items()}
rules_on = bool(active_visit_steps(visit_rules))
preset_name = st.session_state.get("qc_preset_name", next(iter(VISIT_PRESETS)))
rules_edited = visit_rules != {**RULES_DEFAULT, **VISIT_PRESETS[preset_name][0]}
if rules_on and not cdays.empty:
    try:
        rules_result = visit_rules_result(source, tuple(sorted(visit_rules.items())))
        cdays = cdays.copy(deep=False)  # the cohort's own frame; the shared cached days stay as they are
        apply_daily_gases(cdays, rules_result["daily"])
    except Exception as exc:  # noqa: BLE001
        st.sidebar.error(f"The GreenFeed visit rules could not be applied: {exc}")
st.sidebar.caption(f"GreenFeed visit rules: **{preset_name}**" + (" (edited)" if rules_edited else "")
                   + ". Change them in the GreenFeed QC tab.")

recipe = {
    "source": source,
    "made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "dataAccess": access,
    "mustHave": {label: HAS[label] for label in has},
    "facets": {FACETS[k][1]: v for k, v in chosen.items() if v},
    "dateFrom": str(date_from) if date_from else None,
    "dateTo": str(date_to) if date_to else None,
    "result": {"studies": cohort_studies, "animals": len(cohort_animals), "animalDays": len(cdays)},
    "visitRules": {"preset": preset_name, "edited": rules_edited, **visit_rules},
    "calculatedColumns":{c: f"{how} [{unit}]" for c, (_, unit, how, _) in CALCULATED.items()
                          if c in cdays and cdays[c].notna().any()},
}

if cdays.empty:
    st.info("No animal-days match this cohort. Loosen the choices in the sidebar.")
    st.stop()

# Only the open tab runs (a closed tab updates when it is opened; every tab uses the same cohort).
# Streamlit forgets the value of a widget that is not drawn, so the tabs' widget values are saved again
# here: your choices are still there when you come back to a tab.
TAB_WIDGETS = ["ov_study", "m_response", "m_level", "m_covariates", "m_screen", "m_screen_cols", "m_within",
               "m_threshold", "m_reference", "qc_study", "qc_animals", *QC_KEYS.values(),
               "lab_unit", "lab_value", "lab_target"]
for _k in TAB_WIDGETS:
    if _k in st.session_state:
        st.session_state[_k] = st.session_state[_k]


def keep(key: str, default, options=None, multi: bool = False) -> None:
    """Start value of a tab widget, kept valid for the current options (the widget then gets no default)."""
    if key not in st.session_state:
        st.session_state[key] = default
    elif options is not None:
        current = st.session_state[key]
        if multi:
            st.session_state[key] = [v for v in current if v in options]
        elif current not in options:
            st.session_state[key] = default


tab_overview, tab_qc, tab_lab, tab_model, tab_export = st.tabs(
    ["Overview", "GreenFeed QC", "Standardisation lab", "Model", "Export"], key="analysis_tab", on_change="rerun")


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def render_overview() -> None:
    ch4_days = int(cdays["CH4_g_d"].notna().sum()) if "CH4_g_d" in cdays else 0
    visits = int(cdays["nVisits"].sum()) if "nVisits" in cdays else 0
    n_open = int(studies.loc[studies["studyId"].astype(str).isin(cohort_studies), "isOpenData"].sum()) \
        if "isOpenData" in studies else 0
    tiles([
        ("Studies", f"{len(cohort_studies)}", f"of {studies['studyId'].nunique()} · {n_open} open data"),
        ("Animals", f"{len(cohort_animals):,}", f"of {len(animals):,}"),
        ("Animal-days", f"{len(cdays):,}", "any measurement or design record"),
        ("CH4 days", f"{ch4_days:,}", "days with a GreenFeed CH4 mean"),
        ("GreenFeed visits", f"{visits:,}", "kept by the visit rules" if rules_on else "matched to animals, CH4 > 0"),
        ("Date span", f"{cdays['Date'].min():%Y-%m} → {cdays['Date'].max():%Y-%m}", ""),
    ])

    heading("Studies in the cohort", "Design is inferred from the data: <i>within-animal</i> when animals changed "
            "treatment (crossover or switchback), <i>between-animal</i> when each kept one treatment.")
    ov = study_overview(cdays, studies)
    table = ov.assign(**{"Open data": ov["Open data"].map({True: "Open", False: "Not open"})}) if "Open data" in ov else ov
    st.dataframe(table, hide_index=True, use_container_width=True, height=min(420, 35 * len(ov) + 38))

    left, right = st.columns(2, gap="medium")
    with left:
        heading("When the studies ran", "One bar per study, first to last record in the cohort.")
        tl = ov.assign(Start=pd.to_datetime(ov["First"]), End=pd.to_datetime(ov["Last"]) + pd.Timedelta(days=1))
        show(timeline_figure(tl), max(160, 22 * len(tl) + 80))
    with right:
        heading("Coverage: animals × days", "GreenFeed visits per animal and day. Hover for the treatment.")
        by_ch4 = ov.sort_values("CH4 days", ascending=False)["Study"].tolist()
        keep("ov_study", by_ch4[0], by_ch4)
        study = st.selectbox("Study", by_ch4, key="ov_study", help="Studies ordered by CH4 days in the cohort.",
                             label_visibility="collapsed")
        sub = cdays[cdays["studyId"].astype(str) == str(study)]
        label_col = "AnimalIdentifier" if "AnimalIdentifier" in sub and sub["AnimalIdentifier"].notna().any() else "animal_id"
        sub = sub.assign(_animal=sub[label_col].astype(str),
                         _visits=sub["nVisits"].fillna(0) if "nVisits" in sub else 0,
                         _trt=sub["Treatment"].fillna("–").astype(str) if "Treatment" in sub else "–")
        z = sub.pivot_table(index="_animal", columns="Date", values="_visits", aggfunc="sum")
        trt = sub.pivot_table(index="_animal", columns="Date", values="_trt", aggfunc="first").reindex_like(z)
        # grey = a day with other records but no visit; 1 → 6+ visits on the one-hue ramp; blank = no record
        scale = [[0, EMPTY], [0.5 / 6, EMPTY], [0.5 / 6, SEQ[0]]] + [[k / 6, SEQ[k]] for k in range(1, 7)]
        heat = go.Figure(go.Heatmap(
            z=z.values, x=z.columns, y=z.index, customdata=trt.values, colorscale=scale, zmin=0, zmax=6,
            colorbar={"title": "Visits", "tickvals": [0, 2, 4, 6], "ticktext": ["0", "2", "4", "6+"], "thickness": 12},
            xgap=1, ygap=1,
            hovertemplate="Animal %{y}<br>%{x|%Y-%m-%d}<br>Visits: %{z}<br>Treatment: %{customdata}<extra></extra>"))
        heat.update_yaxes(type="category", title=None, autorange="reversed")
        show(heat, min(900, max(260, 16 * len(z) + 70)))


# ---------------------------------------------------------------------------
# GreenFeed QC: the visit rules, what they remove, and each animal's CH4 record
# ---------------------------------------------------------------------------
REASON_COLORS = [SERIES[1], SERIES[3], SERIES[4], SERIES[6], SERIES[7], SERIES[2], SERIES[5], OTHER]


def _apply_visit_preset() -> None:
    name = st.session_state.get("qc_preset_pick")
    if not name:
        return
    for rule, value in {**RULES_DEFAULT, **VISIT_PRESETS[name][0]}.items():
        st.session_state[f"qc_{rule}"] = value
    st.session_state["qc_preset_name"] = name
    st.session_state["qc_preset_pick"] = None


def attrition_figure(att: pd.DataFrame) -> go.Figure:
    """Visits left after each rule, the starting count at the top."""
    text = [f"{v:,}" + (f"  (−{r:,})" if r else "") for v, r in zip(att["visits"], att["removed"])]
    fig = go.Figure(go.Bar(x=att["visits"], y=att["step"], orientation="h", text=text, textposition="auto",
                           marker_color=[OTHER] + [SERIES[0]] * (len(att) - 1),
                           hovertemplate="%{y}<br>%{x:,} visits left<extra></extra>"))
    fig.update_yaxes(autorange="reversed", title=None, type="category")
    fig.update_xaxes(title="Visits left")
    return fig


def record_figure(rec: pd.DataFrame, daily_rec: pd.DataFrame, order: list[str], reasons: list[str]) -> go.Figure:
    """One panel per animal: each visit's CH4 over time, coloured kept / by the first rule that removed it,
    and the daily mean of the kept visits as a line."""
    cols = min(3, len(order))
    rows = int(np.ceil(len(order) / cols))
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=order, horizontal_spacing=0.06,
                        vertical_spacing=min(0.12, 0.5 / rows))
    colors = {"Kept": SERIES[0], **{r: REASON_COLORS[i % len(REASON_COLORS)] for i, r in enumerate(x for x in reasons if x != "Kept")}}
    shown: set[str] = set()
    for i, animal in enumerate(order):
        r, c = i // cols + 1, i % cols + 1
        part = rec[rec["animal"] == animal]
        for reason in reasons:
            sub = part[part["reason"] == reason]
            if sub.empty:
                continue
            fig.add_trace(go.Scatter(x=sub["start"], y=sub["CH4_g_d"], mode="markers", name=reason, legendgroup=reason,
                                     showlegend=reason not in shown,
                                     marker={"color": colors[reason], "size": 5, "opacity": 0.8 if reason == "Kept" else 0.6},
                                     hovertemplate="%{x|%Y-%m-%d %H:%M}<br>%{y:.0f} g/d<extra>" + reason + "</extra>"),
                          row=r, col=c)
            shown.add(reason)
        d = daily_rec[daily_rec["animal"] == animal].sort_values("Date")
        if not d.empty:
            fig.add_trace(go.Scatter(x=d["Date"] + pd.Timedelta(hours=12), y=d["CH4_g_d"], mode="lines",
                                     name="Daily mean of kept visits", legendgroup="daily",
                                     showlegend="daily" not in shown, line={"color": SEQ[6], "width": 1.5},
                                     hovertemplate="%{x|%Y-%m-%d}: %{y:.0f} g/d<extra>daily mean</extra>"),
                          row=r, col=c)
            shown.add("daily")
    fig.update_yaxes(title_text="CH4 (g/d)", col=1)
    return fig


def render_qc() -> None:
    heading("GreenFeed visit rules", "Which GreenFeed visits count when each day's CH4 is calculated. The rules "
            "apply to every tab: the Overview counts, the Model tab, the calculated variables (methane yield, Ym, …) "
            "and the Export all use the daily CH4 they give. With no rules, daily CH4 is the mean of every visit "
            "matched to the animal.")
    st.pills("Start from", list(VISIT_PRESETS), key="qc_preset_pick", on_change=_apply_visit_preset)
    st.caption(f"**{preset_name}**" + (" (edited below)" if rules_edited else "") + ": " + VISIT_PRESETS[preset_name][1])
    c1, c2, c3 = st.columns(3, gap="large")
    with c1:
        st.markdown("**Each visit**")
        keep("qc_exclude_phases", False)
        st.toggle("Leave out adaptation, transition and washout days", key="qc_exclude_phases",
                  help="Uses each day's experimental phase as the contributor recorded it.")
        keep("qc_min_minutes", 0.0)
        st.number_input("Minimum good-data time (min)", min_value=0.0, max_value=15.0, step=0.5, key="qc_min_minutes",
                        help="0 = off. C-Lock and the Global Research Alliance SOPs ask for at least 2 minutes.")
        keep("qc_drop_interrupted", False)
        st.toggle("Leave out interrupted visits", key="qc_drop_interrupted",
                  help="Visits whose interrupted flag cannot be read are kept.")
        keep("qc_min_airflow", 0.0)
        st.number_input("Minimum airflow (L/s)", min_value=0.0, max_value=200.0, step=1.0, key="qc_min_airflow",
                        help="0 = off. Beck et al. (2024) left out visits below 26 L/s. Visits without airflow are kept.")
        keep("qc_max_ch4", 0.0)
        st.number_input("Maximum CH4 (g/d)", min_value=0.0, max_value=3000.0, step=50.0, key="qc_max_ch4",
                        help="0 = off.")
    with c2:
        st.markdown("**Each animal's visits**")
        keep("qc_mad_k", 0.0)
        st.number_input("Outliers: robust z above", min_value=0.0, max_value=10.0, step=0.5, key="qc_mad_k",
                        help="0 = off. Per animal: |CH4 − median| / (1.4826 × MAD) over the visits still kept; "
                             "animals with fewer than 5 visits are not screened.")
        keep("qc_min_visits_day", 1)
        st.number_input("Minimum visits per day", min_value=1, max_value=20, step=1, key="qc_min_visits_day")
    with c3:
        st.markdown("**Each animal and period**")
        keep("qc_min_days", 0)
        st.number_input("Minimum days with visits", min_value=0, max_value=100, step=1, key="qc_min_days",
                        help="Per animal, treatment and period. 0 = off.")
        keep("qc_min_visits", 0)
        st.number_input("Minimum visits", min_value=0, max_value=500, step=1, key="qc_min_visits",
                        help="Per animal, treatment and period. 0 = off. Beck et al. (2024): 30 visits of 3 min or more.")
        keep("qc_min_bins", 0)
        st.number_input("Minimum 3-hour periods of the day covered (of 8)", min_value=0, max_value=8, step=1,
                        key="qc_min_bins", help="Spreads the visits over the day, since CH4 changes with time since "
                                                "feeding. 0 = off.")

    try:
        visits = load_visits(source)
        result = visit_rules_result(source, tuple(sorted(visit_rules.items())))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not load the GreenFeed visits from {source}: {exc}")
        return
    in_cohort = visits["animal_id"].isin(cohort_animals)
    if date_from is not None:
        in_cohort &= visits["Date"] >= pd.Timestamp(date_from)
    if date_to is not None:
        in_cohort &= visits["Date"] <= pd.Timestamp(date_to)
    in_cohort = in_cohort.to_numpy()
    if not in_cohort.any():
        st.info("The cohort has no GreenFeed visits matched to its animals.")
        return
    att = visit_attrition(visits, result["status"], visit_rules, in_cohort)
    first, last = att.iloc[0], att.iloc[-1]
    tiles([
        ("Visits kept", f"{last['visits']:,}", f"of {first['visits']:,} ({last['visits'] / first['visits']:.0%})"),
        ("Animal-days with CH4", f"{last['animal_days']:,}", f"of {first['animal_days']:,}"),
        ("Animals with CH4", f"{last['animals']:,}", f"of {first['animals']:,}"),
    ])
    heading("What each rule removes", "Visits left after each rule, in the order the rules are applied; the "
            "cohort's visits only. Rules for days and for animal-periods remove all of a day's or period's visits.")
    show(attrition_figure(att), 70 + 40 * len(att))
    with st.expander("Attrition table"):
        st.dataframe(att.rename(columns={"step": "Step", "visits": "Visits left", "removed": "Removed",
                                         "animal_days": "Animal-days left", "animals": "Animals left"}),
                     hide_index=True, use_container_width=True)

    heading("CH4 record by animal", "Each visit's CH4 over time: blue visits are kept, other colours show the "
            "first rule that removed the visit. The dark line is the daily mean of the kept visits.")
    studies_with_visits = sorted(pd.unique(visits.loc[in_cohort, "studyId"]), key=str)
    s1, s2 = st.columns([1, 3], gap="medium")
    keep("qc_study", studies_with_visits[0], studies_with_visits)
    study = s1.selectbox("Study", studies_with_visits, key="qc_study")
    in_study = in_cohort & (visits["studyId"] == study).to_numpy()
    by_visits = visits.loc[in_study, "animal_id"].value_counts()
    names = animals_t.set_index("animal_id")["AnimalIdentifier"].astype(str).to_dict() \
        if {"animal_id", "AnimalIdentifier"} <= set(animals_t.columns) else {}
    options = by_visits.index.tolist()
    keep("qc_animals", options[:3], options, multi=True)
    chosen = s2.multiselect("Animals (most visits first; up to 6)", options, key="qc_animals", max_selections=6,
                            format_func=lambda a: f"{names.get(a, a)} ({by_visits[a]:,} visits)")
    if not chosen:
        st.caption("Pick one or more animals to see their visits.")
        return
    label = {a: names.get(a, str(a)) for a in chosen}
    if len(set(label.values())) < len(label):  # the same identifier twice: add the KG id's end to tell them apart
        label = {a: f"{names.get(a, a)} ({str(a)[-6:]})" for a in chosen}
    rows = np.flatnonzero(in_study & visits["animal_id"].isin(chosen).to_numpy())
    reason_names = {code: visit_step_label(code, visit_rules) for code in [0] + active_visit_steps(visit_rules)}
    rec = visits.iloc[rows][["animal_id", "start", "CH4_g_d"]].assign(
        reason=[reason_names[int(s)] for s in result["status"][rows]])
    rec["animal"] = rec["animal_id"].map(label)
    daily_rec = result["daily"].reset_index()
    daily_rec = daily_rec[daily_rec["animal_id"].isin(chosen)]
    if date_from is not None:
        daily_rec = daily_rec[daily_rec["Date"] >= pd.Timestamp(date_from)]
    if date_to is not None:
        daily_rec = daily_rec[daily_rec["Date"] <= pd.Timestamp(date_to)]
    daily_rec = daily_rec.assign(animal=daily_rec["animal_id"].map(label))
    order = [label[a] for a in chosen]
    n_rows = int(np.ceil(len(order) / 3))
    show(record_figure(rec, daily_rec, order, list(reason_names.values())), 280 * n_rows + 110, top=80,
         legend_y=1 + 55 / (280 * n_rows))  # legend above the animals' titles


# ---------------------------------------------------------------------------
# Standardisation lab: how many days (or visits) an animal needs for a reliable mean
# ---------------------------------------------------------------------------
def lab_figure(per_study: pd.DataFrame, pooled_r: float, target: float, k_max: int, unit: str) -> go.Figure:
    """Reliability of an animal's mean against the number of records: each study (thin) and pooled (bold)."""
    k = np.arange(1, k_max + 1)
    fig = go.Figure()
    xs, ys = [], []
    for r in per_study["r"].dropna():
        xs += [*k, None]
        ys += [*reliability_curve(r, k), None]
    if xs:
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", name="Each study", hoverinfo="skip",
                                 line={"color": SERIES[0], "width": 1}, opacity=0.45))
    if pooled_r is not None and not np.isnan(pooled_r):
        fig.add_trace(go.Scatter(x=k, y=reliability_curve(pooled_r, k), mode="lines", name="All studies pooled",
                                 line={"color": SERIES[1], "width": 3},
                                 hovertemplate="%{x} " + unit.lower() + ": %{y:.2f}<extra>pooled</extra>"))
        need = records_needed(pooled_r, target)
        if np.isfinite(need) and need <= k_max:
            fig.add_vline(x=need, line_dash="dot", line_color=SERIES[1])
    fig.add_hline(y=target, line_dash="dash", line_color=OTHER)
    fig.update_xaxes(title=f"{unit} per animal and period", range=[1, k_max])
    fig.update_yaxes(title="Reliability of the animal's mean", range=[0, 1.02])
    return fig


def render_lab() -> None:
    heading("How many GreenFeed days does an animal need?",
            "How reliable an animal's average becomes as days (or visits) are added. Uses the values after your "
            "GreenFeed visit rules, within each study, after removing treatment and period means, so only "
            "differences between animals count as signal.")
    labels = {**column_labels(dictionary, "kgAnimalDay"), **calculated_labels()}
    c1, c2, c3 = st.columns([1, 2, 1], vertical_alignment="bottom")
    keep("lab_unit", "Days")
    unit = c1.segmented_control("Count", ["Days", "Visits"], key="lab_unit") or "Days"
    gases = ["CH4_g_d", "CO2_g_d", "O2_g_d", "H2_g_d"]
    options = numeric_columns(cdays) if unit == "Days" else [g for g in gases]
    if not options:
        st.info("The cohort has no numeric measurements.")
        return
    keep("lab_value", "CH4_g_d" if "CH4_g_d" in options else options[0], options)
    value = c2.selectbox("Measurement", options, key="lab_value", format_func=lambda c: labels.get(c, c))
    keep("lab_target", 0.9)
    target = c3.segmented_control("Target reliability", [0.8, 0.9, 0.95], key="lab_target",
                                  format_func=lambda x: f"{x:.2f}") or 0.9

    if unit == "Days":
        frame = repeatability_frame(cdays, value)
    else:
        try:
            visits = load_visits(source)
            status = visit_rules_result(source, tuple(sorted(visit_rules.items())))["status"]
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not load the GreenFeed visits from {source}: {exc}")
            return
        mask = (status == 0) & visits["animal_id"].isin(cohort_animals).to_numpy()
        if date_from is not None:
            mask &= (visits["Date"] >= pd.Timestamp(date_from)).to_numpy()
        if date_to is not None:
            mask &= (visits["Date"] <= pd.Timestamp(date_to)).to_numpy()
        vk = visits[mask]
        frame = pd.DataFrame({"studyId": vk["studyId"], "animal_id": vk["animal_id"], value: vk[value],
                              "group": vk["_group"], "segment": vk["_segment"]})
    per = repeatability(frame, value)
    pooled = repeatability(frame.assign(studyId="All studies"), value)
    if per.empty or pooled.empty:
        st.info("Not enough records in the cohort for this measurement.")
        return
    pooled_r = float(pooled["r"].iloc[0])
    per["needed"] = [records_needed(r, target) for r in per["r"]]
    finite = per["needed"].replace(np.inf, np.nan).dropna()
    word = unit.lower()
    need_pooled = records_needed(pooled_r, target)
    tiles([
        (f"Repeatability of one {word[:-1]}", "—" if np.isnan(pooled_r) else f"{pooled_r:.2f}",
         "share of the variation that is between animals"),
        (f"{unit} needed for {target:.2f}", "not reached" if not np.isfinite(need_pooled) else f"{need_pooled:.0f}",
         "all studies pooled"),
        (f"Median {word} needed per study", "—" if finite.empty else f"{finite.median():.0f}",
         f"{len(finite)} of {len(per)} studies with an estimate"),
    ])
    k_max = int(min(200, max(30, np.nanpercentile(finite, 90) * 1.5 if len(finite) else 30,
                             need_pooled * 1.5 if np.isfinite(need_pooled) else 30)))
    heading("Reliability of an animal's mean", f"Thin lines: each study. Bold: all studies pooled; the dotted line "
            f"marks where it reaches the target (dashed). R(k) = k·r / (1 + (k − 1)·r), Spearman–Brown.")
    show(lab_figure(per, pooled_r, target, k_max, unit), 380)
    heading("By study", f"{unit} needed = target/(1 − target) × (1 − r)/r. \"Enough now?\" compares it with the study's "
            f"average {word} per animal and period.")
    table = per.assign(enough=np.where(per["records_per_period"] >= per["needed"], "Yes", "No")).sort_values("needed")
    table = table.rename(columns={
        "studyId": "Study", "animals": "Animals", "animal_periods": "Animal-periods", "records": unit,
        "records_per_period": f"{unit} per animal-period", "sd_between": "SD between animals",
        "sd_within": "SD within animal", "r": f"Repeatability (one {word[:-1]})", "needed": f"{unit} needed",
        "enough": "Enough now?"})
    st.dataframe(table.replace(np.inf, np.nan).round(2), hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def render_model() -> None:
    labels = {**column_labels(dictionary, "kgAnimalDay"), **calculated_labels()}
    data = cdays
    values = numeric_columns(data)
    if not values:
        st.info("The cohort has no numeric measurements.")
        return
    c1, c2, c3, c4 = st.columns([3, 2, 3, 2], vertical_alignment="bottom")
    keep("m_response", "CH4_g_d" if "CH4_g_d" in values else values[0], values)
    response = c1.selectbox("Measurement", values, key="m_response", format_func=lambda c: labels.get(c, c))
    keep("m_level", "Animal-period mean")
    level = c2.radio("Level", ["Animal-period mean", "Animal-day"], key="m_level", horizontal=True,
                     help="Animal-period mean: one value per animal, treatment and period, the usual unit for "
                          "comparing treatments; the model fits in seconds. Animal-day: every daily record; the "
                          "model can take minutes on a large cohort.")
    covariate_options = [v for v in values if v != response]
    keep("m_covariates", [], covariate_options, multi=True)
    covariates = c3.multiselect("Covariates (optional)", covariate_options, key="m_covariates",
                                format_func=lambda c: labels.get(c, c))
    keep("m_screen", False)
    screen = c4.toggle("Remove outliers", key="m_screen",
                       help="Off by default. Values outside the plausible range are original source data; "
                            "decide here whether to screen them, and see what is removed.")
    for c in [v for v in [response] + covariates if v in CALCULATED]:
        label, unit, how, note = CALCULATED[c]
        st.caption(f"**{label}** ({unit}) is calculated in this page for each cow and day: {how}. "
                   f"Missing when an input is missing that day. {note}")
    used = [response] + covariates
    ids = [c for c in ["studyId", "animal_id", "AnimalIdentifier", "Treatment", "Period"] if c in data.columns]
    data = data[ids + used]  # only the columns needed: dropping blanks on all ~60 columns was slow
    if level == "Animal-period mean":
        data = animal_period_means(data, used)
    data = data.dropna(subset=used + ["Treatment"]).reset_index(drop=True)
    if data.empty:
        st.info("No records have all the chosen measurements and a treatment.")
        return

    removed = pd.Series(False, index=data.index)
    if screen:
        heading("Outlier screening")
        s1, s2, s3 = st.columns([3, 2, 1])
        keep("m_screen_cols", [response], used, multi=True)
        screen_cols = s1.multiselect("Screen these measurements", used, key="m_screen_cols",
                                     format_func=lambda c: labels.get(c, c))
        keep("m_within", "Study")
        within = s2.radio("Within", ["Study", "Study × treatment"], key="m_within", horizontal=True)
        keep("m_threshold", 3.5)
        threshold = s3.number_input("Robust z", min_value=2.0, max_value=10.0, step=0.5, key="m_threshold")
        st.caption("A value is flagged when |x − median| / (1.4826 × MAD) is above the threshold within its group "
                   "(Iglewicz & Hoaglin). Groups with fewer than 10 values are not screened. A record is removed "
                   "when any screened measurement is flagged.")
        if screen_cols:
            groups = ["studyId"] + (["Treatment"] if within == "Study × treatment" else [])
            removed = robust_outliers(data, screen_cols, groups, threshold).any(axis=1)
            per_study = (data.assign(removed=removed).groupby("studyId")
                         .agg(records=("removed", "size"), removed=("removed", "sum"),
                              animals_affected=("animal_id", lambda a: a[removed[a.index]].nunique())))
            with st.expander(f"Removed per study: {int(removed.sum()):,} of {len(data):,} records"):
                st.dataframe(per_study.reset_index(), hide_index=True, use_container_width=True)
    kept = data[~removed]

    unit_label = labels.get(response, response)
    screened = " Records removed by the outlier screen are left out." if screen and removed.any() else ""

    heading("Distribution by study", "One box per study, lowest median first. The box holds the middle half of the "
            "records, the line inside is the median, and the whiskers reach the most extreme values within "
            "1.5 × IQR. A study far from the rest often points to a unit or recording problem." + screened)
    production = (studies.set_index(studies["studyId"].astype(str))["ProductionType"].to_dict()
                  if "ProductionType" in studies else {})
    show(distribution_figure(box_stats(kept, response), unit_label, production), 380)

    means = study_treatment_means(kept, response)
    heading("Means by study and treatment",
            "Mean over animals with a 95% confidence interval: each animal's records are averaged first, so an "
            "animal counts once however many days it was measured. Treatment names are the study's own." + screened)
    show(means_figure(means, unit_label), 18 * len(means) + 90)
    with st.expander("Means by study and treatment (table)"):
        st.dataframe(means.round(2), hide_index=True, use_container_width=True)

    if covariates:
        heading("Relationship with the covariates", "Grey: records (at most 150 per study). Thin lines: each "
                "study's own slope. Bold: one line through all records. When the slope inside studies differs "
                "from the slope between studies, the two tell different stories.")
        cols = st.columns(min(len(covariates), 2), gap="medium")
        fmt = lambda v: "—" if v is None or np.isnan(v) else f"{v:.3g}"  # noqa: E731
        for i, cov in enumerate(covariates):
            slopes = covariate_slopes(kept, response, cov)
            points = plot_sample(kept[["studyId", cov, response]].dropna(), ["studyId"], per_group=150)
            with cols[i % len(cols)]:
                show(covariate_figure(points, cov, response, slopes, labels.get(cov, cov), unit_label), 360)
                st.caption(f"Change in {unit_label} per unit of {labels.get(cov, cov)}: all records pooled "
                           f"**{fmt(slopes['overall'])}**, within studies **{fmt(slopes['within'])}**, between "
                           f"studies **{fmt(slopes['between'])}**. The model's estimate combines within and "
                           "between, usually leaning towards within.")

    heading("Mixed model")
    group_cols = ["studyId", "animal_id"] if kept["studyId"].nunique() > 1 else ["animal_id"]
    rhs = " + ".join(["Treatment"] + covariates)
    random_text = "study and animal within study" if len(group_cols) == 2 else "animal"
    treatments = sorted(kept["Treatment"].astype(str).unique())
    setup, results = st.columns([1, 2], gap="large")
    with setup:
        keep("m_reference", "Control" if "Control" in treatments else treatments[0], treatments)
        reference = st.selectbox("Reference treatment", treatments, key="m_reference",
                                 help="The other treatments are estimated as differences from this one.")
        st.markdown(f"`{response} ~ {rhs}`  \nRandom intercepts for {random_text}; {len(kept):,} records.")
        if level == "Animal-day" and len(kept) > 20_000:
            st.caption(f"Fitting {len(kept):,} daily records can take many minutes. Animal-period means usually "
                       "fit in seconds.")
        fit = st.button("Fit model", type="primary")
    model_data = kept.assign(Treatment=pd.Categorical(kept["Treatment"].astype(str),
                                                      categories=[reference] + [t for t in treatments if t != reference]))
    formula = build_formula(response, ["Treatment"] + covariates)
    # The results stay on screen (and a refit of the same model is instant) until the model or its data change.
    spec = (formula, tuple(group_cols), reference, int(pd.util.hash_pandas_object(model_data, index=False).sum()))
    if fit:
        st.session_state["m_fitted_spec"] = spec
    with results:
        if st.session_state.get("m_fitted_spec") != spec:
            changed = " The model or its data changed since the last fit." if "m_fitted_spec" in st.session_state else ""
            st.caption("Results appear here after **Fit model**." + changed)
            return
        try:
            with st.spinner(f"Fitting the model on {len(model_data):,} records…"):
                res = fit_results(model_data, formula, tuple(group_cols), reference)
        except Exception as exc:  # noqa: BLE001
            st.session_state.pop("m_fitted_spec", None)
            st.error(f"The model could not be fitted: {exc}")
            return
        stats = res["stats"]
        tiles([
            ("Observations", f"{int(stats.get('nobs') or 0):,}", ""),
            ("Log-likelihood", f"{stats['llf']:.1f}" if stats.get("llf") is not None else "—", ""),
            ("Converged", "Yes" if res["converged"] else "No", ""),
        ])
        coefs = res["coefs"]
        coefs = coefs[~coefs["term"].astype(str).str.endswith(" Var")].copy()  # variances are shown beside
        effects = treatment_effects(coefs)
        if len(effects):
            heading(f"Treatment effects: difference from {reference}", "Estimate and 95% confidence interval. "
                    "The dashed line is no difference.")
            show(effects_figure(effects, f"Difference in {unit_label}"), 34 * len(effects) + 90)
        residual_span = "day to day" if level == "Animal-day" else "period to period"
        names = {"Group": "Between studies" if len(group_cols) == 2 else "Between animals",
                 "animal_id": "Between animals (within study)", "residual": f"Within animal ({residual_span})"}
        shares = variance_shares(res["variances"]).replace({"name": names})
        if len(shares) and shares["share"].notna().all():
            heading("Where the variation is", "Share of the total variance left after the treatment effects. "
                    "A large within-animal share means more measurements per animal are needed.")
            show(variance_figure(shares), 150)
        if "p_value" in coefs:
            coefs["p_value"] = coefs["p_value"].map(lambda p: "" if pd.isna(p) else "<0.001" if p < 0.001 else f"{p:.3f}")
        variances = pd.DataFrame(res["variances"]).replace({"name": names})
        f1, f2 = st.columns([3, 2])
        f1.markdown("**Fixed effects**")
        f1.dataframe(coefs.round(3), hide_index=True, use_container_width=True)
        f2.markdown("**Variance components**")
        f2.dataframe(variances.round(3), hide_index=True, use_container_width=True)

        rules_text = preset_name + (" (edited)" if rules_edited else "")
        with st.expander("Code to reproduce this model (R and Python)"):
            code = model_code(response, covariates, level, reference, len(group_cols) == 2, rules_text,
                              bool(screen and removed.any()))
            st.caption("Download the Animal-days table on the Export tab first (gems_cohort_animal_days.csv). "
                       "R needs the dplyr and lme4 packages; both versions give the page's estimates.")
            r_tab, py_tab = st.tabs(["R", "Python"])
            r_tab.code(code["R"], language="r")
            py_tab.code(code["Python"], language="python")

        with st.expander("AI explanation (optional)"):
            st.caption("The AI gets only the model's results (estimates, intervals, variance shares and counts), "
                       "not the data rows, and writes a plain-language explanation. statsmodels did all the "
                       "math. Every number the AI quotes is checked against the results.")
            context = {
                "model": f"{formula}; random intercepts for {random_text}; fitted on {level.lower()} records",
                "measurement": unit_label, "reference treatment": reference,
                "cohort": {"studies": int(kept["studyId"].nunique()), "animals": int(kept["animal_id"].nunique()),
                           "records": int(len(kept))},
                "GreenFeed visit rules": rules_text,
                "note": "Treatment names are study-specific; a pooled treatment effect assumes the same name means "
                        "the same treatment in every study.",
                "fit": stats, "fixed effects": coefs.drop(columns=["p_value"], errors="ignore").to_dict("records"),
                "p values": res["coefs"][["term", "p_value"]].to_dict("records") if "p_value" in res["coefs"] else [],
                "variance shares (%)": shares.to_dict("records") if len(shares) else [],
            }
            if st.button("Explain these results", key="m_explain"):
                with st.spinner("Asking the AI…"):
                    try:
                        text = interpret_model(json.dumps(context, indent=2, default=str))
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"The AI call failed: {exc}")
                        text = ""
                if text:
                    known = [v for v in pd.json_normalize(json.loads(json.dumps(context, default=str)), sep=".")
                             .to_numpy().ravel() if isinstance(v, (int, float))]
                    for part in [coefs, res["coefs"], shares]:
                        known += [float(x) for x in part.select_dtypes("number").to_numpy().ravel() if pd.notna(x)]
                    st.session_state["m_explanation"] = (spec, text, unverified_numbers(text, known))
            saved = st.session_state.get("m_explanation")
            if saved and saved[0] == spec:
                st.markdown(saved[1])
                if saved[2]:
                    st.warning("These numbers in the explanation were not found in the results; check them before "
                               "using the text: " + ", ".join(saved[2]))
                else:
                    st.caption("Every number in the explanation matches the results.")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def _csv(make_frame, item: str):
    """Deferred CSV for st.download_button: the table is prepared, written and logged only when the user
    clicks (preparing the whole cohort's animal-days each time the tab opened took seconds)."""
    def make() -> bytes:
        frame = make_frame()
        log_download(user_info.email, "analysis", item, len(frame), cohort_studies, recipe)
        return frame.to_csv(index=False).encode("utf-8")
    return make


def render_export() -> None:
    left, right = st.columns([1, 1], gap="large")
    with left:
        heading("Recipe", "The choices that define this cohort. Keep it with your results so the selection can be repeated.")
        recipe_json = json.dumps(recipe, indent=2, default=str, ensure_ascii=False)
        st.code(recipe_json, language="json")
        st.download_button("Download recipe (JSON)", recipe_json, file_name="gems_cohort_recipe.json",
                           mime="application/json", on_click="ignore")
    with right:
        render_tables()


def render_tables() -> None:
    heading("Tables", "The cohort's rows, ready for R, Python or a spreadsheet. Animal-days include the "
            "variables calculated in this page; the data dictionary says how each one is calculated.")
    full_dictionary = pd.concat([dictionary, calculated_dictionary()], ignore_index=True)
    st.download_button(f"Data dictionary — {len(full_dictionary):,} rows (CSV)", full_dictionary.to_csv(index=False),
                       file_name="gems_kg_data_dictionary.csv", mime="text/csv", on_click="ignore",
                       help="Column labels, units and ontology terms. No data values.")
    can_download = bool(synthetic_dir()) or has_api_access(user_info, user_info.bearer_token)
    if not can_download:
        st.info("Downloading the cohort's data is limited to users on the API access list. "
                "Ask the GEMS team if you need access.")
        return
    st.caption("Each download is logged with your email, the table, the row count and the recipe above.")
    in_studies = lambda df: df[df["studyId"].astype(str).isin(cohort_studies)] if "studyId" in df else df.iloc[0:0]  # noqa: E731
    in_cohort = animals_t["animal_id"].isin(cohort_animals)
    small = {item: in_studies(tables[t]) for item, t in [("studies", "kgStudy"), ("diets", "kgDiet"),
                                                         ("diet_nutrients", "kgDietNutrients")]}
    # (label, item, rows, how to make the table): only the row count is worked out now
    items = [
        ("Animal-days", "animal_days", len(cdays), lambda: cdays.assign(Date=cdays["Date"].dt.date)),
        ("Animals", "animals", int(in_cohort.sum()), lambda: animals_t[in_cohort]),
        ("Studies", "studies", len(small["studies"]), lambda: small["studies"]),
        ("Diets (ingredients)", "diets", len(small["diets"]), lambda: small["diets"]),
        ("Diet nutrients", "diet_nutrients", len(small["diet_nutrients"]), lambda: small["diet_nutrients"]),
    ]
    for label, item, rows, make_frame in items:
        if not rows:
            continue
        st.download_button(f"{label} — {rows:,} rows (CSV)", _csv(make_frame, item),
                           file_name=f"gems_cohort_{item}.csv", mime="text/csv", on_click="ignore", key=f"dl_{item}")


if tab_overview.open:
    with tab_overview:
        render_overview()
if tab_qc.open:
    with tab_qc:
        render_qc()
if tab_lab.open:
    with tab_lab:
        render_lab()
if tab_model.open:
    with tab_model:
        render_model()
if tab_export.open:
    with tab_export:
        render_export()
