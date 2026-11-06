"""Data layer for the Analysis page: the kg_v1 analysis tables built from the knowledge graph.

Two sources:
- Databricks (default): `gems_catalog`.`kg_v1`.<table>, written by the KG job (KG03Publish).
- Synthetic test data: set GEMS_ANALYSIS_SYNTHETIC_DIR to a folder of <table>.parquet files made by
  tools_make_synthetic.py + the KG job's kg_local.py. In this mode the page never opens a Databricks
  connection, so local testing cannot pull real data.

Also holds the pure helpers the page uses: the cohort (facets, date range, required measurements),
per-study overview, animal-period means, robust outlier flags, and the variables the page calculates
per cow-day (methane yield, FPCM, nutrient intakes, ...; see CALCULATED), which are not stored anywhere.
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


def read_tables() -> dict[str, pd.DataFrame]:
    """All analysis tables, read now (synthetic files or Databricks)."""
    d = synthetic_dir()
    read = (lambda t: _read_synthetic(d, t)) if d else _read_databricks
    out = {t: read(t) for t in TABLES}
    days = out["kgAnimalDay"]
    if len(days):
        days["Date"] = pd.to_datetime(days["Date"], errors="coerce")
    return out


@st.cache_resource(ttl=12 * 3600, show_spinner="Loading analysis tables…")
def load_dataset(source: str) -> dict:
    """Everything the page needs that depends only on the tables, prepared once.

    Shared by every user and every click: st.cache_resource hands back the same objects without copying
    them (st.cache_data copied ~130 MB per click). So the page must NEVER change these frames in place;
    filter or .assign() instead. Kept until "Reload data", an app restart, or 12 hours. `source` is part
    of the cache key, so synthetic and real data never share it.
    """
    tables = read_tables()
    days = tables["kgAnimalDay"]
    if len(days):
        # Calculated variables are per cow-day and do not depend on the cohort: do them once here.
        days = add_calculated(days, tables["kgDietNutrients"])
        tables["kgAnimalDay"] = days
        animals, additives = animal_frame(tables["kgStudy"], tables["kgAnimal"], days)
    else:
        animals, additives = pd.DataFrame(), pd.DataFrame()
    return {"tables": tables, "animals": animals, "additives": additives,
            "loadedAt": pd.Timestamp.now(tz="UTC")}


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
    # only the three columns needed: dropping blanks on the whole frame copied ~60 columns
    trt_per_animal = (days[["studyId", "animal_id", "Treatment"]].dropna(subset=["Treatment"])
                      .groupby(["studyId", "animal_id"])["Treatment"].nunique())
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


def plot_sample(df: pd.DataFrame, by: list[str], per_group: int = 300, seed: int = 1) -> pd.DataFrame:
    """At most `per_group` random rows per group, for drawing only (summaries and models use every row).

    Plotting every record of a large cohort sent megabytes to the browser; a fixed seed keeps the same
    points on every redraw.
    """
    if df.empty:
        return df
    group = np.zeros(len(df), dtype=np.int64)  # one integer per group (blank counts as its own value)
    for c in by:
        codes, uniques = pd.factorize(df[c], use_na_sentinel=False)
        group = group * (len(uniques) + 1) + codes
    order = np.random.default_rng(seed).permutation(len(df))
    place = pd.Series(group[order]).groupby(group[order]).cumcount().to_numpy()  # position in the shuffle
    keep = np.zeros(len(df), dtype=bool)
    keep[order[place < per_group]] = True
    return df[keep]


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


# ---------------------------------------------------------------------------
# Variables calculated per cow-day in the page (not stored in Databricks or the KG)
# ---------------------------------------------------------------------------
CH4_MJ_PER_G = 0.05565  # energy content of methane: 55.65 MJ/kg (IPCC)

# kgDietNutrients column (g/kg DM) -> (intake column, short name). Intake = DMI x content / 1000, in kg/d.
DIET_NUTRIENTS = {
    "CrudeProtein": ("CrudeProteinIntake", "crude protein"),
    "NeutralDetergentFiber": ("NeutralDetergentFiberIntake", "NDF"),
    "AcidDetergentFiber": ("AcidDetergentFiberIntake", "ADF"),
    "Starch": ("StarchIntake", "starch"),
    "EtherExtract": ("EtherExtractIntake", "ether extract"),
    "OrganicMatter": ("OrganicMatterIntake", "organic matter"),
}
DIET_NOTE = ("Diet values: the average of all analyses of the cow's diet that day (offered diet, so sorting "
             "and refusals are ignored). Days with two treatments recorded are left out.")

# column -> (label, unit, how it is calculated, extra note or "")
CALCULATED = {
    "MethaneYield": ("Methane yield", "g/kg DMI", "CH4 ÷ DMI", "Most studies' DMI leaves out the GreenFeed pellets."),
    "MethaneIntensityMilk": ("Methane intensity, milk", "g/kg milk", "CH4 ÷ milk yield", ""),
    "FPCM": ("Fat- and protein-corrected milk", "kg/d",
             "milk × (0.1226 × fat % + 0.0722 × crude protein % + 0.2534), IDF 2022", ""),
    "MethaneIntensityFPCM": ("Methane intensity, FPCM", "g/kg FPCM", "CH4 ÷ FPCM", ""),
    "MilkFatYield": ("Milk fat yield", "kg/d", "milk × fat % ÷ 100", ""),
    "MilkProteinYield": ("Milk protein yield", "kg/d", "milk × crude protein % ÷ 100", ""),
    "FeedEfficiency": ("Feed efficiency", "kg FPCM/kg DMI", "FPCM ÷ DMI", ""),
    "ForageShare": ("Forage share of intake", "%", "forage intake ÷ DMI × 100", ""),
    "MethanePerMetabolicBW": ("Methane per metabolic body weight", "g/kg BW^0.75", "CH4 ÷ BW^0.75",
                              "Only on days the cow was weighed."),
    "DMIPercentBW": ("DMI as % of body weight", "%", "DMI ÷ BW × 100", "Only on days the cow was weighed."),
    **{col: (f"{name[0].upper()}{name[1:]} intake", "kg/d", f"DMI × diet {name} (g/kg DM) ÷ 1000", DIET_NOTE)
       for col, name in DIET_NUTRIENTS.values()},
    "GrossEnergyIntakeAll": ("Gross energy intake, recorded or from diet", "MJ/d",
                             "recorded GE intake; where missing, DMI × diet GE (MJ/kg DM)",
                             "GrossEnergyIntakeSource says which. " + DIET_NOTE),
    "Ym": ("Methane conversion factor (Ym)", "% of GE intake", "CH4 × 0.05565 MJ/g ÷ GE intake × 100",
           "Uses the GE intake above (recorded, else from diet)."),
}


def _positive(s: pd.Series) -> pd.Series:
    """Numbers > 0; anything else (blank, 0, negative, text) becomes missing, so it is never divided by."""
    s = pd.to_numeric(s, errors="coerce")
    return s.where(s > 0)


def diet_means(diet_nutrients: pd.DataFrame) -> pd.DataFrame:
    """One row per treatment_id: the mean of each nutrient over all analyses of that treatment's diet."""
    cols = [c for c in [*DIET_NUTRIENTS, "GrossEnergy"] if c in diet_nutrients]
    if diet_nutrients.empty or "treatment_id" not in diet_nutrients or not cols:
        return pd.DataFrame(columns=cols)
    values = diet_nutrients[cols].apply(pd.to_numeric, errors="coerce")
    return values.groupby(diet_nutrients["treatment_id"]).mean()


def add_calculated(days: pd.DataFrame, diet_nutrients: pd.DataFrame) -> pd.DataFrame:
    """`days` (kgAnimalDay rows) plus the CALCULATED columns, each for one cow on one day.

    A value is missing when one of its inputs is missing on that day; zero or negative denominators count
    as missing.
    """
    out = days.copy()
    blank = pd.Series(np.nan, index=out.index)

    def col(name: str) -> pd.Series:
        return pd.to_numeric(out[name], errors="coerce") if name in out else blank

    ch4, dmi = _positive(col("CH4_g_d")), _positive(col("DryMatterIntake"))
    milk, bw = _positive(col("MilkProduction")), _positive(col("BodyWeight"))
    fat, protein = col("MilkFat"), col("MilkProtein")

    out["MethaneYield"] = ch4 / dmi
    out["MethaneIntensityMilk"] = ch4 / milk
    fpcm = milk * (0.1226 * fat + 0.0722 * protein + 0.2534)
    out["FPCM"] = fpcm
    out["MethaneIntensityFPCM"] = ch4 / _positive(fpcm)
    out["MilkFatYield"] = milk * fat / 100
    out["MilkProteinYield"] = milk * protein / 100
    out["FeedEfficiency"] = fpcm / dmi
    out["ForageShare"] = col("ForageIntake") / dmi * 100
    out["MethanePerMetabolicBW"] = ch4 / bw ** 0.75
    out["DMIPercentBW"] = dmi / bw * 100

    # Diet: each day's treatment -> the average composition of that treatment's diet.
    diet = diet_means(diet_nutrients)
    if "treatment_id" in out and not diet.empty:
        # True as a boolean or as text ("true"), depending on how the table was read
        conflict = (out["treatmentConflict"].astype(str).str.strip().str.lower() == "true") if "treatmentConflict" in out \
            else pd.Series(False, index=out.index)
        usable = out["treatment_id"].where(~conflict)
        on_day = diet.reindex(usable.to_numpy())
        on_day.index = out.index
    else:
        on_day = pd.DataFrame(index=out.index)
    for nutrient, (intake, _) in DIET_NUTRIENTS.items():
        out[intake] = dmi * on_day[nutrient] / 1000 if nutrient in on_day else blank

    recorded = _positive(col("GrossEnergyIntake"))
    from_diet = dmi * on_day["GrossEnergy"] if "GrossEnergy" in on_day else blank
    ge = recorded.fillna(from_diet)
    out["GrossEnergyIntakeAll"] = ge
    out["GrossEnergyIntakeSource"] = np.where(recorded.notna(), "recorded",
                                              np.where(from_diet.notna(), "from diet", None))
    out["Ym"] = ch4 * CH4_MJ_PER_G / _positive(ge) * 100
    return out


def calculated_labels() -> dict[str, str]:
    """column -> 'label (unit) · calculated' for the variable pickers."""
    return {c: f"{label} ({unit}) · calculated" for c, (label, unit, _, _) in CALCULATED.items()}


def calculated_dictionary() -> pd.DataFrame:
    """Data-dictionary rows for the calculated columns (same columns as kgDataDictionary where they apply)."""
    rows = [{"analysisTable": "kgAnimalDay", "column": c, "datatype": "double", "unit": unit,
             "derivedFrom": f"calculated in the Analysis page: {how}" + (f". {note}" if note else ""), "termLabel": label}
            for c, (label, unit, how, note) in CALCULATED.items()]
    rows.append({"analysisTable": "kgAnimalDay", "column": "GrossEnergyIntakeSource", "datatype": "string", "unit": "",
                 "derivedFrom": "calculated in the Analysis page: 'recorded' or 'from diet' for GrossEnergyIntakeAll",
                 "termLabel": "Source of gross energy intake"})
    return pd.DataFrame(rows)
