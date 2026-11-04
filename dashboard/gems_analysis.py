"""Data layer for the Analysis page: the kg_v1 analysis tables built from the knowledge graph.

Two sources:
- Databricks (default): `gems_catalog`.`kg_v1`.<table>, written by the KG job (KG03Publish).
- Synthetic test data: set GEMS_ANALYSIS_SYNTHETIC_DIR to a folder of <table>.parquet files made by
  tools_make_synthetic.py + the KG job's kg_local.py. In this mode the page never opens a Databricks
  connection, so local testing cannot pull real data.

Also holds the pure helpers the page uses: the cohort (facets, date range, required measurements),
per-study overview, animal-period means and robust outlier flags.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

TABLES = ["kgStudy", "kgAnimal", "kgAnimalDay", "kgDiet", "kgDietNutrients", "kgDataDictionary"]
ID_COLUMNS = {"studyId", "animal_id", "AnimalIdentifier", "Date", "treatment_id", "Treatment", "Period"}
MAD_SCALE = 1.4826  # makes the MAD comparable to an SD for normal data

# Cohort facets: key -> (label, column of the animal frame). "additive" is per day, see animal_frame.
FACETS = {
    "production": ("Production type", "ProductionType"),
    "animal_type": ("Animal type", "AnimalType"),
    "breed": ("Breed", "Breed"),
    "location": ("Experimental location", "ExperimentalLocation"),
    "additive": ("Additive (as recorded)", "MethaneInhibitingFeedAdditive"),
    "study": ("Studies", "studyId"),
}
# "Must have" measurements: label -> kgAnimalDay column. An animal qualifies with at least one day of it.
HAS = {"CH4": "CH4_g_d", "Dry matter intake": "DryMatterIntake", "Milk yield": "MilkProduction", "Body weight": "BodyWeight"}
UNKNOWN = "(unknown)"


def synthetic_dir() -> Path | None:
    raw = os.environ.get("GEMS_ANALYSIS_SYNTHETIC_DIR", "").strip()
    return Path(raw) if raw else None


def source_label() -> str:
    d = synthetic_dir()
    return f"Synthetic test data ({d.name})" if d else "Databricks kg_v1"


def _read_synthetic(d: Path, table: str) -> pd.DataFrame:
    path = d / f"{table}.parquet"
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def _read_databricks(table: str) -> pd.DataFrame:
    from gems_data import _connect  # imported here so synthetic mode never touches Databricks

    catalog = os.environ.get("GEMS_CATALOG", "gems_catalog").strip()
    schema = os.environ.get("GEMS_KG_SCHEMA", "kg_v1").strip()
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM `{catalog}`.`{schema}`.`{table}`")
        return cur.fetchall_arrow().to_pandas()


@st.cache_data(ttl=600, show_spinner="Loading analysis tables…")
def load_tables(source: str) -> dict[str, pd.DataFrame]:
    """All analysis tables. `source` is part of the cache key so the two modes never share a cache."""
    d = synthetic_dir()
    read = (lambda t: _read_synthetic(d, t)) if d else _read_databricks
    out = {t: read(t) for t in TABLES}
    days = out["kgAnimalDay"]
    if len(days):
        days["Date"] = pd.to_datetime(days["Date"], errors="coerce")
    return out


def animal_frame(studies: pd.DataFrame, animals: pd.DataFrame, days: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(one row per animal with its facet values and days per required measurement,
    animal_id x additive pairs for the per-day additive facet)."""
    keys = pd.concat([animals[["studyId", "animal_id"]], days[["studyId", "animal_id"]]]).drop_duplicates("animal_id")
    cols = [c for c in ["animal_id", "ProductionType", "AnimalType", "Breed"] if c in animals]
    out = keys.merge(animals[cols], on="animal_id", how="left")
    study_cols = [c for c in ["studyId", "isOpenData", "ProductionType", "ExperimentalLocation"] if c in studies]
    out = out.merge(studies[study_cols].rename(columns={"ProductionType": "_studyProduction"}), on="studyId", how="left")
    if "_studyProduction" in out:
        out["ProductionType"] = out.get("ProductionType", pd.Series(index=out.index, dtype=object)).fillna(out.pop("_studyProduction"))
    for _, col in FACETS.values():
        if col not in out and col != "MethaneInhibitingFeedAdditive":
            out[col] = None
    for col in ["ProductionType", "AnimalType", "Breed", "ExperimentalLocation"]:
        out[col] = out[col].fillna(UNKNOWN).astype(str).replace({"": UNKNOWN})
    out["isOpenData"] = out.get("isOpenData", pd.Series(False, index=out.index)).fillna(False).astype(bool)
    for label, col in HAS.items():
        n = days.loc[days[col].notna(), "animal_id"].value_counts() if col in days else pd.Series(dtype=int)
        out[label] = out["animal_id"].map(n).fillna(0).astype(int)
    add_col = FACETS["additive"][1]
    additives = (days[["animal_id", add_col]].fillna({add_col: UNKNOWN}).drop_duplicates() if add_col in days
                 else pd.DataFrame(columns=["animal_id", add_col]))
    return out.reset_index(drop=True), additives


def facet_masks(animals: pd.DataFrame, additives: pd.DataFrame, chosen: dict[str, list[str]]) -> dict[str, pd.Series]:
    """Per facet: True for animals that match its selection (an empty selection matches every animal)."""
    masks = {}
    for key, (_, col) in FACETS.items():
        sel = chosen.get(key) or []
        if not sel:
            masks[key] = pd.Series(True, index=animals.index)
        elif key == "additive":
            ids = set(additives.loc[additives[col].isin(sel), "animal_id"])
            masks[key] = animals["animal_id"].isin(ids)
        else:
            masks[key] = animals[col].isin(sel)
    return masks


def facet_counts(animals: pd.DataFrame, additives: pd.DataFrame, masks: dict[str, pd.Series],
                 base: pd.Series) -> dict[str, pd.Series]:
    """Per facet: animals per value among those matching `base` and every OTHER facet (so a list never
    empties itself as the user picks values from it)."""
    out = {}
    for key, (_, col) in FACETS.items():
        ok = base.copy()
        for other, m in masks.items():
            if other != key:
                ok &= m
        if key == "additive":
            out[key] = additives[additives["animal_id"].isin(animals.loc[ok, "animal_id"])][col].value_counts()
        else:
            out[key] = animals.loc[ok, col].value_counts()
    return out


def cohort_days(days: pd.DataFrame, animal_ids: set, date_from, date_to, additive: list[str]) -> pd.DataFrame:
    """The animal-day rows of the cohort: chosen animals, inside the date range, on a chosen additive (if any)."""
    keep = days["animal_id"].isin(animal_ids)
    if date_from is not None:
        keep &= days["Date"] >= pd.Timestamp(date_from)
    if date_to is not None:
        keep &= days["Date"] <= pd.Timestamp(date_to)
    add_col = FACETS["additive"][1]
    if additive and add_col in days:
        keep &= days[add_col].fillna(UNKNOWN).isin(additive)
    return days[keep]


def study_overview(days: pd.DataFrame, studies: pd.DataFrame) -> pd.DataFrame:
    """One row per study in the cohort. Design is inferred from the data: within-animal when animals changed
    treatment (crossover / switchback), between-animal when each kept one."""
    if days.empty:
        return pd.DataFrame()
    g = days.groupby("studyId")
    trt_per_animal = days.dropna(subset=["Treatment"]).groupby(["studyId", "animal_id"])["Treatment"].nunique()
    max_trt = trt_per_animal.groupby("studyId").max()
    visits = days["nVisits"] if "nVisits" in days else pd.Series(np.nan, index=days.index)
    out = pd.DataFrame({
        "Animals": g["animal_id"].nunique(),
        "Treatments": g["Treatment"].nunique() if "Treatment" in days else 0,
        "Periods": g["Period"].nunique() if "Period" in days else 0,
        "Animal-days": g.size(),
        "CH4 days": g["CH4_g_d"].count() if "CH4_g_d" in days else 0,
        "Visits": visits.groupby(days["studyId"]).sum(min_count=1),
        "GF days": visits.gt(0).groupby(days["studyId"]).sum(),
        "First": g["Date"].min().dt.date,
        "Last": g["Date"].max().dt.date,
    })
    out["Visits / animal / GF day"] = (out["Visits"] / out["GF days"].replace(0, np.nan)).round(1)
    out["Design"] = max_trt.reindex(out.index).map(lambda m: "within-animal" if m > 1 else "between-animal" if m == 1 else "none recorded")
    info_cols = [c for c in ["studyId", "isOpenData", "ProductionType", "ExperimentalLocation"] if c in studies]
    info = studies[info_cols].set_index("studyId")
    out = info.join(out, how="inner").reset_index()
    out = out.rename(columns={"studyId": "Study", "isOpenData": "Open data", "ProductionType": "Type",
                              "ExperimentalLocation": "Location"})
    order = ["Study", "Open data", "Type", "Location", "Design", "Periods", "Animals", "Treatments", "Animal-days",
             "CH4 days", "Visits", "Visits / animal / GF day", "First", "Last"]
    return out[[c for c in order if c in out]]


def numeric_columns(df: pd.DataFrame) -> list[str]:
    skip = ID_COLUMNS | {c for c in df.columns if c.endswith("_id") or c.startswith("n")}
    return [c for c in df.columns if c not in skip and pd.api.types.is_numeric_dtype(df[c]) and df[c].notna().any()]


def column_labels(dictionary: pd.DataFrame, table: str) -> dict[str, str]:
    """column -> 'column (unit)' for the variable pickers."""
    if dictionary.empty:
        return {}
    d = dictionary[dictionary["analysisTable"] == table]
    labels = {}
    for r in d.itertuples():
        unit = r.unit if isinstance(r.unit, str) and r.unit else ("g/d" if r.column.endswith("_g_d") else "")
        labels[r.column] = f"{r.column} ({unit})" if unit else r.column
    return labels


def animal_period_means(days: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    """One row per study x animal x treatment x period: the mean of each value over its days."""
    keys = [c for c in ["studyId", "animal_id", "AnimalIdentifier", "Treatment", "Period"] if c in days.columns]
    g = days.groupby(keys, dropna=False, observed=True)
    out = g[value_cols].mean()
    out["nDays"] = g.size()
    return out.reset_index()


def robust_outliers(df: pd.DataFrame, cols: list[str], group_cols: list[str],
                    threshold: float = 3.5, min_n: int = 10) -> pd.DataFrame:
    """Per column: True where |x - median| / (1.4826 * MAD) > threshold within its group (Iglewicz & Hoaglin).

    Groups with fewer than `min_n` values, or with MAD = 0, are not screened (all False).
    """
    flags = pd.DataFrame(False, index=df.index, columns=cols)
    for col in cols:
        x = df[col]
        grouped = x.groupby([df[g] for g in group_cols], dropna=False) if group_cols else x.groupby(np.zeros(len(x)))
        med = grouped.transform("median")
        mad = (x - med).abs().groupby([df[g] for g in group_cols], dropna=False).transform("median") if group_cols \
            else (x - med).abs().groupby(np.zeros(len(x))).transform("median")
        n = grouped.transform("count")
        z = (x - med).abs() / (MAD_SCALE * mad.replace(0, np.nan))
        flags[col] = (z > threshold) & (n >= min_n)
    return flags
