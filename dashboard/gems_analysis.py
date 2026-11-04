"""Data layer for the Analysis page: the kg_v1 analysis tables built from the knowledge graph.

Two sources:
- Databricks (default): `gems_catalog`.`kg_v1`.<table>, written by the KG job (KG03Publish).
- Synthetic test data: set GEMS_ANALYSIS_SYNTHETIC_DIR to a folder of <table>.parquet files made by
  tools_make_synthetic.py + the KG job's kg_local.py. In this mode the page never opens a Databricks
  connection, so local testing cannot pull real data.

Also holds the pure helpers the page uses: animal-period means and robust outlier flags.
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
