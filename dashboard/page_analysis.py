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
    UNKNOWN,
    animal_period_means,
    box_stats,
    calculated_dictionary,
    calculated_labels,
    cohort_days,
    column_labels,
    covariate_slopes,
    facet_counts,
    facet_masks,
    load_dataset,
    numeric_columns,
    plot_sample,
    robust_outliers,
    source_label,
    study_overview,
    study_treatment_means,
    synthetic_dir,
    treatment_effects,
    variance_shares,
)
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


def show(fig: go.Figure, height: int) -> None:
    fig.update_layout(height=height, margin={"l": 8, "r": 8, "t": 28, "b": 8}, font={"size": 12},
                      legend={"orientation": "h", "y": 1.0, "yanchor": "bottom", "x": 0, "title_text": ""})
    st.plotly_chart(fig, use_container_width=True)


user = require_authorized_user()
sidebar_user(user)
user_info = get_current_user_info()

source = source_label()
if synthetic_dir():
    st.warning("Synthetic test data: every value is invented. This is not GEMS data.", icon="🧪")
if st.sidebar.button("Reload data", help="Load the kg_v1 tables again, e.g. after the KG job has run."):
    load_dataset.clear()
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

recipe = {
    "source": source,
    "made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "dataAccess": access,
    "mustHave": {label: HAS[label] for label in has},
    "facets": {FACETS[k][1]: v for k, v in chosen.items() if v},
    "dateFrom": str(date_from) if date_from else None,
    "dateTo": str(date_to) if date_to else None,
    "result": {"studies": cohort_studies, "animals": len(cohort_animals), "animalDays": len(cdays)},
    "calculatedColumns": {c: f"{how} [{unit}]" for c, (_, unit, how, _) in CALCULATED.items()
                          if c in cdays and cdays[c].notna().any()},
}

if cdays.empty:
    st.info("No animal-days match this cohort. Loosen the choices in the sidebar.")
    st.stop()

# Only the open tab runs (a closed tab updates when it is opened; every tab uses the same cohort).
# Streamlit forgets the value of a widget that is not drawn, so the tabs' widget values are saved again
# here: your choices are still there when you come back to a tab.
TAB_WIDGETS = ["ov_study", "m_response", "m_level", "m_covariates", "m_screen", "m_screen_cols", "m_within",
               "m_threshold", "m_reference"]
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


tab_overview, tab_model, tab_export = st.tabs(["Overview", "Model", "Export"], key="analysis_tab", on_change="rerun")


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
        ("GreenFeed visits", f"{visits:,}", "matched to animals, CH4 > 0"),
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
if tab_model.open:
    with tab_model:
        render_model()
if tab_export.open:
    with tab_export:
        render_export()
