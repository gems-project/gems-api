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
    FACETS,
    HAS,
    animal_frame,
    animal_period_means,
    cohort_days,
    column_labels,
    facet_counts,
    facet_masks,
    load_tables,
    numeric_columns,
    robust_outliers,
    source_label,
    study_overview,
    synthetic_dir,
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
user = require_authorized_user()
sidebar_user(user)
user_info = get_current_user_info()

source = source_label()
if synthetic_dir():
    st.warning("Synthetic test data: every value is invented. This is not GEMS data.", icon="🧪")
if st.sidebar.button("Reload data"):
    load_tables.clear()
    st.rerun()
st.sidebar.caption(f"Source: {source}")

try:
    tables = load_tables(source)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not load the analysis tables from {source}: {exc}")
    st.stop()

studies, animals_t, days, dictionary = tables["kgStudy"], tables["kgAnimal"], tables["kgAnimalDay"], tables["kgDataDictionary"]
if days.empty:
    st.info("There are no analysis tables yet. They appear after the KG job has run.")
    st.stop()

# ---------------------------------------------------------------------------
# Cohort (sidebar): feeds every tab
# ---------------------------------------------------------------------------
animals, additives = animal_frame(studies, animals_t, days)

st.sidebar.markdown("### Cohort")
st.sidebar.caption("Every tab uses this selection. The number after each choice counts the animals "
                   "that also match your other choices.")
if st.sidebar.button("Clear choices"):
    for key in FACETS:
        st.session_state.pop(f"facet_{key}", None)
    st.session_state.pop("cohort_has", None)
    st.session_state.pop("cohort_open", None)
    st.session_state.pop("cohort_dates", None)
open_only = st.sidebar.toggle("Open-data studies only", key="cohort_open")
has = st.sidebar.multiselect("Must have", list(HAS), default=["CH4"], key="cohort_has",
                             help="Keep animals with at least one day of each of these measurements.")
base = pd.Series(True, index=animals.index)
for label in has:
    base &= animals[label] > 0
if open_only:
    base &= animals["isOpenData"]

chosen = {k: list(st.session_state.get(f"facet_{k}", [])) for k in FACETS}
masks = facet_masks(animals, additives, chosen)
counts = facet_counts(animals, additives, masks, base)
for key, (label, col) in FACETS.items():
    values = sorted(set(additives[col]) if key == "additive" else set(animals[col]), key=str)
    n = counts[key]
    st.sidebar.multiselect(label, values, key=f"facet_{key}",
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
cdays = cohort_days(days, set(animals.loc[ok, "animal_id"]), date_from, date_to, chosen["additive"])
cohort_studies = sorted(cdays["studyId"].astype(str).unique())
cohort_animals = set(cdays["animal_id"])
st.sidebar.markdown(f"**{len(cohort_studies)}** studies · **{len(cohort_animals):,}** animals · "
                    f"**{len(cdays):,}** animal-days")

recipe = {
    "source": source,
    "made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "openDataOnly": bool(open_only),
    "mustHave": {label: HAS[label] for label in has},
    "facets": {FACETS[k][1]: v for k, v in chosen.items() if v},
    "dateFrom": str(date_from) if date_from else None,
    "dateTo": str(date_to) if date_to else None,
    "result": {"studies": cohort_studies, "animals": len(cohort_animals), "animalDays": len(cdays)},
}

if cdays.empty:
    st.info("No animal-days match this cohort. Loosen the choices in the sidebar.")
    st.stop()

tab_overview, tab_model, tab_export = st.tabs(["Overview", "Model", "Export"])


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def render_overview() -> None:
    ch4_days = int(cdays["CH4_g_d"].notna().sum()) if "CH4_g_d" in cdays else 0
    visits = int(cdays["nVisits"].sum()) if "nVisits" in cdays else 0
    t = st.columns(3) + st.columns(3)
    t[0].metric(f"Studies (of {studies['studyId'].nunique()})", f"{len(cohort_studies)}")
    t[1].metric(f"Animals (of {len(animals):,})", f"{len(cohort_animals):,}")
    t[2].metric("Animal-days", f"{len(cdays):,}", help="Days with any measurement or design record.")
    t[3].metric("CH4 days", f"{ch4_days:,}", help="Animal-days with a GreenFeed CH4 mean (matched visits, CH4 > 0).")
    t[4].metric("GreenFeed visits", f"{visits:,}", help="Visits matched to an animal, with CH4 > 0.")
    t[5].metric("Date span", f"{cdays['Date'].min():%Y-%m} → {cdays['Date'].max():%Y-%m}")

    st.markdown("#### Studies in the cohort")
    st.caption("Design is inferred from the data: *within-animal* when animals changed treatment (crossover or "
               "switchback), *between-animal* when each kept one treatment.")
    ov = study_overview(cdays, studies)
    st.dataframe(ov, hide_index=True, use_container_width=True,
                 column_config={"Open data": st.column_config.CheckboxColumn("Open data")})

    left, right = st.columns(2)
    with left:
        st.markdown("#### When the studies ran")
        tl = ov.assign(Start=pd.to_datetime(ov["First"]), End=pd.to_datetime(ov["Last"]) + pd.Timedelta(days=1))
        fig = px.timeline(tl.sort_values("Start"), x_start="Start", x_end="End", y="Study", color="Type",
                          hover_data={"Animals": True, "Animal-days": True, "Location": True})
        fig.update_yaxes(autorange="reversed", title=None, type="category")
        fig.update_layout(height=max(260, 22 * len(tl) + 90), margin={"t": 10, "b": 10}, legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)
    with right:
        st.markdown("#### Coverage: animals × days")
        by_ch4 = ov.sort_values("CH4 days", ascending=False)["Study"].tolist()
        study = st.selectbox("Study", by_ch4, help="Studies ordered by CH4 days in the cohort.")
        sub = cdays[cdays["studyId"].astype(str) == str(study)]
        label_col = "AnimalIdentifier" if "AnimalIdentifier" in sub and sub["AnimalIdentifier"].notna().any() else "animal_id"
        sub = sub.assign(_animal=sub[label_col].astype(str),
                         _visits=sub["nVisits"].fillna(0) if "nVisits" in sub else 0,
                         _trt=sub["Treatment"].fillna("–").astype(str) if "Treatment" in sub else "–")
        z = sub.pivot_table(index="_animal", columns="Date", values="_visits", aggfunc="sum")
        trt = sub.pivot_table(index="_animal", columns="Date", values="_trt", aggfunc="first").reindex_like(z)
        heat = go.Figure(go.Heatmap(
            z=z.values, x=z.columns, y=z.index, customdata=trt.values, colorscale="Greens", zmin=0,
            colorbar={"title": "Visits"}, xgap=1, ygap=1,
            hovertemplate="Animal %{y}<br>%{x|%Y-%m-%d}<br>Visits: %{z}<br>Treatment: %{customdata}<extra></extra>"))
        heat.update_layout(height=min(900, max(260, 16 * len(z) + 90)), margin={"t": 10, "b": 10},
                           yaxis={"type": "category", "title": None})
        st.plotly_chart(heat, use_container_width=True)
        st.caption("Cells: GreenFeed visits that day (0 = a day with other records but no visit; blank = no record). "
                   "Hover for the treatment.")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def render_model() -> None:
    labels = column_labels(dictionary, "kgAnimalDay")
    data = cdays
    values = numeric_columns(data)
    if not values:
        st.info("The cohort has no numeric measurements.")
        return
    st.markdown("#### Measurement")
    c1, c2, c3 = st.columns([2, 2, 3])
    response = c1.selectbox("Measurement", values, index=values.index("CH4_g_d") if "CH4_g_d" in values else 0,
                            format_func=lambda c: labels.get(c, c))
    level = c2.radio("Level", ["Animal-day", "Animal-period mean"], horizontal=True,
                     help="Animal-period mean: one value per animal, treatment and period.")
    covariates = c3.multiselect("Covariates (optional)", [v for v in values if v != response],
                                format_func=lambda c: labels.get(c, c))
    used = [response] + covariates
    if level == "Animal-period mean":
        data = animal_period_means(data, used)
    data = data.dropna(subset=used + ["Treatment"]).reset_index(drop=True)
    if data.empty:
        st.info("No records have all the chosen measurements and a treatment.")
        return

    st.markdown("#### Outlier screening")
    screen = st.toggle("Remove outliers", value=False,
                       help="Off by default. Values outside the plausible range are original source data; "
                            "decide here whether to screen them, and see what is removed.")
    removed = pd.Series(False, index=data.index)
    if screen:
        s1, s2, s3 = st.columns([3, 2, 1])
        screen_cols = s1.multiselect("Screen these measurements", used, default=[response],
                                     format_func=lambda c: labels.get(c, c))
        within = s2.radio("Within", ["Study", "Study × treatment"], horizontal=True)
        threshold = s3.number_input("Robust z", min_value=2.0, max_value=10.0, value=3.5, step=0.5)
        st.caption("A value is flagged when |x − median| / (1.4826 × MAD) is above the threshold within its group "
                   "(Iglewicz & Hoaglin). Groups with fewer than 10 values are not screened. A record is removed "
                   "when any screened measurement is flagged.")
        if screen_cols:
            groups = ["studyId"] + (["Treatment"] if within == "Study × treatment" else [])
            removed = robust_outliers(data, screen_cols, groups, threshold).any(axis=1)
            per_study = (data.assign(removed=removed).groupby("studyId")
                         .agg(records=("removed", "size"), removed=("removed", "sum"),
                              animals_affected=("animal_id", lambda a: a[removed[a.index]].nunique())))
            st.dataframe(per_study.reset_index(), hide_index=True, use_container_width=True)
    kept = data[~removed]

    st.markdown("#### By treatment")
    plot = data.assign(Status=np.where(removed, "Removed", "Kept"))
    n_studies = plot["studyId"].nunique()
    fig = px.strip(plot, x="Treatment", y=response, color="Status", facet_col="studyId", facet_col_wrap=4,
                   color_discrete_map={"Kept": "#2E7D32", "Removed": "#C62828"},
                   labels={response: labels.get(response, response)}, stripmode="overlay")
    fig.update_traces(marker={"size": 4, "opacity": 0.55}, jitter=0.4)
    fig.update_layout(height=380 if n_studies <= 4 else 260 * int(np.ceil(n_studies / 4)),
                      legend_title_text="", margin={"t": 40})
    st.plotly_chart(fig, use_container_width=True)
    summary = (kept.groupby(["studyId", "Treatment"])[response]
               .agg(n="count", mean="mean", sd="std", median="median").round(2).reset_index())
    st.dataframe(summary, hide_index=True, use_container_width=True)

    st.markdown("#### Mixed model")
    group_cols = ["studyId", "animal_id"] if kept["studyId"].nunique() > 1 else ["animal_id"]
    rhs = " + ".join(["Treatment"] + covariates)
    random_text = "study and animal within study" if len(group_cols) == 2 else "animal"
    treatments = sorted(kept["Treatment"].astype(str).unique())
    reference = st.selectbox("Reference treatment", treatments,
                             index=treatments.index("Control") if "Control" in treatments else 0,
                             help="The other treatments are estimated as differences from this one.")
    model_data = kept.assign(Treatment=pd.Categorical(kept["Treatment"].astype(str),
                                                      categories=[reference] + [t for t in treatments if t != reference]))
    st.markdown(f"`{response} ~ {rhs}` with random intercepts for {random_text}; {len(kept):,} records.")
    if not st.button("Fit model", type="primary"):
        return
    try:
        with st.spinner("Fitting…"):
            model = fit_mixedlm_multi(model_data, build_formula(response, ["Treatment"] + covariates), group_cols, nested=True)
    except Exception as exc:  # noqa: BLE001
        st.error(f"The model could not be fitted: {exc}")
        return
    stats = fit_statistics(model, "mixed")
    m1, m2, m3 = st.columns(3)
    m1.metric("Observations", f"{int(stats.get('nobs') or 0):,}")
    m2.metric("Log-likelihood", f"{stats['llf']:.1f}" if stats.get("llf") is not None else "—")
    m3.metric("Converged", "Yes" if getattr(model, "converged", False) else "No")
    coefs = coefficient_table(model)
    coefs = coefs[~coefs["term"].astype(str).str.endswith(" Var")]  # variances are shown below
    if "p_value" in coefs:
        coefs["p_value"] = coefs["p_value"].map(lambda p: "" if pd.isna(p) else "<0.001" if p < 0.001 else f"{p:.3f}")
    st.markdown("**Fixed effects**")
    st.dataframe(coefs.round(3), hide_index=True, use_container_width=True)
    names = {"Group": group_cols[0], "animal_id": "animal within study" if len(group_cols) == 2 else "animal"}
    variances = pd.DataFrame(variance_components(model)).replace({"name": names})
    st.markdown("**Variance components**")
    st.dataframe(variances.round(3), hide_index=True, use_container_width=True)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def _csv(frame: pd.DataFrame, item: str):
    """Deferred CSV for st.download_button: built, and logged, only when the user clicks."""
    def make() -> bytes:
        log_download(user_info.email, "analysis", item, len(frame), cohort_studies, recipe)
        return frame.to_csv(index=False).encode("utf-8")
    return make


def render_export() -> None:
    st.markdown("#### Recipe")
    st.caption("The choices that define this cohort. Keep it with your results so the selection can be repeated.")
    recipe_json = json.dumps(recipe, indent=2, default=str)
    st.code(recipe_json, language="json")
    st.download_button("Download recipe (JSON)", recipe_json, file_name="gems_cohort_recipe.json",
                       mime="application/json", on_click="ignore")

    st.markdown("#### Tables")
    st.download_button(f"Data dictionary — {len(dictionary):,} rows (CSV)", dictionary.to_csv(index=False),
                       file_name="gems_kg_data_dictionary.csv", mime="text/csv", on_click="ignore",
                       help="Column labels, units and ontology terms. No data values.")
    can_download = bool(synthetic_dir()) or has_api_access(user_info, user_info.bearer_token)
    if not can_download:
        st.info("Downloading the cohort's data is limited to users on the API access list. "
                "Ask the GEMS team if you need access.")
        return
    st.caption("Each download is logged with your email, the table, the row count and the recipe above.")
    in_studies = lambda df: df[df["studyId"].astype(str).isin(cohort_studies)] if "studyId" in df else df.iloc[0:0]  # noqa: E731
    items = [
        ("Animal-days", "animal_days", cdays.assign(Date=cdays["Date"].dt.date)),
        ("Animals", "animals", animals_t[animals_t["animal_id"].isin(cohort_animals)]),
        ("Studies", "studies", in_studies(studies)),
        ("Diets (ingredients)", "diets", in_studies(tables["kgDiet"])),
        ("Diet nutrients", "diet_nutrients", in_studies(tables["kgDietNutrients"])),
    ]
    for label, item, frame in items:
        if frame.empty:
            continue
        st.download_button(f"{label} — {len(frame):,} rows (CSV)", _csv(frame, item),
                           file_name=f"gems_cohort_{item}.csv", mime="text/csv", on_click="ignore", key=f"dl_{item}")


with tab_overview:
    render_overview()
with tab_model:
    render_model()
with tab_export:
    render_export()
