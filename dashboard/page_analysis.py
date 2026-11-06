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
    KG_CLASSES,
    RULES_DEFAULT,
    UNKNOWN,
    VISIT_PRESETS,
    active_visit_steps,
    animal_dot,
    animal_graph,
    animal_period_means,
    animal_spread,
    apply_daily_gases,
    box_stats,
    calculated_dictionary,
    calculated_labels,
    cohort_days,
    column_labels,
    covariate_slopes,
    facet_counts,
    facet_masks,
    kg_schema_counts,
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
    schema_dot,
    source_label,
    studies_dot,
    study_graph,
    study_overview,
    study_treatment_means,
    synthetic_dir,
    treatment_effects,
    treatment_runs,
    unverified_numbers,
    variance_shares,
    visit_attrition,
    visit_qc_charts,
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
# This page only: the full window width and the look of the kg_map_claude workbench: a light page, white cards
# (title, one-line note and a "table" button; see card()) and one chart style (see style_figure()).
st.markdown("""<style>
  .block-container, [data-testid="stMainBlockContainer"] {
    max-width: none !important; padding-left: 2.2rem !important; padding-right: 2.2rem !important; }
  [data-testid="stMain"] { background: #f9f9f7; }
  [data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.6rem; }
  .ga-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin: 2px 0 6px; }
  .ga-tile { border: 1px solid rgba(11,11,11,.10); border-radius: 10px; padding: 9px 12px; background: #fcfcfb; }
  .ga-tile .l { font-size: 12px; color: #52514e; }
  .ga-tile .v { font-size: 22px; font-weight: 600; margin-top: 2px; line-height: 1.25; color: #0b0b0b; }
  .ga-tile .d { font-size: 12px; color: #898781; }
  .ga-h { font-size: 14px; font-weight: 600; margin: 10px 0 0; color: #0b0b0b; }
  .ga-sub { font-size: 12.5px; color: #52514e; margin: 0 0 6px; }
  .ga-intro { color: #52514e; font-size: 14px; max-width: 1000px; margin: 0 0 4px; }
  [class*="st-key-gacard_"] { position: relative; background: #fcfcfb; border: 1px solid rgba(11,11,11,.10);
    border-radius: 10px; padding: 12px 14px 8px; gap: .5rem;
    box-shadow: 0 1px 2px rgba(11,11,11,.06), 0 4px 14px rgba(11,11,11,.05); }
  [class*="st-key-gacard_"] > [data-testid="stLayoutWrapper"]:has([data-testid="stPopover"]) {
    position: absolute; top: 9px; right: 12px; width: auto !important; z-index: 3; }
  [class*="st-key-gacard_"] [data-testid="stPopoverButton"] { border: 0; background: none; box-shadow: none;
    color: #1c5cab; font-size: 12px; padding: 0 2px; min-height: 0; }
  /* Streamlit pulls the element after a text block up by 1rem; inside cards that hid the note's last line */
  [class*="st-key-gacard_"] [data-testid="stMarkdownContainer"]:has(> .ga-ct, > .ga-funnel) { margin-bottom: 0 !important; }
  .ga-ct { font-size: 14px; font-weight: 600; color: #0b0b0b; line-height: 1.3; padding-right: 64px; }
  .ga-cs { font-size: 12.5px; color: #52514e; line-height: 1.4; margin: 2px 0 0; }
  .ga-vg { margin: 12px 0 4px; font-size: 11px; color: #898781; text-transform: uppercase; letter-spacing: .05em; }
  .ga-funnel { display: flex; flex-direction: column; gap: 6px; margin: 8px 0 6px; }
  .ga-fstep { display: grid; grid-template-columns: minmax(140px, 260px) minmax(60px, 1fr) 170px; gap: 10px;
    align-items: center; font-size: 12.5px; }
  .ga-fstep .fl { color: #52514e; }
  .ga-fstep .fb { height: 14px; background: #e1e0d9; border-radius: 0 4px 4px 0; position: relative; }
  .ga-fstep .fb i { position: absolute; left: 0; top: 0; bottom: 0; background: #3987e5; border-radius: 0 4px 4px 0; }
  .ga-fstep .fv { font-variant-numeric: tabular-nums; color: #0b0b0b; white-space: nowrap; }
  .ga-fstep .fv .loss { color: #898781; margin-left: 4px; }
  .ga-fstep.gap { margin-top: 6px; }
</style>""", unsafe_allow_html=True)

# Chart colours from the kg_map_claude palette (categorical series, neutral "other", one-hue ramp) and its inks.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
OTHER, EMPTY, CRITICAL = "#b4b2aa", "#e1e0d9", "#d03b3b"
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
PRODUCTION_COLORS = {"Dairy": SERIES[0], "Beef": SERIES[1], UNKNOWN: OTHER}
INK, INK_2, MUTED, GRID, AXIS = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
CHART_FONT = '"Source Sans 3", "Source Sans Pro", system-ui, -apple-system, "Segoe UI", sans-serif'


def heading(title: str, sub: str = "") -> None:
    st.markdown(f'<div class="ga-h">{escape(title)}</div>' + (f'<div class="ga-sub">{sub}</div>' if sub else ""),
                unsafe_allow_html=True)


def intro(text: str) -> None:
    """The tab's opening line(s), above its cards."""
    st.markdown(f'<p class="ga-intro">{text}</p>', unsafe_allow_html=True)


def card(key: str, title: str, note: str = "", table: pd.DataFrame | None = None):
    """A white card: title, one-line note and, with `table`, a "table" button that shows the numbers behind the
    chart. Returns the card; draw its content inside `with card(...):`. Keys must be unique on the page."""
    box = st.container(key=f"gacard_{key}")
    box.markdown(f'<div class="ga-ct">{escape(title)}</div>' + (f'<div class="ga-cs">{note}</div>' if note else ""),
                 unsafe_allow_html=True)
    if table is not None:
        with box.popover("table"):
            st.dataframe(table, hide_index=True, use_container_width=True)
    return box


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


def style_figure(fig: go.Figure, height: int, top: int, legend_y: float) -> None:
    """The page's chart style (as in the kg_map_claude workbench): hairline grid, muted ticks, rounded bar ends,
    legend top left, transparent background (the card shows through)."""
    fig.update_layout(height=height, margin={"l": 8, "r": 12, "t": top, "b": 8},
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font={"family": CHART_FONT, "size": 12, "color": INK_2}, bargap=0.12, barcornerradius=3,
                      legend={"orientation": "h", "x": 0, "y": legend_y, "yanchor": "bottom", "title_text": "",
                              "font": {"size": 12, "color": INK_2}},
                      hoverlabel={"bgcolor": "#ffffff", "bordercolor": GRID,
                                  "font": {"family": CHART_FONT, "size": 12.5, "color": INK}})
    # automargin: room for the tick labels and titles (the default template leaves it off, which cut them)
    fig.update_xaxes(showgrid=False, zeroline=False, showline=True, linecolor=AXIS, ticks="", automargin=True,
                     tickfont={"size": 11, "color": MUTED}, title_font={"size": 12, "color": INK_2}, title_standoff=8)
    fig.update_yaxes(showgrid=True, gridcolor=GRID, zeroline=False, showline=False, ticks="", automargin=True,
                     tickfont={"size": 11, "color": MUTED}, title_font={"size": 12, "color": INK_2})
    fig.update_annotations(font={"family": CHART_FONT, "size": 12, "color": INK_2})


def show(fig: go.Figure, height: int, top: int = 28, legend_y: float = 1.0) -> None:
    """Draw a figure in the page's chart style, without the toolbar and with Streamlit's chart theme off (so this
    style is what shows). Panels with their own titles need a larger `top` and a higher `legend_y`."""
    style_figure(fig, height, top, legend_y)
    st.plotly_chart(fig, use_container_width=True, theme=None, config={"displayModeBar": False})


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
        st.session_state["visits_loaded"] = True  # the KG view may now count GreenFeed units without a new query
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
               "lab_unit", "lab_value", "lab_target", "ex_study", "ex_animal", "ex_value", "kg_view", "kg_studies"]
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


tab_overview, tab_qc, tab_lab, tab_model, tab_explore, tab_kg, tab_export = st.tabs(
    ["Overview", "GreenFeed QC", "Standardisation lab", "Model", "Explore", "KG view", "Export"],
    key="analysis_tab", on_change="rerun")


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

    ov = study_overview(cdays, studies)
    table = ov.assign(**{"Open data": ov["Open data"].map({True: "Open", False: "Not open"})}) if "Open data" in ov else ov
    with card("ov_studies", "Studies in the cohort", "Design is inferred from the data: <i>within-animal</i> when "
              "animals changed treatment (crossover or switchback), <i>between-animal</i> when each kept one treatment."):
        st.dataframe(table, hide_index=True, use_container_width=True, height=min(420, 35 * len(ov) + 38))

    left, right = st.columns(2, gap="medium")
    tl = ov.assign(Start=pd.to_datetime(ov["First"]), End=pd.to_datetime(ov["Last"]) + pd.Timedelta(days=1))
    with left, card("ov_timeline", "When the studies ran", "One bar per study, first to last record in the cohort.",
                    tl[["Study", "Type", "First", "Last", "Animals", "Animal-days"]]
                    if {"Type", "Animals", "Animal-days"} <= set(tl.columns) else None):
        show(timeline_figure(tl), max(160, 22 * len(tl) + 80))
    with right, card("ov_coverage", "Coverage: animals × days", "GreenFeed visits per animal and day. Hover for the "
                     "treatment."):
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

def card_chart(fig: go.Figure, height: int = 220, legend: bool = False, top: int | None = None,
               legend_y: float = 1.02) -> None:
    """show() for the small chart cards: no legend unless asked, and room above the plot for a legend or for the
    label of a rule line."""
    if top is None:
        top = 30 if legend else (24 if fig.layout.annotations else 8)
    fig.update_layout(showlegend=legend)
    show(fig, height, top, legend_y)


def _share(x: float) -> str:
    return f"{x:.1%}" if x < 0.1 else f"{x:.0%}"


def funnel_html(att: pd.DataFrame) -> str:
    """Attrition as bars: visits left after each rule (grey = removed so far), then the animal-days and animals
    that still have CH4. Each value shows what the step removed."""
    first, last = att.iloc[0], att.iloc[-1]

    def row(label: str, value: int, base: int, unit: str, prev: int | None, gap: bool = False) -> str:
        width = 100 * value / base if base else 0
        loss = f'<span class="loss">−{_share(1 - value / prev)}</span>' if prev and value < prev else ""
        return (f'<div class="ga-fstep{" gap" if gap else ""}"><div class="fl">{escape(label)}</div>'
                f'<div class="fb"><i style="width:{width:.1f}%"></i></div>'
                f'<div class="fv">{value:,} {unit}{loss}</div></div>')

    rows, prev = [], None
    for r in att.itertuples():
        rows.append(row(r.step if prev is None else f"− {r.step}", int(r.visits), int(first["visits"]), "visits", prev))
        prev = int(r.visits)
    days0, animals0 = int(first["animal_days"]), int(first["animals"])
    rows.append(row("Animal-days with CH4", int(last["animal_days"]), days0, "animal-days", days0, gap=True))
    rows.append(row("Animals with CH4", int(last["animals"]), animals0, "animals", animals0))
    return f'<div class="ga-funnel">{"".join(rows)}</div>'


def _rule_line(fig: go.Figure, x: float, text: str) -> None:
    fig.add_vline(x=x, line_dash="dash", line_color=INK_2, line_width=1, annotation_text=text,
                  annotation_position="top", annotation_yanchor="bottom")  # label above the plot, clear of the bars


def duration_figure(d: pd.DataFrame, rule: float) -> go.Figure:
    labels = [f"{a:.2f}–{b:.2f} min" if a < 8 else "8 min or more" for a, b in zip(d["x0"], d["x1"])]
    fig = go.Figure(go.Bar(x=d["x0"] + 0.125, y=d["visits"], width=0.22, marker_color=SERIES[0], customdata=labels,
                           hovertemplate="%{customdata}<br>%{y:,} visits<extra></extra>"))
    if rule > 0:
        _rule_line(fig, rule, f"rule: ≥ {rule:g} min")
    fig.update_xaxes(range=[0, 8.25], dtick=1, title="minutes of good data")
    fig.update_yaxes(tickformat="~s")
    return fig


def per_day_figure(d: pd.DataFrame) -> go.Figure:
    total = max(int(d["animal_days"].sum()), 1)
    fig = go.Figure(go.Bar(x=d["visits_that_day"], y=d["animal_days"], marker_color=SERIES[0],
                           customdata=[_share(n / total) for n in d["animal_days"]],
                           hovertemplate="%{x} visits that day<br>%{y:,} animal-days (%{customdata})<extra></extra>"))
    fig.update_xaxes(type="category", title="visits that day")
    fig.update_yaxes(tickformat="~s")
    return fig


def hours_figure(d: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Bar(x=d["hour"], y=d["visits"], marker_color=SERIES[0],
                           customdata=[f"{h:02d}:00–{h:02d}:59" for h in d["hour"]],
                           hovertemplate="%{customdata}<br>%{y:,} kept visits<extra></extra>"))
    fig.update_xaxes(range=[-0.6, 23.6], dtick=3, title="hour of day")
    fig.update_yaxes(tickformat="~s")
    return fig


def diurnal_figure(d: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    present = set(d["production"])
    order = [p for p in PRODUCTION_COLORS if p in present] + sorted(present - set(PRODUCTION_COLORS))
    for k, p in enumerate(order):
        part = d[d["production"] == p].sort_values("hour")
        fig.add_trace(go.Scatter(
            x=part["hour"], y=part["pct"], mode="lines+markers", name=p, customdata=part["visits"],
            line={"color": PRODUCTION_COLORS.get(p, SERIES[(k + 2) % len(SERIES)]), "width": 2}, marker={"size": 6},
            hovertemplate=f"{escape(p)} · %{{x:02d}}:00<br>%{{y:+.1f}}% vs the day's mean<br>"
                          "%{customdata:,} visits<extra></extra>"))
    fig.add_hline(y=0, line_dash="dash", line_color=MUTED, line_width=1)
    fig.update_xaxes(range=[-0.5, 23.5], dtick=3, title="hour of day")
    fig.update_yaxes(ticksuffix="%", title="% vs the day's mean")
    return fig


def unit_visits_figure(d: pd.DataFrame, rule: int) -> go.Figure:
    step = int(d["x1"].iloc[0] - d["x0"].iloc[0])
    last = len(d) - 1
    labels = [(f"{a:,}" if step == 1 else f"{a:,}–{b - 1:,}") + (" or more" if i == last else "")
              for i, (a, b) in enumerate(zip(d["x0"], d["x1"]))]
    fig = go.Figure(go.Bar(x=d["x0"] + step / 2, y=d["n"], width=step * 0.88, marker_color=SERIES[0],
                           customdata=labels, hovertemplate="%{customdata} visits<br>%{y:,} animal-periods<extra></extra>"))
    if rule > 0:
        _rule_line(fig, rule, f"rule: ≥ {rule}")
    fig.update_xaxes(range=[0, float(d["x1"].iloc[-1])], title="kept visits per animal-period")
    fig.update_yaxes(tickformat="~s")
    return fig


def coverage_figure(d: pd.DataFrame, rule: int) -> go.Figure:
    fig = go.Figure(go.Bar(x=d["windows"], y=d["units"], marker_color=[OTHER if w < rule else SERIES[0] for w in d["windows"]],
                           hovertemplate="%{x} of 8 windows<br>%{y:,} animal-periods<extra></extra>"))
    fig.update_xaxes(range=[-0.6, 8.6], dtick=1, title="3-hour windows covered (of 8)")
    fig.update_yaxes(tickformat="~s")
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
                                     showlegend="daily" not in shown, line={"color": INK, "width": 1.5},
                                     hovertemplate="%{x|%Y-%m-%d}: %{y:.0f} g/d<extra>daily mean</extra>"),
                          row=r, col=c)
            shown.add("daily")
    fig.update_yaxes(title_text="CH4 (g/d)", col=1)
    return fig


def _apply_visit_preset() -> None:
    name = st.session_state.get("qc_preset_select")
    if name not in VISIT_PRESETS:
        return  # "Custom" only names edited rules
    for rule, value in {**RULES_DEFAULT, **VISIT_PRESETS[name][0]}.items():
        st.session_state[f"qc_{rule}"] = value
    st.session_state["qc_preset_name"] = name


def protocol_card() -> None:
    """The visit rules, as a form in a card (same keys as before: the rules apply to every tab)."""
    with card("protocol", "Protocol", "Rules applied in order: each visit, then each animal's visits and days, then "
              "each animal and period."):
        st.session_state["qc_preset_select"] = "Custom" if rules_edited else preset_name
        st.selectbox("Start from", [*VISIT_PRESETS, "Custom"], key="qc_preset_select", on_change=_apply_visit_preset,
                     help="Custom: the rules below were changed after choosing a starting point.")
        st.caption(f"Edited from **{preset_name}**." if rules_edited else VISIT_PRESETS[preset_name][1])
        st.markdown('<div class="ga-vg">1 · Each visit</div>', unsafe_allow_html=True)
        keep("qc_exclude_phases", False)
        st.toggle("Leave out adaptation, transition and washout days", key="qc_exclude_phases",
                  help="Uses each day's experimental phase as the contributor recorded it.")
        keep("qc_drop_interrupted", False)
        st.toggle("Leave out interrupted visits", key="qc_drop_interrupted",
                  help="Visits whose interrupted flag cannot be read are kept.")
        keep("qc_min_minutes", 0.0)
        st.number_input("Minimum good-data time (min)", min_value=0.0, max_value=15.0, step=0.5, format="%g",
                        key="qc_min_minutes",
                        help="0 = off. C-Lock and the Global Research Alliance SOPs ask for at least 2 minutes.")
        keep("qc_min_airflow", 0.0)
        st.number_input("Minimum airflow (L/s)", min_value=0.0, max_value=200.0, step=1.0, format="%g",
                        key="qc_min_airflow",
                        help="0 = off. Beck et al. (2024) left out visits below 26 L/s. Visits without airflow are kept.")
        keep("qc_max_ch4", 0.0)
        st.number_input("Maximum CH4 (g/d)", min_value=0.0, max_value=3000.0, step=50.0, format="%g", key="qc_max_ch4",
                        help="Visits with more CH4 are left out. 0 = off.")
        st.markdown('<div class="ga-vg">2 · Each animal\'s visits and days</div>', unsafe_allow_html=True)
        a, b = st.columns(2, gap="small")
        keep("qc_mad_k", 0.0)
        a.number_input("Robust z above", min_value=0.0, max_value=10.0, step=0.5, format="%g", key="qc_mad_k",
                       help="Per-animal outliers. 0 = off. Per animal: |CH4 − median| / (1.4826 × MAD) over the visits "
                            "still kept; animals with fewer than 5 visits are not screened.")
        keep("qc_min_visits_day", 1)
        b.number_input("Min visits a day", min_value=1, max_value=20, step=1, key="qc_min_visits_day",
                       help="Days with fewer kept visits are left out.")
        st.markdown('<div class="ga-vg">3 · Each animal and period</div>', unsafe_allow_html=True)
        a, b, c = st.columns(3, gap="small")
        keep("qc_min_days", 0)
        a.number_input("Min days", min_value=0, max_value=100, step=1, key="qc_min_days")
        keep("qc_min_visits", 0)
        b.number_input("Min visits", min_value=0, max_value=500, step=1, key="qc_min_visits")
        keep("qc_min_bins", 0)
        c.number_input("Min windows", min_value=0, max_value=8, step=1, key="qc_min_bins")
        st.caption("Per animal, treatment and period: days with kept visits, kept visits, and three-hour windows of "
                   "the day covered (of 8; spreads the visits over the day, since CH4 changes with time since "
                   "feeding). 0 = off. Beck et al. (2024): 30 visits of 3 min or more.")
        with st.expander("Protocol as data"):
            st.json({"startedFrom": preset_name, "edited": rules_edited, **visit_rules})


def render_qc() -> None:
    intro("Which GreenFeed visits count when each day's CH4 is calculated. The protocol applies to every tab: the "
          "Overview, the Model, the calculated variables (methane yield, Ym, …) and the Export all use the daily CH4 "
          "it gives. With no rules, daily CH4 is the mean of every visit matched to the animal, as the KG job "
          "publishes it.")
    left, right = st.columns([1, 2.6], gap="medium")
    with left:
        protocol_card()
    with right:
        try:
            visits = load_visits(source)
            result = visit_rules_result(source, tuple(sorted(visit_rules.items())))
            st.session_state["visits_loaded"] = True
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
        status = result["status"]
        att = visit_attrition(visits, status, visit_rules, in_cohort)
        with card("attrition", "Attrition", "What each rule removes from this cohort's visits, in the order the rules "
                  "are applied; then the animal-days and animals that still have CH4.",
                  att.rename(columns={"step": "Step", "visits": "Visits left", "removed": "Removed",
                                      "animal_days": "Animal-days left", "animals": "Animals left"})):
            st.markdown(funnel_html(att), unsafe_allow_html=True)

        production = (animals.set_index("animal_id")["ProductionType"]
                      if {"animal_id", "ProductionType"} <= set(animals.columns) else None)
        q = visit_qc_charts(visits, status, in_cohort, production)
        r = {**RULES_DEFAULT, **visit_rules}
        c1, c2 = st.columns(2, gap="medium")
        dur = q["duration"]
        with c1, card("dur", "Visit duration", "Minutes of good data of the cohort's visits, before the duration rule.",
                      pd.DataFrame({"Minutes": [f"{a:.2f}–{b:.2f}" if a < 8 else "8 or more"
                                                for a, b in zip(dur["x0"], dur["x1"])], "Visits": dur["visits"]})):
            card_chart(duration_figure(dur, r["min_minutes"]))
        with c2, card("perday", "Visits per animal-day", "All the cohort's visits on each day an animal visited.",
                      q["per_day"].rename(columns={"visits_that_day": "Visits that day", "animal_days": "Animal-days"})):
            card_chart(per_day_figure(q["per_day"]))
        c3, c4 = st.columns(2, gap="medium")
        with c3, card("hours", "When animals visit", "Kept visits by hour of day (GreenFeed clock).",
                      q["hours"].rename(columns={"hour": "Hour", "visits": "Kept visits"})):
            card_chart(hours_figure(q["hours"]))
        diurnal = q["diurnal"]
        with c4, card("diurnal", "CH4 by hour of day", "Kept visits, % above or below the same animal's mean that day "
                      "(days with ≥ 2 kept visits and a mean ≥ 20 g/d; hours with ≥ 30 visits).",
                      diurnal.pivot(index="hour", columns="production", values="pct").round(1).reset_index()
                      .rename(columns={"hour": "Hour"}) if len(diurnal) else None):
            if diurnal.empty:
                st.caption("Not enough days with 2 or more kept visits.")
            else:
                card_chart(diurnal_figure(diurnal), legend=True)
        c5, c6 = st.columns(2, gap="medium")
        units = q["unit_visits"]
        with c5, card("unitvisits", "Visits per animal-period", "Kept visits of each animal × treatment × period, "
                      "before the animal-period rule.",
                      units.rename(columns={"x0": "From", "x1": "To (not incl.)", "n": "Animal-periods"})
                      if len(units) else None):
            if units.empty:
                st.caption("No animal-period has kept visits.")
            else:
                card_chart(unit_visits_figure(units, r["min_visits"]))
        with c6, card("coverage", "Time-of-day coverage per animal-period", "How many of the 8 three-hour windows of "
                      "the day have a kept visit; low coverage biases the mean. Grey: below the rule.",
                      q["coverage"].rename(columns={"windows": "Windows covered", "units": "Animal-periods"})):
            card_chart(coverage_figure(q["coverage"], r["min_bins"]))

    with card("record", "CH4 record by animal", "Each visit's CH4 over time: blue visits are kept, other colours show "
              "the first rule that removed the visit. The dark line is the daily mean of the kept visits."):
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
            reason=[reason_names[int(s)] for s in status[rows]])
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
        card_chart(record_figure(rec, daily_rec, order, list(reason_names.values())), 280 * n_rows + 100,
                   legend=True, top=80, legend_y=1 + 55 / (280 * n_rows))  # legend above the animals' titles


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
    intro("How many GreenFeed days does an animal need? How reliable an animal's average becomes as days (or visits) "
          "are added. Uses the values after your GreenFeed visit rules, within each study, after removing treatment "
          "and period means, so only differences between animals count as signal.")
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
            st.session_state["visits_loaded"] = True
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
    table = per.assign(enough=np.where(per["records_per_period"] >= per["needed"], "Yes", "No")).sort_values("needed")
    table = table.rename(columns={
        "studyId": "Study", "animals": "Animals", "animal_periods": "Animal-periods", "records": unit,
        "records_per_period": f"{unit} per animal-period", "sd_between": "SD between animals",
        "sd_within": "SD within animal", "r": f"Repeatability (one {word[:-1]})", "needed": f"{unit} needed",
        "enough": "Enough now?"}).replace(np.inf, np.nan).round(2)
    left, right = st.columns([3, 2], gap="medium")
    with left, card("lab_curve", "Reliability of an animal's mean", "Thin lines: each study. Bold: all studies pooled; "
                    "the dotted line marks where it reaches the target (dashed). R(k) = k·r / (1 + (k − 1)·r), "
                    "Spearman–Brown."):
        show(lab_figure(per, pooled_r, target, k_max, unit), 400)
    with right, card("lab_table", "By study", f"{unit} needed = target/(1 − target) × (1 − r)/r. \"Enough now?\" "
                     f"compares it with the study's average {word} per animal and period."):
        st.dataframe(table, hide_index=True, use_container_width=True, height=368)


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
        screen_card = card("m_screen", "Outlier screening", "A value is flagged when |x − median| / (1.4826 × MAD) is "
                           "above the threshold within its group (Iglewicz & Hoaglin). Groups with fewer than 10 values "
                           "are not screened. A record is removed when any screened measurement is flagged.")
        s1, s2, s3 = screen_card.columns([3, 2, 1])
        keep("m_screen_cols", [response], used, multi=True)
        screen_cols = s1.multiselect("Screen these measurements", used, key="m_screen_cols",
                                     format_func=lambda c: labels.get(c, c))
        keep("m_within", "Study")
        within = s2.radio("Within", ["Study", "Study × treatment"], key="m_within", horizontal=True)
        keep("m_threshold", 3.5)
        threshold = s3.number_input("Robust z", min_value=2.0, max_value=10.0, step=0.5, key="m_threshold")
        if screen_cols:
            groups = ["studyId"] + (["Treatment"] if within == "Study × treatment" else [])
            removed = robust_outliers(data, screen_cols, groups, threshold).any(axis=1)
            per_study = (data.assign(removed=removed).groupby("studyId")
                         .agg(records=("removed", "size"), removed=("removed", "sum"),
                              animals_affected=("animal_id", lambda a: a[removed[a.index]].nunique())))
            with screen_card.expander(f"Removed per study: {int(removed.sum()):,} of {len(data):,} records"):
                st.dataframe(per_study.reset_index(), hide_index=True, use_container_width=True)
    kept = data[~removed]

    unit_label = labels.get(response, response)
    screened = " Records removed by the outlier screen are left out." if screen and removed.any() else ""

    production = (studies.set_index(studies["studyId"].astype(str))["ProductionType"].to_dict()
                  if "ProductionType" in studies else {})
    boxes = box_stats(kept, response)
    with card("m_dist", "Distribution by study", "One box per study, lowest median first. The box holds the middle "
              "half of the records, the line inside is the median, and the whiskers reach the most extreme values "
              "within 1.5 × IQR. A study far from the rest often points to a unit or recording problem." + screened,
              boxes.round(2)):
        show(distribution_figure(boxes, unit_label, production), 380)

    means = study_treatment_means(kept, response)
    with card("m_means", "Means by study and treatment", "Mean over animals with a 95% confidence interval: each "
              "animal's records are averaged first, so an animal counts once however many days it was measured. "
              "Treatment names are the study's own." + screened, means.round(2)):
        show(means_figure(means, unit_label), 18 * len(means) + 90)

    if covariates:
        cov_card = card("m_cov", "Relationship with the covariates", "Grey: records (at most 150 per study). Thin "
                        "lines: each study's own slope. Bold: one line through all records. When the slope inside "
                        "studies differs from the slope between studies, the two tell different stories.")
        cols = cov_card.columns(min(len(covariates), 2), gap="medium")
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

    group_cols = ["studyId", "animal_id"] if kept["studyId"].nunique() > 1 else ["animal_id"]
    rhs = " + ".join(["Treatment"] + covariates)
    random_text = "study and animal within study" if len(group_cols) == 2 else "animal"
    treatments = sorted(kept["Treatment"].astype(str).unique())
    model_card = card("m_model", "Mixed model", "Treatment (and the covariates) as fixed effects, random intercepts "
                      "for the grouping; fitted with statsmodels (REML).")
    setup, results = model_card.columns([1, 2], gap="large")
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
# Explore and KG view share the selected study and animal (keys ex_study, ex_animal)
# ---------------------------------------------------------------------------
RECORD_PANELS = [("CH4_g_d", "CH4 (g/d)"), ("DryMatterIntake", "DMI (kg/d)"), ("MilkProduction", "Milk (kg/d)"),
                 ("BodyWeight", "Body weight (kg)")]


def _animal_names() -> dict:
    if {"animal_id", "AnimalIdentifier"} <= set(animals_t.columns):
        return animals_t.set_index("animal_id")["AnimalIdentifier"].astype(str).to_dict()
    return {}


def pick_animal(where) -> tuple[str, object, pd.DataFrame]:
    """Study and animal selectors (shared by Explore and KG view); returns (study, animal_id, study's days)."""
    names = _animal_names()
    c1, c2 = where.columns([1, 2], gap="medium")
    keep("ex_study", cohort_studies[0], cohort_studies)
    study = c1.selectbox("Study", cohort_studies, key="ex_study")
    sub = cdays[cdays["studyId"].astype(str) == study]
    ch4_days = sub.groupby("animal_id")["CH4_g_d"].count() if "CH4_g_d" in sub else pd.Series(dtype=int)
    order = sub.groupby("animal_id").size().to_frame("days").join(ch4_days.rename("ch4")).fillna(0) \
        .sort_values(["ch4", "days"], ascending=False).index.tolist()
    keep("ex_animal", order[0], order)
    animal = c2.selectbox("Animal (most CH4 days first)", order, key="ex_animal",
                          format_func=lambda a: f"{names.get(a, a)} ({int(ch4_days.get(a, 0))} CH4 days)")
    return study, animal, sub


def record_panels_figure(rec: pd.DataFrame, runs: pd.DataFrame) -> go.Figure:
    """One animal's daily values, one panel per measurement it has, with its treatment periods shaded."""
    panels = [(c, t) for c, t in RECORD_PANELS if c in rec and rec[c].notna().any()]
    fig = make_subplots(rows=len(panels), cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        subplot_titles=[t for _, t in panels])
    for i, (col, title) in enumerate(panels, start=1):
        d = rec.dropna(subset=[col])
        fig.add_trace(go.Scatter(x=d["Date"], y=d[col], mode="lines+markers", name=title, showlegend=False,
                                 line={"color": SERIES[0], "width": 1}, marker={"size": 4},
                                 hovertemplate="%{x|%Y-%m-%d}: %{y:.1f}<extra>" + title + "</extra>"), row=i, col=1)
    colors = {t: SERIES[(k + 1) % len(SERIES)] for k, t in enumerate(dict.fromkeys(runs["Treatment"].astype(str)))}
    for r in runs.itertuples():
        fig.add_vrect(x0=r.start, x1=r.end + pd.Timedelta(days=1), fillcolor=colors[str(r.Treatment)], opacity=0.12,
                      line_width=0, layer="below", row="all", col=1)
        if r.days >= 3:
            period = "" if pd.isna(r.Period) else f" · P{r.Period}"
            fig.add_annotation(x=r.start + (r.end - r.start) / 2, y=1.0, yref="paper", yanchor="bottom", yshift=22,
                               text=f"{r.Treatment}{period}", showarrow=False, font={"size": 10})  # above panel titles
    return fig


def spread_figure(spread: pd.DataFrame, highlight, names: dict, y_label: str) -> go.Figure:
    """Animals sorted by their mean, each with its day-to-day SD as a bar; coloured by main treatment."""
    s = spread.reset_index(drop=True).assign(rank=lambda d: d.index + 1)
    fig = go.Figure()
    for k, (trt, part) in enumerate(s.groupby(s["treatment"].fillna("–").astype(str), sort=False)):
        fig.add_trace(go.Scatter(
            x=part["rank"], y=part["mean"], mode="markers", name=trt,
            marker={"color": SERIES[k % len(SERIES)], "size": 7},
            error_y={"type": "data", "array": part["sd"].fillna(0).tolist(), "thickness": 1, "width": 0},
            customdata=list(zip([names.get(a, a) for a in part["animal_id"]], part["days"], part["sd"].round(1))),
            hovertemplate="%{customdata[0]}<br>mean %{y:.1f}, SD %{customdata[2]}<br>%{customdata[1]} days<extra></extra>"))
    sel = s[s["animal_id"] == highlight]
    if len(sel):
        fig.add_trace(go.Scatter(x=sel["rank"], y=sel["mean"], mode="markers", name="Selected animal",
                                 marker={"color": "rgba(0,0,0,0)", "size": 15, "line": {"color": CRITICAL, "width": 2}},
                                 hoverinfo="skip"))
    fig.update_xaxes(title="Animals, lowest mean first")
    fig.update_yaxes(title=y_label)
    return fig


def render_explore() -> None:
    intro("Pick a study and an animal. Its record uses the cohort's values, including the daily CH4 from your "
          "GreenFeed visit rules. The KG view tab shows the same animal in the knowledge graph.")
    study, animal, sub = pick_animal(st)
    names = _animal_names()
    rec = sub[sub["animal_id"] == animal].sort_values("Date")
    runs = treatment_runs(rec)
    periods = runs.assign(start=runs["start"].dt.date, end=runs["end"].dt.date) if len(runs) else None
    with card("ex_record", f"Record of animal {names.get(animal, animal)}", "Shaded: treatment periods from the "
              "experimental design records (labels on periods of 3 days or more).", periods):
        if not any(c in rec and rec[c].notna().any() for c, _ in RECORD_PANELS):
            st.info("This animal has no CH4, DMI, milk or body-weight values in the cohort.")
        else:
            n = sum(c in rec and rec[c].notna().any() for c, _ in RECORD_PANELS)
            show(record_panels_figure(rec, runs), 150 * n + 110, top=70)

    labels = {**column_labels(dictionary, "kgAnimalDay"), **calculated_labels()}
    options = numeric_columns(sub)
    with card("ex_spread", "Animals compared with their own day-to-day spread", f"Every animal in study {study}: "
              "its mean (dot) and its day-to-day standard deviation (bar). When the bars are long compared with the "
              "differences between dots, single days say little about an animal and more days are needed (see the "
              "Standardisation lab)."):
        if not options:
            st.info("This study has no numeric measurements in the cohort.")
            return
        keep("ex_value", "CH4_g_d" if "CH4_g_d" in options else options[0], options)
        value = st.selectbox("Measurement", options, key="ex_value", format_func=lambda c: labels.get(c, c))
        spread = animal_spread(sub, value)
        if spread.empty:
            st.info("No animal in this study has values for that measurement.")
            return
        show(spread_figure(spread, animal, names, labels.get(value, value)), 380)
        rep = repeatability(repeatability_frame(sub, value), value)
        if len(rep) and pd.notna(rep["r"].iloc[0]):
            r = rep.iloc[0]
            st.caption(f"Study {study}, after removing treatment and period means: SD between animals "
                       f"**{r['sd_between']:.3g}**, SD within an animal from day to day **{r['sd_within']:.3g}**, "
                       f"repeatability of one day **{r['r']:.2f}**.")


def render_kg() -> None:
    intro("The knowledge graph behind this cohort. Class and link names as in the GEMS ontology (gems.ttl). Counts "
          "are the current cohort's, after the GreenFeed visit rules. A full knowledge-graph explorer (queries, "
          "provenance) will be a page of its own.")
    visits = units = None
    if st.session_state.get("visits_loaded"):
        visits = load_visits(source)
        status = visit_rules_result(source, tuple(sorted(visit_rules.items())))["status"]
        kept = (status == 0) & visits["animal_id"].isin(cohort_animals).to_numpy()
        if date_from is not None:
            kept &= (visits["Date"] >= pd.Timestamp(date_from)).to_numpy()
        if date_to is not None:
            kept &= (visits["Date"] <= pd.Timestamp(date_to)).to_numpy()
        visits = visits[kept]
        units = set(visits["unit"].dropna())
    counts = kg_schema_counts(cdays, tables["kgDiet"], tables["kgDietNutrients"], units)
    what = pd.DataFrame([{"Box": cls, "Count": "–" if counts.get(cls) is None else f"{counts[cls]:,}",
                          "What it counts": text} for cls, (_, _, text) in KG_CLASSES.items()])
    with card("kg_schema", "Structure, with the cohort's counts", "Each box is one kind of thing in the knowledge "
              "graph, with how many of them your cohort has; each arrow is a link between them. Hover a box (or open "
              "the table) to see what it counts.", what):
        st.graphviz_chart(schema_dot(counts), use_container_width=True)
        if visits is None:
            st.caption("GreenFeed units are counted once the GreenFeed visits are loaded (open the GreenFeed QC tab, "
                       "or load them here).")
            if st.button("Load the GreenFeed visits", key="kg_load_visits"):
                st.session_state["visits_loaded"] = True
                st.rerun()

    with card("kg_graph", "Studies and animals in the graph", "Studies: their treatments, each treatment's diet and "
              "the diet's ingredients, for several studies side by side. One animal: the animal picked in the Explore "
              "tab, with its records, treatments, diets and GreenFeed units."):
        keep("kg_view", "Studies", ["Studies", "One animal"])
        view = st.segmented_control("Show", ["Studies", "One animal"], key="kg_view") or "Studies"
        share_unit = _dictionary_unit("kgDiet", "Proportion", "%")
        if view == "Studies":
            _render_kg_studies(share_unit)
            return
        _, animal, _ = pick_animal(st)
        unit_visits = (visits[visits["animal_id"] == animal]["unit"].value_counts().to_dict()
                       if visits is not None else None)
        graph = animal_graph(animal, cdays, animals_t, tables["kgDiet"], tables["kgDietNutrients"], unit_visits)
        st.graphviz_chart(animal_dot(graph, share_unit), use_container_width=True)
        if visits is None:
            st.caption("Its GreenFeed units appear once the GreenFeed visits are loaded (button above).")


def _dictionary_unit(table: str, column: str, default: str = "") -> str:
    """Unit of a kg_v1 column, from the data dictionary."""
    if {"analysisTable", "column", "unit"} <= set(dictionary.columns):
        u = dictionary.loc[(dictionary["analysisTable"] == table) & (dictionary["column"] == column), "unit"]
        u = u.dropna().astype(str).str.strip()
        if len(u) and u.iloc[0]:
            return u.iloc[0]
    return default


def _render_kg_studies(share_unit: str) -> None:
    """Several studies side by side: treatments, diets and ingredient names (shared names link the studies)."""
    first = st.session_state.get("ex_study")
    i = cohort_studies.index(first) if first in cohort_studies else 0
    keep("kg_studies", cohort_studies[i:i + 2] if i + 1 < len(cohort_studies) else cohort_studies[max(0, i - 1):i + 1],
         cohort_studies, multi=True)
    picked = st.multiselect("Studies (up to 6)", cohort_studies, key="kg_studies", max_selections=6)
    if not picked:
        st.info("Pick one or more studies to see their treatments, diets and ingredients.")
        return
    g = study_graph(picked, cdays, animals_t, tables["kgDiet"], tables["kgDietNutrients"])
    st.graphviz_chart(studies_dot(g, share_unit), use_container_width=True)
    st.caption("Solid arrows are KG links: Treatment → Study is belongsToStudy and Treatment → Diet is usesDiet. "
               "Each diet lists its ingredient lines (hasIngredient) with their share of the diet, and its mean "
               "nutrients. Ingredient names used by two or more of these studies are bold and linked on the right "
               "with dashed lines, one per study. Names are matched exactly as recorded, so “Corn silage” and "
               "“corn silage” stay separate. A study shows at most 6 treatments here; the table below has them all.")
    if g["lines"]:
        unit_text = f" ({share_unit})" if share_unit else ""
        table = pd.DataFrame(g["lines"]).rename(columns={"study": "Study", "treatment": "Treatment",
                                                          "name": "Ingredient", "share": f"Share of diet{unit_text}"})
        with st.expander(f"Ingredient lines as a table ({len(table):,})"):
            st.dataframe(table.drop(columns="diet"), hide_index=True, use_container_width=True)


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
    intro("A recipe is the whole selection as data: cohort, GreenFeed visit rules and the variables calculated in "
          "this page. Keep it with your results so the selection can be repeated.")
    left, right = st.columns([1, 1], gap="medium")
    with left, card("exp_recipe", "Recipe", "The choices that define this cohort."):
        recipe_json = json.dumps(recipe, indent=2, default=str, ensure_ascii=False)
        st.code(recipe_json, language="json", height=420)
        st.download_button("Download recipe (JSON)", recipe_json, file_name="gems_cohort_recipe.json",
                           mime="application/json", on_click="ignore", type="primary")
    with right, card("exp_tables", "Tables", "The cohort's rows, ready for R, Python or a spreadsheet. Animal-days "
                     "include the variables calculated in this page; the data dictionary says how each one is "
                     "calculated."):
        render_tables()


def render_tables() -> None:
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
if tab_explore.open:
    with tab_explore:
        render_explore()
if tab_kg.open:
    with tab_kg:
        render_kg()
if tab_export.open:
    with tab_export:
        render_export()
