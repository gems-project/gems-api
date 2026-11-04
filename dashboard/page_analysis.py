"""Analysis page — GEMS data as built from the knowledge graph (kg_v1 analysis tables).

Pick studies, a measurement and a level (animal-day or animal-period means), optionally screen
outliers, look at the values by treatment, and fit a linear mixed model with random intercepts for
study and animal within study. Every number comes from pandas/statsmodels in this process.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

_DASHBOARD_ROOT = Path(__file__).resolve().parent
load_dotenv(_DASHBOARD_ROOT / ".env", override=True)
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from gems_analysis import (  # noqa: E402
    animal_period_means,
    column_labels,
    load_tables,
    numeric_columns,
    robust_outliers,
    source_label,
    synthetic_dir,
)
from gems_auth import require_authorized_user  # noqa: E402
from gems_stats import (  # noqa: E402
    build_formula,
    coefficient_table,
    fit_mixedlm_multi,
    fit_statistics,
    variance_components,
)
from gems_ui import page_header, sidebar_user  # noqa: E402

st.set_page_config(page_title="Analysis · GEMS", layout="wide", page_icon="🧪")
page_header(
    "Analysis",
    "Data built from the GEMS knowledge graph: choose studies and a measurement, "
    "look at it by treatment, and fit a mixed model.",
)
user = require_authorized_user()
sidebar_user(user)

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

studies, days, dictionary = tables["kgStudy"], tables["kgAnimalDay"], tables["kgDataDictionary"]
if days.empty:
    st.info("There are no analysis tables yet. They appear after the KG job has run.")
    st.stop()

# ---------------------------------------------------------------------------
# 1. Studies
# ---------------------------------------------------------------------------
st.subheader("1 · Studies")
study_cols = [c for c in ["studyId", "isOpenData", "ProductionType", "ExperimentalDesign", "nAnimals",
                          "nAnimalDays", "nVisits", "nMatchedVisits", "firstDate", "lastDate"] if c in studies]
st.dataframe(studies[study_cols], hide_index=True, use_container_width=True,
             column_config={"isOpenData": st.column_config.CheckboxColumn("Open data")})
all_ids = sorted(studies["studyId"].astype(str))
chosen = st.multiselect("Studies to include", all_ids, default=all_ids)
if not chosen:
    st.info("Choose at least one study.")
    st.stop()

# ---------------------------------------------------------------------------
# 2. Measurement and level
# ---------------------------------------------------------------------------
st.subheader("2 · Measurement")
labels = column_labels(dictionary, "kgAnimalDay")
data = days[days["studyId"].isin(chosen)]
values = numeric_columns(data)
if not values:
    st.info("The chosen studies have no numeric measurements.")
    st.stop()
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
    st.stop()

# ---------------------------------------------------------------------------
# 3. Outlier screening (off by default)
# ---------------------------------------------------------------------------
st.subheader("3 · Outlier screening")
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

# ---------------------------------------------------------------------------
# 4. Look at the data
# ---------------------------------------------------------------------------
st.subheader("4 · By treatment")
plot = data.assign(Status=np.where(removed, "Removed", "Kept"))
fig = px.strip(plot, x="Treatment", y=response, color="Status", facet_col="studyId", facet_col_wrap=4,
               color_discrete_map={"Kept": "#2E7D32", "Removed": "#C62828"},
               labels={response: labels.get(response, response)}, stripmode="overlay")
fig.update_traces(marker={"size": 4, "opacity": 0.55}, jitter=0.4)
fig.update_layout(height=380 if len(chosen) <= 4 else 700, legend_title_text="", margin={"t": 40})
st.plotly_chart(fig, use_container_width=True)
summary = (kept.groupby(["studyId", "Treatment"])[response]
           .agg(n="count", mean="mean", sd="std", median="median").round(2).reset_index())
st.dataframe(summary, hide_index=True, use_container_width=True)

# ---------------------------------------------------------------------------
# 5. Mixed model
# ---------------------------------------------------------------------------
st.subheader("5 · Mixed model")
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
if st.button("Fit model", type="primary"):
    try:
        with st.spinner("Fitting…"):
            model = fit_mixedlm_multi(model_data, build_formula(response, ["Treatment"] + covariates), group_cols, nested=True)
    except Exception as exc:  # noqa: BLE001
        st.error(f"The model could not be fitted: {exc}")
        st.stop()
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
