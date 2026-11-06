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

import html
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from scipy import stats

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


def study_treatment_means(data: pd.DataFrame, value: str) -> pd.DataFrame:
    """Per study and treatment: the mean over animals with a 95% confidence interval.

    Each animal's records are averaged first, so an animal measured on 20 days counts once; treating its
    days as independent records would make the interval far too narrow. CI = mean ± t(0.975, n - 1) ×
    SD / √n over the n animal means; blank when there is only one animal.
    """
    keys = ["studyId", "Treatment"]
    if data.empty:
        return pd.DataFrame(columns=keys + ["animals", "records", "mean", "sd", "ci_low", "ci_high"])
    per_animal = data.dropna(subset=[value]).groupby(keys + ["animal_id"], observed=True)[value].agg(["mean", "size"])
    g = per_animal.groupby(level=keys, observed=True)
    out = pd.DataFrame({"animals": g["mean"].count(), "records": g["size"].sum(),
                        "mean": g["mean"].mean(), "sd": g["mean"].std()})
    df = out["animals"] - 1
    t = np.where(df >= 1, stats.t.ppf(0.975, np.maximum(df, 1)), np.nan)
    half = t * out["sd"] / np.sqrt(out["animals"])
    out["ci_low"], out["ci_high"] = out["mean"] - half, out["mean"] + half
    return out.reset_index()


def box_stats(data: pd.DataFrame, value: str, by: str = "studyId") -> pd.DataFrame:
    """Per group: n, quartiles, median and Tukey whiskers (the most extreme values within 1.5 × IQR of the
    box), plus how many values lie beyond them. Computed here so the browser gets 6 numbers per study, not
    every record."""
    d = data[[by, value]].dropna()
    if d.empty:
        return pd.DataFrame(columns=[by, "n", "q1", "median", "q3", "lowerfence", "upperfence", "beyond"])
    g = d.groupby(by, observed=True)[value]
    out = pd.DataFrame({"n": g.size(), "q1": g.quantile(0.25), "median": g.median(), "q3": g.quantile(0.75)})
    iqr = out["q3"] - out["q1"]
    low_lim = d[by].map(out["q1"] - 1.5 * iqr)
    high_lim = d[by].map(out["q3"] + 1.5 * iqr)
    inside = (d[value] >= low_lim) & (d[value] <= high_lim)
    out["lowerfence"] = d[value].where(inside).groupby(d[by], observed=True).min()
    out["upperfence"] = d[value].where(inside).groupby(d[by], observed=True).max()
    out["beyond"] = (~inside).groupby(d[by], observed=True).sum()
    return out.reset_index()


def _slope(x: pd.Series, y: pd.Series) -> float:
    """Least-squares slope of y on x; missing when x does not vary."""
    dx = x - x.mean()
    ss = float((dx * dx).sum())
    return float((dx * (y - y.mean())).sum()) / ss if ss > 0 else np.nan


def covariate_slopes(data: pd.DataFrame, y: str, x: str, by: str = "studyId", min_n: int = 3) -> dict:
    """How y changes with x: pooled over all records, within studies and between studies.

    - overall: one line through every record (mixes the two below);
    - within: from each record's deviation from its own study's means, i.e. the relationship inside studies;
    - between: through the study means, i.e. how studies with higher x differ in y.
    They often differ in multi-study data (St-Pierre 2001); with a random study effect, the model's covariate
    estimate combines the two, usually leaning towards the within-study slope. `lines` holds each study's own
    fitted line over its x range (studies with at least `min_n` records and some spread in x).
    """
    d = data[[by, x, y]].dropna()
    out = {"overall": np.nan, "intercept": np.nan, "within": np.nan, "between": np.nan, "n": len(d),
           "xmin": np.nan, "xmax": np.nan, "lines": pd.DataFrame(columns=[by, "slope", "intercept", "x0", "x1", "n"])}
    if len(d) < 2:
        return out
    out["xmin"], out["xmax"] = float(d[x].min()), float(d[x].max())
    out["overall"] = _slope(d[x], d[y])
    out["intercept"] = d[y].mean() - out["overall"] * d[x].mean() if not np.isnan(out["overall"]) else np.nan
    g = d.groupby(by, observed=True)
    out["within"] = _slope(d[x] - g[x].transform("mean"), d[y] - g[y].transform("mean"))
    means = g[[x, y]].mean()
    if len(means) >= 3:
        out["between"] = _slope(means[x], means[y])
    rows = []
    for key, part in g:
        if len(part) >= min_n:
            b = _slope(part[x], part[y])
            if not np.isnan(b):
                rows.append({by: key, "slope": b, "intercept": part[y].mean() - b * part[x].mean(),
                             "x0": part[x].min(), "x1": part[x].max(), "n": len(part)})
    if rows:
        out["lines"] = pd.DataFrame(rows)
    return out


def treatment_effects(coefs: pd.DataFrame) -> pd.DataFrame:
    """The treatment rows of a fixed-effects table (differences from the reference), with plain labels."""
    rows = coefs[coefs["term"].astype(str).str.match(r"^Treatment\[T\.")].copy()
    rows["treatment"] = rows["term"].astype(str).str.replace(r"^Treatment\[T\.(.*)\]$", r"\1", regex=True)
    return rows[["treatment", "estimate", "ci_low", "ci_high", "p_value"]].reset_index(drop=True)


def variance_shares(components: list[dict]) -> pd.DataFrame:
    """Each variance component (study, animal, residual, ...) as a percentage of their total."""
    df = pd.DataFrame(components)
    if df.empty or "variance" not in df:
        return pd.DataFrame(columns=["name", "variance", "share"])
    df["variance"] = pd.to_numeric(df["variance"], errors="coerce")
    df = df[df["variance"] >= 0][["name", "variance"]].reset_index(drop=True)
    total = df["variance"].sum()
    df["share"] = df["variance"] / total * 100 if total > 0 else np.nan
    return df


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

    dmi = _positive(col("DryMatterIntake"))
    milk, bw = _positive(col("MilkProduction")), _positive(col("BodyWeight"))
    fat, protein = col("MilkFat"), col("MilkProtein")

    # Columns that use CH4 are filled by update_methane_columns at the end (placeholders keep their order).
    out["MethaneYield"] = np.nan
    out["MethaneIntensityMilk"] = np.nan
    fpcm = milk * (0.1226 * fat + 0.0722 * protein + 0.2534)
    out["FPCM"] = fpcm
    out["MethaneIntensityFPCM"] = np.nan
    out["MilkFatYield"] = milk * fat / 100
    out["MilkProteinYield"] = milk * protein / 100
    out["FeedEfficiency"] = fpcm / dmi
    out["ForageShare"] = col("ForageIntake") / dmi * 100
    out["MethanePerMetabolicBW"] = np.nan
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
    update_methane_columns(out)
    return out


def update_methane_columns(df: pd.DataFrame) -> None:
    """(Re)calculate, in place, the variables that use daily CH4: methane yield, the two intensities, CH4 per
    metabolic body weight and Ym. Called by add_calculated, and again when the GreenFeed visit rules change
    daily CH4. `df` must be a frame of its own, never one shared through the cache."""
    def num(name: str) -> pd.Series:
        return pd.to_numeric(df[name], errors="coerce") if name in df else pd.Series(np.nan, index=df.index)

    ch4 = _positive(num("CH4_g_d"))
    df["MethaneYield"] = ch4 / _positive(num("DryMatterIntake"))
    df["MethaneIntensityMilk"] = ch4 / _positive(num("MilkProduction"))
    df["MethaneIntensityFPCM"] = ch4 / _positive(num("FPCM"))
    df["MethanePerMetabolicBW"] = ch4 / _positive(num("BodyWeight")) ** 0.75
    df["Ym"] = ch4 * CH4_MJ_PER_G / _positive(num("GrossEnergyIntakeAll")) * 100


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


# ---------------------------------------------------------------------------
# GreenFeed visit rules (QC tab): which visits count when daily CH4 is calculated
# ---------------------------------------------------------------------------
VISIT_COLUMNS = ["studyId", "animal_id", "Start_Time", "End_Time", "goodDataSeconds", "Good_Data_Duration",
                 "CH4_Massflow_g_d", "CO2_Massflow_g_d", "O2_Massflow_g_d", "H2_Massflow_g_d",
                 "Average_Airflow_L_s", "Was_Interrupted", "gfunit_id"]
VISIT_GASES = {"CH4_Massflow_g_d": "CH4_g_d", "CO2_Massflow_g_d": "CO2_g_d", "O2_Massflow_g_d": "O2_g_d",
               "H2_Massflow_g_d": "H2_g_d"}
DAILY_COLUMNS = ["CH4_g_d", "CO2_g_d", "O2_g_d", "H2_g_d", "nVisits"]
NO_READING = -999  # GreenFeed exports write -999 for a gas they could not measure
NON_MEASUREMENT = re.compile(r"adapt|adpat|transition|wash", re.IGNORECASE)  # "adpat" is a typo found in the data

# A rule that is 0 or False is off. With every rule off, daily CH4 is what the KG job publishes in kgAnimalDay:
# the mean of every matched visit with a CH4 reading.
RULES_DEFAULT = {"exclude_phases": False, "min_minutes": 0.0, "drop_interrupted": False, "min_airflow": 0.0,
                 "max_ch4": 0.0, "mad_k": 0.0, "min_visits_day": 1, "min_days": 0, "min_visits": 0, "min_bins": 0}
VISIT_PRESETS = {
    "Every matched visit": ({}, "No rules: every visit matched to an animal with a CH4 reading (what the KG job "
                                "publishes as daily CH4)."),
    "C-Lock minimum": ({"min_minutes": 2.0}, "At least 2 minutes of good data per visit (C-Lock GreenFeed SOP; "
                                             "Global Research Alliance GreenFeed SOP)."),
    "Common practice": ({"exclude_phases": True, "min_minutes": 2.0, "drop_interrupted": True, "mad_k": 3.5,
                         "min_days": 7, "min_visits": 20, "min_bins": 6},
                        "A combination often used with GreenFeed data (as in the GEMS kg_map workbench), not one "
                        "published standard: measurement days only, visits of 2 min or more, no interrupted visits, "
                        "no per-animal outliers, and at least 7 days, 20 visits and 6 of the 8 three-hour periods "
                        "of the day per animal and period."),
    "Beck et al. 2024": ({"min_minutes": 3.0, "min_airflow": 26.0, "max_ch4": 500.0, "min_visits": 30},
                         "Beck et al. (2024, J Anim Sci 102:skae158): visits of 3 min or more, airflow of at least "
                         "26 L/s, CH4 at most 500 g/d (their limit for steers), and at least 30 visits per animal "
                         "(here: per animal and period)."),
}
# Why a visit was left out: the first rule it failed, in the order the rules are applied (0 = kept).
VISIT_STEPS = {1: "exclude_phases", 2: "min_minutes", 3: "drop_interrupted", 4: "min_airflow", 5: "max_ch4",
               6: "mad_k", 7: "min_visits_day", 8: "segment"}


def visit_step_label(code: int, rules: dict) -> str:
    r = {**RULES_DEFAULT, **rules}
    return {0: "Kept",
            1: "Adaptation, transition or washout day",
            2: f"Shorter than {r['min_minutes']:g} min",
            3: "Interrupted visit",
            4: f"Airflow below {r['min_airflow']:g} L/s",
            5: f"CH4 above {r['max_ch4']:g} g/d",
            6: f"Outlier for its animal (robust z > {r['mad_k']:g})",
            7: f"Day with fewer than {r['min_visits_day']} visits",
            8: "Animal-period below the minimum days, visits or times of day"}[code]


def active_visit_steps(rules: dict) -> list[int]:
    r = {**RULES_DEFAULT, **rules}
    on = {1: r["exclude_phases"], 2: r["min_minutes"] > 0, 3: r["drop_interrupted"], 4: r["min_airflow"] > 0,
          5: r["max_ch4"] > 0, 6: r["mad_k"] > 0, 7: r["min_visits_day"] > 1,
          8: r["min_days"] > 0 or r["min_visits"] > 0 or r["min_bins"] > 0}
    return [code for code, is_on in on.items() if is_on]


def _as_bool(s: pd.Series | None, index) -> pd.Series:
    """True / False / missing from booleans or text ("true", "1", "yes", ...). Anything else is missing."""
    if s is None:
        return pd.Series(np.nan, index=index, dtype=object)
    t = s.astype(str).str.strip().str.lower()
    out = pd.Series(np.nan, index=s.index, dtype=object)
    out[t.isin(["true", "1", "1.0", "yes", "y", "t"])] = True
    out[t.isin(["false", "0", "0.0", "no", "n", "f"])] = False
    return out


def _seconds(text: pd.Series) -> pd.Series:
    """'hh:mm:ss' -> seconds."""
    hms = text.astype(str).str.extract(r"^(\d+):(\d{2}):(\d{2})$").astype(float)
    return hms[0] * 3600 + hms[1] * 60 + hms[2]


def prepare_visits(raw: pd.DataFrame, days: pd.DataFrame) -> pd.DataFrame:
    """Matched GreenFeed visits with a CH4 reading, one row each, ready for the rules: start time, minutes of
    good data, the four gases (-999 left out), airflow, the interrupted flag, and from that animal's day its
    treatment, period and experimental phase. Integer codes for the animal, the day and the animal-period
    (animal x treatment x period) make the rules fast."""
    idx = raw.index
    start = pd.to_datetime(raw["Start_Time"], errors="coerce") if "Start_Time" in raw else pd.Series(pd.NaT, index=idx)
    v = pd.DataFrame({"studyId": raw["studyId"].astype(str), "animal_id": raw["animal_id"], "start": start,
                      "Date": start.dt.normalize(),
                      "unit": raw["gfunit_id"] if "gfunit_id" in raw else pd.Series(np.nan, index=idx)})
    secs = pd.to_numeric(raw["goodDataSeconds"], errors="coerce") if "goodDataSeconds" in raw else pd.Series(np.nan, index=idx)
    missing = secs.isna()  # fall back to the text duration, then to end - start, only where needed (parsing is slow)
    if missing.any() and "Good_Data_Duration" in raw:
        secs[missing] = _seconds(raw.loc[missing, "Good_Data_Duration"])
        missing = secs.isna()
    if missing.any() and "End_Time" in raw:
        secs[missing] = (pd.to_datetime(raw.loc[missing, "End_Time"], errors="coerce") - start[missing]).dt.total_seconds()
    v["minutes"] = secs / 60
    for src, dst in VISIT_GASES.items():
        x = pd.to_numeric(raw[src], errors="coerce") if src in raw else pd.Series(np.nan, index=idx)
        v[dst] = x.where(x != NO_READING)
    v["airflow"] = pd.to_numeric(raw["Average_Airflow_L_s"], errors="coerce") if "Average_Airflow_L_s" in raw else np.nan
    v["interrupted"] = _as_bool(raw.get("Was_Interrupted"), idx).map({True: 1.0, False: 0.0}).astype(float)  # 1 / 0 / blank
    v = v[v["animal_id"].notna() & v["start"].notna() & (v["CH4_g_d"] > 0)]
    info_cols = [c for c in ["Treatment", "Period", "ExperimentalPhase"] if c in days]
    if not days.empty:
        info = days[["animal_id", "Date"] + info_cols].drop_duplicates(["animal_id", "Date"])
        v = v.merge(info, on=["animal_id", "Date"], how="left")
    for c in ["Treatment", "Period", "ExperimentalPhase"]:
        if c not in v:
            v[c] = np.nan
    v["non_measurement"] = v["ExperimentalPhase"].notna() & v["ExperimentalPhase"].astype(str).str.contains(NON_MEASUREMENT)
    v["hour_bin"] = (v["start"].dt.hour // 3).astype("int8")  # 8 three-hour periods of the day
    v = v.sort_values(["animal_id", "start"], kind="stable").reset_index(drop=True)
    v["_animal"] = pd.factorize(v["animal_id"])[0]
    v["_day"] = v.groupby(["animal_id", "Date"], sort=False).ngroup()
    v["_segment"] = v.groupby(["animal_id", "Treatment", "Period"], sort=False, dropna=False).ngroup()
    v["_group"] = v.groupby(["studyId", "Treatment", "Period"], sort=False, dropna=False).ngroup()  # for repeatability
    return v.drop(columns=["Treatment", "Period", "ExperimentalPhase"])  # only needed for the codes above


def run_visit_rules(v: pd.DataFrame, rules: dict) -> dict:
    """Apply the rules in order. Returns `status` (per visit: 0 = kept, else the VISIT_STEPS code of the first
    rule that left it out) and `daily` (per animal and day: mean of the kept visits' gases, and how many).

    A rule that cannot be checked for a visit (no airflow recorded, interrupted flag unreadable) does not remove
    it. The per-animal outlier screen uses the visits still kept at that point and needs at least 5 of them.
    """
    r = {**RULES_DEFAULT, **rules}
    n = len(v)
    status = np.zeros(n, dtype=np.int8)
    keep = np.ones(n, dtype=bool)

    def drop(fails: np.ndarray, code: int) -> None:
        hit = keep & fails
        status[hit] = code
        keep[hit] = False

    if n:
        if r["exclude_phases"]:
            drop(v["non_measurement"].to_numpy(dtype=bool), 1)
        if r["min_minutes"] > 0:
            drop((v["minutes"] < r["min_minutes"]).to_numpy(), 2)
        if r["drop_interrupted"]:
            drop(v["interrupted"].eq(1.0).to_numpy(), 3)
        if r["min_airflow"] > 0:
            drop((v["airflow"] < r["min_airflow"]).to_numpy(), 4)
        if r["max_ch4"] > 0:
            drop((v["CH4_g_d"] > r["max_ch4"]).to_numpy(), 5)
        if r["mad_k"] > 0:
            x = v["CH4_g_d"].where(keep)
            g = x.groupby(v["_animal"])
            med = g.transform("median")
            mad = (x - med).abs().groupby(v["_animal"]).transform("median")
            z = (x - med).abs() / (MAD_SCALE * mad.replace(0, np.nan))
            drop(((z > r["mad_k"]) & (g.transform("count") >= 5)).to_numpy(), 6)
        day = v["_day"].to_numpy()
        if r["min_visits_day"] > 1:
            per_day = np.bincount(day[keep], minlength=day.max() + 1)
            drop(per_day[day] < r["min_visits_day"], 7)
        if r["min_days"] > 0 or r["min_visits"] > 0 or r["min_bins"] > 0:
            seg = v["_segment"].to_numpy()
            n_seg = seg.max() + 1
            visits = np.bincount(seg[keep], minlength=n_seg)
            day_seg = np.zeros(day.max() + 1, dtype=np.int64)
            day_seg[day] = seg
            days_ = np.bincount(day_seg[np.unique(day[keep])], minlength=n_seg)
            seg_bin = np.unique(seg[keep].astype(np.int64) * 8 + v["hour_bin"].to_numpy()[keep])
            bins = np.bincount(seg_bin // 8, minlength=n_seg)
            ok = (days_ >= r["min_days"]) & (visits >= r["min_visits"]) & (bins >= r["min_bins"])
            drop(~ok[seg], 8)
    kept = v[keep]
    g = kept.groupby(["animal_id", "Date"], sort=False)
    daily = g[["CH4_g_d", "CO2_g_d", "O2_g_d", "H2_g_d"]].mean()
    daily["nVisits"] = g.size()
    return {"status": status, "daily": daily}


def visit_attrition(v: pd.DataFrame, status: np.ndarray, rules: dict, cohort: np.ndarray | None = None) -> pd.DataFrame:
    """Visits, animal-days and animals left after each active rule (in the order applied), for the cohort's
    visits (`cohort`: True for visits of the selected animals and dates)."""
    cohort = np.ones(len(v), dtype=bool) if cohort is None else np.asarray(cohort, dtype=bool)
    day, animal = v["_day"].to_numpy(), v["_animal"].to_numpy()
    n_day, n_animal = (int(day.max()) + 1, int(animal.max()) + 1) if len(v) else (0, 0)
    rows, previous = [], None
    for code in [0] + active_visit_steps(rules):
        left = cohort & ((status == 0) | (status > code)) if code else cohort
        visits = int(left.sum())
        rows.append({"step": "Matched visits with a CH4 reading" if code == 0 else visit_step_label(code, rules),
                     "visits": visits, "removed": 0 if previous is None else previous - visits,
                     # distinct days / animals among the visits left (counting beats sorting on ~1M visits)
                     "animal_days": int(np.count_nonzero(np.bincount(day[left], minlength=n_day))),
                     "animals": int(np.count_nonzero(np.bincount(animal[left], minlength=n_animal)))})
        previous = visits
    return pd.DataFrame(rows)


def _value_bins(values: np.ndarray, max_bins: int = 30, q: float = 0.98) -> pd.DataFrame:
    """Histogram of whole-number counts in at most `max_bins` equal bins up to the q-quantile (at least 10);
    larger values go into the last bin. Columns x0, x1 (x1 exclusive), n."""
    values = np.asarray(values)
    if not len(values):
        return pd.DataFrame(columns=["x0", "x1", "n"])
    hi = max(10.0, float(np.quantile(values, q)))
    step = max(1, int(np.ceil(hi / max_bins)))
    n_bins = int(np.ceil(hi / step)) + 1
    counts = np.bincount(np.minimum(values // step, n_bins - 1).astype(np.int64), minlength=n_bins)
    x0 = np.arange(n_bins) * step
    return pd.DataFrame({"x0": x0, "x1": x0 + step, "n": counts})


def visit_qc_charts(v: pd.DataFrame, status: np.ndarray, cohort: np.ndarray | None = None,
                    production: pd.Series | None = None, min_n: int = 30) -> dict:
    """The QC tab's visit diagnostics for the cohort's visits (as in the kg_map_claude workbench):

    duration  — minutes of good data in 0.25-min bins (last bin: 8 min or more), visits before the duration rule
                (only the phase rule applied);
    per_day   — animal-days by how many visits the animal made that day (8+ pooled), same visits;
    hours     — kept visits by hour of day;
    diurnal   — kept visits, % above or below the same animal's mean that day (days with >= 2 kept visits and a
                mean >= 20 g/d), mean per hour and production type, hours with at least `min_n` visits;
    unit_visits / coverage — per animal-period (animal x treatment x period) with visits left before the
                animal-period rule: its visits (binned) and how many of the 8 three-hour windows they cover.
    `production`: animal_id -> production type (Dairy, Beef, ...)."""
    cohort = np.ones(len(v), dtype=bool) if cohort is None else np.asarray(cohort, dtype=bool)
    day, seg = v["_day"].to_numpy(), v["_segment"].to_numpy()
    n_day = int(day.max()) + 1 if len(v) else 0
    base = cohort & (status != 1)  # before the duration rule: only the phase rule applied
    minutes = v["minutes"].to_numpy(dtype=float)
    m = minutes[base & ~np.isnan(minutes)]
    dur = np.bincount(np.minimum(np.floor(m / 0.25), 32).astype(np.int64), minlength=33)
    x0 = np.arange(33) * 0.25
    out = {"duration": pd.DataFrame({"x0": x0, "x1": x0 + 0.25, "visits": dur})}

    per_day = np.bincount(day[base], minlength=n_day)
    per_day = per_day[per_day > 0]
    counts = np.bincount(np.minimum(per_day, 8), minlength=9)[1:]
    out["per_day"] = pd.DataFrame({"visits_that_day": [str(k) for k in range(1, 8)] + ["8+"], "animal_days": counts})

    kept = cohort & (status == 0)
    hour = v["start"].dt.hour.to_numpy()
    out["hours"] = pd.DataFrame({"hour": np.arange(24), "visits": np.bincount(hour[kept], minlength=24)})

    ch4 = v["CH4_g_d"].to_numpy(dtype=float)
    day_n = np.bincount(day[kept], minlength=n_day)
    day_mean = np.bincount(day[kept], weights=ch4[kept], minlength=n_day) / np.maximum(day_n, 1)
    use = np.flatnonzero(kept & (day_n[day] >= 2) & (day_mean[day] >= 20))
    # production type per animal (a few thousand look-ups), then per visit by the animal's integer code
    animal = v["_animal"].to_numpy()
    first = np.full(int(animal.max()) + 1 if len(v) else 0, -1)
    first[animal[::-1]] = np.arange(len(animal))[::-1]  # first visit of each animal
    names = pd.Series(v["animal_id"].to_numpy()[first]).map(production) if production is not None else None
    names = (names if names is not None else pd.Series(np.nan, index=range(len(first)))).fillna(UNKNOWN).astype(str)
    kinds, of_animal = np.unique(names.to_numpy(), return_inverse=True)
    pcode, h = of_animal[animal[use]], hour[use]
    dev = 100 * (ch4[use] - day_mean[day[use]]) / day_mean[day[use]]
    n = np.bincount(pcode * 24 + h, minlength=len(kinds) * 24).reshape(len(kinds), 24)
    total = np.bincount(pcode * 24 + h, weights=dev, minlength=len(kinds) * 24).reshape(len(kinds), 24)
    order = sorted(range(len(kinds)), key=lambda k: -n[k].sum())  # most visits first
    rows = [{"production": kinds[k], "hour": hh, "pct": total[k, hh] / n[k, hh], "visits": int(n[k, hh])}
            for k in order for hh in range(24) if n[k, hh] >= min_n]
    out["diurnal"] = pd.DataFrame(rows, columns=["production", "hour", "pct", "visits"])

    pre_unit = cohort & ((status == 0) | (status == 8))  # every rule applied except the animal-period rule
    n_seg = int(seg.max()) + 1 if len(v) else 0
    unit_visits = np.bincount(seg[pre_unit], minlength=n_seg)
    has = unit_visits > 0
    in_window = np.bincount(seg[pre_unit].astype(np.int64) * 8 + v["hour_bin"].to_numpy()[pre_unit],
                            minlength=n_seg * 8).reshape(n_seg, 8)
    covered = (in_window > 0).sum(axis=1)
    out["unit_visits"] = _value_bins(unit_visits[has])
    out["coverage"] = pd.DataFrame({"windows": np.arange(9), "units": np.bincount(covered[has], minlength=9)[:9]})
    return out


def apply_daily_gases(days: pd.DataFrame, daily: pd.DataFrame) -> None:
    """Replace, in place, the daily gas means and visit counts of `days` (a frame of its own, e.g. the cohort)
    with those from the visit rules, and recalculate the variables that use CH4. Days without a kept visit get
    no gas values."""
    aligned = daily.reindex(pd.MultiIndex.from_arrays([days["animal_id"], days["Date"]]))
    for c in DAILY_COLUMNS:
        days[c] = aligned[c].to_numpy() if c in aligned else np.nan
    update_methane_columns(days)


def _read_visits_synthetic(d: Path) -> pd.DataFrame:
    v = _read_synthetic(d, "kgVisit")
    if v.empty:
        return v
    if "matchOk" in v:
        v = v[v["matchOk"].astype(str).str.lower() == "true"]
    return v[[c for c in VISIT_COLUMNS if c in v]]


def _read_visits_databricks() -> pd.DataFrame:
    from gems_data import _connect  # imported here so synthetic mode never touches Databricks

    catalog = os.environ.get("GEMS_CATALOG", "gems_catalog").strip()
    schema = os.environ.get("GEMS_KG_SCHEMA", "kg_v1").strip()
    table = f"`{catalog}`.`{schema}`.`kgVisit`"
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table} LIMIT 0")
        names = [d[0] for d in cur.description]
        cols = ", ".join(f"`{c}`" for c in VISIT_COLUMNS if c in names)
        where = "WHERE matchOk = true AND animal_id IS NOT NULL" if "matchOk" in names else "WHERE animal_id IS NOT NULL"
        cur.execute(f"SELECT {cols} FROM {table} {where}")
        return cur.fetchall_arrow().to_pandas()


@st.cache_resource(ttl=12 * 3600, show_spinner="Loading GreenFeed visits…")
def load_visits(source: str) -> pd.DataFrame:
    """Matched visits prepared for the rules; loaded only when the QC tab or a visit rule needs them, then shared
    (never change in place). Cleared with the rest by "Reload data"."""
    d = synthetic_dir()
    raw = _read_visits_synthetic(d) if d else _read_visits_databricks()
    return prepare_visits(raw, load_dataset(source)["tables"]["kgAnimalDay"])


@st.cache_resource(max_entries=4, show_spinner="Applying the visit rules…")
def visit_rules_result(source: str, rules_items: tuple) -> dict:
    """run_visit_rules on all visits, once per rule set (rules_items: sorted (rule, value) pairs)."""
    return run_visit_rules(load_visits(source), dict(rules_items))


# ---------------------------------------------------------------------------
# Standardisation lab: how many days (or visits) an animal needs for a reliable mean
# ---------------------------------------------------------------------------
def repeatability_frame(days: pd.DataFrame, value: str) -> pd.DataFrame:
    """Animal-day records of `value` with the keys the repeatability needs: the treatment-period group
    (study x treatment x period) and the animal-period (animal x treatment x period)."""
    cols = [c for c in ["studyId", "animal_id", "Treatment", "Period"] if c in days]
    d = days[cols + [value]].dropna(subset=[value])
    for c in ["Treatment", "Period"]:
        if c not in d:
            d = d.assign(**{c: np.nan})
    return pd.DataFrame({
        "studyId": d["studyId"].astype(str), "animal_id": d["animal_id"], value: d[value].astype(float),
        "group": d.groupby(["studyId", "Treatment", "Period"], sort=False, dropna=False).ngroup(),
        "segment": d.groupby(["studyId", "animal_id", "Treatment", "Period"], sort=False, dropna=False).ngroup(),
    })


def repeatability(data: pd.DataFrame, value: str) -> pd.DataFrame:
    """Per study: how much of the record-to-record variation in `value` lies between animals.

    `data` has studyId, animal_id, `value`, `group` (treatment-period group) and `segment` (animal-period,
    nested in its group). Each group's mean is removed first, so treatment and period differences do not count
    as animal differences. Then a nested one-way random-effects ANOVA (animal-periods within groups):
      within-animal variance  s2w = MSW, record to record within an animal-period;
      between-animal variance s2b = (MSB - MSW) / n0, n0 = (N - sum_groups(sum n_i^2 / N_group)) / (a - g);
      repeatability of one record r = s2b / (s2b + s2w).
    Missing when a study has fewer than 2 animal-periods more than groups, or no repeated records.
    """
    cols = ["studyId", "animals", "animal_periods", "records", "records_per_period", "sd_between", "sd_within", "r"]
    d = data[["studyId", "animal_id", "group", "segment", value]].dropna(subset=[value])
    if d.empty:
        return pd.DataFrame(columns=cols)
    s_code, studies = pd.factorize(d["studyId"])
    g_code = d.groupby(["studyId", "group"], sort=False).ngroup().to_numpy()
    a_code = d.groupby(["studyId", "segment"], sort=False).ngroup().to_numpy()
    y = d[value].to_numpy(dtype=float)
    n_s, n_g, n_a = len(studies), g_code.max() + 1, a_code.max() + 1
    yc = y - (np.bincount(g_code, weights=y, minlength=n_g) / np.bincount(g_code, minlength=n_g))[g_code]
    n_i = np.bincount(a_code, minlength=n_a).astype(float)
    m_i = np.bincount(a_code, weights=yc, minlength=n_a) / n_i
    a_study = np.zeros(n_a, dtype=np.int64)
    a_study[a_code] = s_code
    a_group = np.zeros(n_a, dtype=np.int64)
    a_group[a_code] = g_code
    g_study = np.zeros(n_g, dtype=np.int64)
    g_study[g_code] = s_code
    N = np.bincount(s_code, minlength=n_s).astype(float)
    a = np.bincount(a_study, minlength=n_s).astype(float)
    g = np.bincount(g_study, minlength=n_s).astype(float)
    ssw = np.bincount(s_code, weights=(yc - m_i[a_code]) ** 2, minlength=n_s)
    ssb = np.bincount(a_study, weights=n_i * m_i ** 2, minlength=n_s)
    n_group = np.bincount(a_group, weights=n_i, minlength=n_g)
    term = np.bincount(g_study, weights=np.bincount(a_group, weights=n_i ** 2, minlength=n_g) / n_group, minlength=n_s)
    with np.errstate(divide="ignore", invalid="ignore"):
        df_b, df_w = a - g, N - a
        msw = np.where(df_w > 0, ssw / df_w, np.nan)
        msb = np.where(df_b > 0, ssb / df_b, np.nan)
        n0 = np.where(df_b > 0, (N - term) / df_b, np.nan)
        s2b = np.where((df_b >= 1) & (df_w >= 1) & (n0 > 0), np.maximum(0.0, (msb - msw) / n0), np.nan)
        s2w = np.where(np.isnan(s2b), np.nan, msw)
        r = s2b / (s2b + s2w)
    animals = d.groupby(s_code)["animal_id"].nunique().reindex(range(n_s)).to_numpy()
    return pd.DataFrame({"studyId": list(studies), "animals": animals, "animal_periods": a.astype(int),
                         "records": N.astype(int), "records_per_period": N / a, "sd_between": np.sqrt(s2b),
                         "sd_within": np.sqrt(s2w), "r": r})


def reliability_curve(r: float, k: np.ndarray) -> np.ndarray:
    """Spearman-Brown: reliability of the mean of k records, R(k) = k r / (1 + (k - 1) r)."""
    return k * r / (1 + (k - 1) * r)


def records_needed(r: float, target: float) -> float:
    """Smallest whole number of records whose mean reaches `target` reliability: k = t/(1-t) x (1-r)/r."""
    if r is None or np.isnan(r) or r <= 0:
        return np.inf
    return float(np.ceil(target / (1 - target) * (1 - r) / r - 1e-9))


# ---------------------------------------------------------------------------
# Model code export and checking the AI explanation's numbers
# ---------------------------------------------------------------------------
def model_code(response: str, covariates: list[str], level: str, reference: str, two_levels: bool,
               rules_name: str, screened: bool) -> dict[str, str]:
    """R and Python code that refit the Model tab's mixed model from the Export tab's animal-days CSV."""
    used = [response] + covariates
    rhs = " + ".join(["Treatment"] + covariates)
    period = level == "Animal-period mean"
    notes = (f"# Reproduces the GEMS Analysis page model. Data: the Animal-days table from the Export tab\n"
             f"# (gems_cohort_animal_days.csv); its daily gases already follow the visit rules \"{rules_name}\".\n"
             + ("# The page's optional outlier screen was on; it is not repeated here.\n" if screened else ""))
    r_vars = ", ".join(used)
    r_lines = [notes + "library(dplyr)\nlibrary(lme4)\n", 'd <- read.csv("gems_cohort_animal_days.csv")']
    if period:
        r_lines.append("# one value per animal, treatment and period (mean of its days)\n"
                       "d <- d %>%\n  group_by(studyId, animal_id, Treatment, Period) %>%\n"
                       f"  summarise(across(c({r_vars}), ~ mean(.x, na.rm = TRUE)), .groups = \"drop\")")
    r_lines.append("d <- d %>% filter(" + ", ".join(f"!is.na({c})" for c in used + ["Treatment"]) + ")")
    r_lines.append(f'd$Treatment <- relevel(factor(d$Treatment), ref = "{reference}")')
    random = "(1 | studyId/animal_id)" if two_levels else "(1 | animal_id)"
    r_lines.append(f"m <- lmer({response} ~ {rhs} + {random}, data = d)  # REML, as on the page")
    r_lines.append('summary(m)\nconfint(m, method = "Wald")  # 95% CIs of the fixed effects')
    py_vars = ", ".join(f'"{c}"' for c in used)
    p_lines = [notes + "import pandas as pd\nimport statsmodels.formula.api as smf\n",
               'd = pd.read_csv("gems_cohort_animal_days.csv")']
    if period:
        p_lines.append("# one value per animal, treatment and period (mean of its days)\n"
                       'd = (d.groupby(["studyId", "animal_id", "Treatment", "Period"], dropna=False)'
                       f"[[{py_vars}]]\n       .mean().reset_index())")
    p_lines.append(f'd = d.dropna(subset=[{py_vars}, "Treatment"])')
    p_lines.append(f'reference = "{reference}"\nothers = sorted(set(d["Treatment"].astype(str)) - {{reference}})\n'
                   'd["Treatment"] = pd.Categorical(d["Treatment"].astype(str), categories=[reference] + others)')
    if two_levels:
        p_lines.append(f'm = smf.mixedlm("{response} ~ {rhs}", d, groups="studyId", re_formula="1",\n'
                       '                vc_formula={"animal_id": "0 + C(animal_id)"}).fit(method="lbfgs")  # REML')
    else:
        p_lines.append(f'm = smf.mixedlm("{response} ~ {rhs}", d, groups="animal_id").fit(method="lbfgs")  # REML')
    p_lines.append("print(m.summary())")
    return {"R": "\n".join(r_lines), "Python": "\n".join(p_lines)}


_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def unverified_numbers(text: str, known: list[float]) -> list[str]:
    """Numbers in an AI explanation that match none of the `known` result values at the precision written
    (signs ignored, so "lowered by 68.2" matches -68.21). Not checked: small whole numbers (0-10, e.g. list
    numbering), thresholds ("p < 0.001") and the confidence level in "95% CI"."""
    values = np.abs(np.array([float(k) for k in known if k is not None and np.isfinite(float(k))]))
    out = []
    for m in _NUMBER.finditer(text):
        s = m.group().replace(",", "")
        x = abs(float(s))
        before, after = text[max(0, m.start() - 3):m.start()].strip(), text[m.end():m.end() + 13].lower()
        if ("." not in s and x <= 10) or before.endswith(("<", ">", "≤", "≥")) \
                or after.startswith(("% ci", "% confidence", "%-ci")):
            continue
        decimals = len(s.split(".")[1]) if "." in s else 0
        tol = 0.5 * 10 ** -decimals + 1e-12
        if not len(values) or not np.any(np.abs(values - x) <= tol):
            out.append(m.group())
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Explore: one animal's record, and how animals differ compared with their own day-to-day spread
# ---------------------------------------------------------------------------
def treatment_runs(animal_days: pd.DataFrame) -> pd.DataFrame:
    """One animal's days in date order, cut into runs of the same treatment and period (a new run starts
    whenever either changes): Treatment, Period, start, end, days."""
    cols = ["Treatment", "Period", "start", "end", "days"]
    if animal_days.empty or "Treatment" not in animal_days:
        return pd.DataFrame(columns=cols)
    d = animal_days.sort_values("Date")
    key = d["Treatment"].astype(str) + "\x1f" + (d["Period"].astype(str) if "Period" in d else "")
    run = (key != key.shift()).cumsum()
    g = d.groupby(run.to_numpy(), sort=True)
    out = pd.DataFrame({"Treatment": g["Treatment"].first(),
                        "Period": g["Period"].first() if "Period" in d else np.nan,
                        "start": g["Date"].min(), "end": g["Date"].max(), "days": g.size()})
    return out[out["Treatment"].notna()].reset_index(drop=True)[cols]


def animal_spread(days: pd.DataFrame, value: str) -> pd.DataFrame:
    """Per animal: days with `value`, its mean and day-to-day SD, and its main treatment (the one on most of
    those days; how many it had). Sorted by mean, lowest first."""
    cols = ["animal_id", "days", "mean", "sd", "treatment", "treatments"]
    d = days.dropna(subset=[value])
    if d.empty:
        return pd.DataFrame(columns=cols)
    g = d.groupby("animal_id", sort=False)
    out = pd.DataFrame({"days": g.size(), "mean": g[value].mean(), "sd": g[value].std()})
    if "Treatment" in d:
        trt = d.dropna(subset=["Treatment"]).groupby("animal_id")["Treatment"]
        out["treatment"] = trt.agg(lambda t: t.astype(str).value_counts().index[0])
        out["treatments"] = trt.nunique()
    else:
        out["treatment"], out["treatments"] = np.nan, 0
    out["treatments"] = out["treatments"].fillna(0).astype(int)
    return out.reset_index().sort_values("mean", kind="stable").reset_index(drop=True)[cols]


# ---------------------------------------------------------------------------
# KG view: the knowledge graph's structure with the cohort's counts, the selected studies (treatments, diets,
# ingredients) and one animal's neighbourhood. Class and link names as in gems.ttl and table_map; drawn from
# the loaded kg_v1 tables (no extra queries).
# ---------------------------------------------------------------------------
KG_SCHEMA_EDGES = [  # (from class, link, to class), as the KG links them
    ("Animal", "belongsToStudy", "Study"), ("Treatment", "belongsToStudy", "Study"),
    ("DesignRecord", "aboutAnimal", "Animal"), ("DesignRecord", "receivesTreatment", "Treatment"),
    ("Treatment", "usesDiet", "Diet"), ("Diet", "hasIngredient", "DietComponent"),
    ("DietNutrientRecord", "aboutDiet", "Diet"),
    ("MilkObservation", "aboutAnimal", "Animal"), ("BodyWeightObservation", "aboutAnimal", "Animal"),
    ("IntakeDayObservation", "aboutAnimal", "Animal"), ("GreenFeedVisit", "aboutAnimal", "Animal"),
    ("GreenFeedVisit", "recordedBy", "GreenFeedUnit"),
]
# Colour groups: the page's categorical colours 1-4 in their fixed order, as (border, fill tint). Text stays ink.
KG_GROUPS = {"design": ("#2a78d6", "#e4eefb"), "records": ("#eb6834", "#fdece4"),
             "diet": ("#1baf7a", "#e0f5ed"), "greenfeed": ("#eda100", "#fdf3da")}
INK, INK_2, MUTED, HAIRLINE, PLANE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#f7f6f2"
# class -> (colour group, (one, many) for its count, what the count is)
KG_CLASSES = {
    "Study": ("design", ("study", "studies"), "Studies in your cohort."),
    "Animal": ("design", ("animal", "animals"),
               "Animals with at least one day in your cohort. Animals belong to their study, so a cow that took "
               "part in two studies is counted in each."),
    "Treatment": ("design", ("treatment", "treatments"),
                  "Treatments your cohort's animals received. Labels belong to their study: T1 in one study is "
                  "not T1 in another."),
    "DesignRecord": ("records", ("design record", "design records"),
                     "Experimental-design records: which treatment (and period) an animal was on, on a given day."),
    "MilkObservation": ("records", ("milk record", "milk records"),
                        "Milk records, per milking or per day as each study reported them."),
    "BodyWeightObservation": ("records", ("day with body weight", "days with body weight"),
                              "Animal-days with a body weight."),
    "IntakeDayObservation": ("records", ("day with intake", "days with intake"),
                             "Animal-days with a dry matter intake."),
    "GreenFeedVisit": ("greenfeed", ("visit", "visits"),
                       "GreenFeed visits behind the daily CH4, after your visit rules."),
    "GreenFeedUnit": ("greenfeed", ("unit", "units"), "GreenFeed units that recorded those visits."),
    "Diet": ("diet", ("diet", "diets"), "Diets of your cohort's treatments (usually one per treatment)."),
    "DietComponent": ("diet", ("ingredient line", "ingredient lines"),
                      "Ingredient lines of those diets: one per ingredient in each diet, with its share of the diet."),
    "DietNutrientRecord": ("diet", ("nutrient analysis", "nutrient analyses"),
                           "Nutrient analyses of those diets (one per diet and date)."),
}
RECORD_CLASSES = ["DesignRecord", "MilkObservation", "BodyWeightObservation", "IntakeDayObservation"]
DIET_SUMMARY = [("CrudeProtein", "CP"), ("NeutralDetergentFiber", "NDF"), ("Starch", "starch")]  # g/kg DM
NO_NAME = "(no name)"


def _cohort_diets(days: pd.DataFrame, diets: pd.DataFrame, diet_nutrients: pd.DataFrame):
    """(diet ids, ingredient lines, nutrient records) of the treatments used on `days`."""
    treatments = set(days["treatment_id"].dropna()) if "treatment_id" in days else set()

    def mine(t: pd.DataFrame) -> pd.DataFrame:
        return t[t["treatment_id"].isin(treatments)] if {"treatment_id", "diet_id"} <= set(t.columns) else t.iloc[0:0]

    lines, records = mine(diets), mine(diet_nutrients)
    ids = set(lines["diet_id"].dropna()) | set(records["diet_id"].dropna()) if len(lines) or len(records) else set()
    return ids, lines, records


def _ingredient_names(lines: pd.DataFrame) -> pd.Series:
    """Ingredient names as recorded (outer spaces dropped; blank names become "(no name)")."""
    if "Ingredients" not in lines:
        return pd.Series(NO_NAME, index=lines.index, dtype=object)
    name = lines["Ingredients"].astype("string").fillna("").str.strip()
    return name.where(name != "", NO_NAME).astype(str)


def kg_schema_counts(days: pd.DataFrame, diets: pd.DataFrame, diet_nutrients: pd.DataFrame,
                     units: set | None = None) -> dict[str, int | None]:
    """How many of each KG class the cohort's days (and their diets) hold. Observations are counted as the
    cohort's animal-day table has them: milk and design records as records, body weight and intake as days
    with a value, GreenFeed visits as the visits behind the daily gases (after the visit rules). GreenFeed units
    are counted only when `units` (the units of the cohort's visits) is given. Also "ingredientNames": how many
    different ingredient names the diets' lines use (None without an Ingredients column)."""
    def total(c: str) -> int:
        return int(pd.to_numeric(days[c], errors="coerce").fillna(0).sum()) if c in days else 0

    def filled(c: str) -> int:
        return int(days[c].notna().sum()) if c in days else 0

    diet_ids, lines, records = _cohort_diets(days, diets, diet_nutrients)
    names = _ingredient_names(lines)
    return {"Study": int(days["studyId"].nunique()), "Animal": int(days["animal_id"].nunique()),
            "Treatment": int(days["treatment_id"].nunique()) if "treatment_id" in days else 0,
            "DesignRecord": total("nDesignRecords"), "MilkObservation": total("nMilkRecords"),
            "BodyWeightObservation": filled("BodyWeight"), "IntakeDayObservation": filled("DryMatterIntake"),
            "GreenFeedVisit": total("nVisits"), "Diet": len(diet_ids), "DietComponent": len(lines),
            "DietNutrientRecord": len(records), "GreenFeedUnit": None if units is None else len(units),
            "ingredientNames": int(names[names != NO_NAME].nunique()) if "Ingredients" in lines else None}


# --- drawing helpers (Graphviz DOT with HTML-like labels) ---
def _count_text(cls: str, n: int) -> str:
    one, many = KG_CLASSES[cls][1]
    return f"{n:,} {one if n == 1 else many}"


def _number(x) -> str:
    """Short number for a label: 45.0 -> 45, 12.5 -> 12.5, 165.43 -> 165, 1234.5 -> 1,234."""
    x = float(x)
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.3g}"


def _share_text(share, unit: str) -> str:
    if share is None or pd.isna(share):
        return ""
    return _number(share) + ("" if unit == "%" else " ") + unit


def _dot_label(text) -> str:
    """Text for a quoted DOT attribute."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _html(text) -> str:
    """Text for a Graphviz HTML-like label."""
    return html.escape(str(text))


def _small(text: str, size: int = 10, color: str = INK_2) -> str:
    return f'<font point-size="{size}" color="{color}">{text}</font>'


def _node(node_id: str, label: str, group: str, tooltip: str = "", strong: bool = False,
          fill: str | None = None) -> str:
    border, tint = KG_GROUPS[group]
    attrs = [f"label=<{label}>", f'color="{border}"', f'fillcolor="{fill or tint}"']
    if strong:
        attrs.append("penwidth=2")
    if tooltip:
        attrs.append(f'tooltip="{_dot_label(tooltip)}"')
    return f'  "{node_id}" [{", ".join(attrs)}];'


def _dot_start(ranksep: float = 0.55) -> list[str]:
    return ["digraph G {",
            f'  graph [rankdir=LR, compound=true, bgcolor="transparent", nodesep=0.22, ranksep={ranksep}, '
            f'fontname="Helvetica", fontsize=10, fontcolor="{INK_2}"];',
            f'  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, fontcolor="{INK}", '
            'penwidth=1.2, margin="0.14,0.06"];',
            f'  edge [fontname="Helvetica", fontsize=9, fontcolor="{MUTED}", color="{MUTED}", arrowsize=0.7, '
            'penwidth=1];']


def _cluster(name: str, label: str, body: list[str]) -> list[str]:
    title = f'label=<{_small(_html(label))}>; labeljust="l"; ' if label else 'label=""; '
    return [f"  subgraph cluster_{name} {{",
            f'  {title}style="rounded,filled"; fillcolor="{PLANE}"; color="{HAIRLINE}"; penwidth=1;',
            *body, "  }"]


def schema_dot(counts: dict) -> str:
    """Graphviz DOT of the KG's classes, with the cohort's counts, and the links between them. Coloured by
    group; the four kinds of records about an animal share one aboutAnimal arrow."""
    def box(cls: str) -> str:
        n = counts.get(cls)
        if n is None:
            sub = _small("<i>load the visits to count</i>", 9, MUTED)
        else:
            sub = _small(_html(_count_text(cls, n)))
            k = counts.get("ingredientNames")
            if cls == "DietComponent" and k:
                sub += "<br/>" + _small(f"{k:,} different ingredient name" + ("" if k == 1 else "s"), 9)
        return _node(cls, f"<b>{cls}</b><br/>{sub}", KG_CLASSES[cls][0], tooltip=KG_CLASSES[cls][2])

    lines = _dot_start()
    lines += _cluster("records", "Records about an animal", [box(c) for c in RECORD_CLASSES])
    lines += _cluster("greenfeed", "GreenFeed", [box("GreenFeedVisit"), box("GreenFeedUnit")])
    lines += _cluster("diet", "Diet", [box(c) for c in ["DietNutrientRecord", "Diet", "DietComponent"]])
    lines += [box(c) for c in ["Animal", "Treatment", "Study"]]
    shared_drawn = False
    for a, link, b in KG_SCHEMA_EDGES:
        if a in RECORD_CLASSES and link == "aboutAnimal":  # one arrow from the records box
            if not shared_drawn:
                lines.append(f'  "{RECORD_CLASSES[2]}" -> "{b}" [label="{link}", ltail=cluster_records];')
                shared_drawn = True
            continue
        lines.append(f'  "{a}" -> "{b}" [label="{link}"];')
    return "\n".join(lines + ["}"])


# --- the selected studies: treatments, diets and ingredients ---
def _ingredient_lines(diets: pd.DataFrame, diet_ids) -> pd.DataFrame:
    """Ingredient lines of these diets: diet_id, name (as recorded), share of the diet; largest share first."""
    cols = ["diet_id", "name", "share"]
    if diets.empty or "diet_id" not in diets:
        return pd.DataFrame(columns=cols)
    d = diets[diets["diet_id"].isin(list(diet_ids))]
    share = pd.to_numeric(d["Proportion"], errors="coerce") if "Proportion" in d else np.nan
    out = pd.DataFrame({"diet_id": d["diet_id"], "name": _ingredient_names(d), "share": share})
    return out.sort_values(["diet_id", "share", "name"], ascending=[True, False, True], na_position="last",
                           kind="stable").reset_index(drop=True)[cols]


def _diet_summary(diet_nutrients: pd.DataFrame, diet_ids) -> tuple[dict, int]:
    """(mean CP, NDF and starch over the diets' nutrient analyses, in g/kg DM; number of analyses)."""
    if diet_nutrients.empty or "diet_id" not in diet_nutrients:
        return {}, 0
    r = diet_nutrients[diet_nutrients["diet_id"].isin(list(diet_ids))]
    means = {short: pd.to_numeric(r[col], errors="coerce").mean() for col, short in DIET_SUMMARY if col in r}
    return {k: float(v) for k, v in means.items() if pd.notna(v)}, len(r)


def _diet_summaries(diet_nutrients: pd.DataFrame, diet_ids) -> dict:
    """{diet id: _diet_summary of that diet alone}, for many diets in one pass."""
    if diet_nutrients.empty or "diet_id" not in diet_nutrients:
        return {}
    r = diet_nutrients[diet_nutrients["diet_id"].isin(list(diet_ids))]
    cols = [(col, short) for col, short in DIET_SUMMARY if col in r]
    means = (r[[c for c, _ in cols]].apply(pd.to_numeric, errors="coerce").groupby(r["diet_id"], sort=False).mean()
             if cols else None)
    out = {}
    for did, n in r.groupby("diet_id", sort=False).size().items():
        nutrients = {} if means is None else {short: float(means.at[did, col]) for col, short in cols
                                              if pd.notna(means.at[did, col])}
        out[did] = (nutrients, int(n))
    return out


def study_graph(studies: list, days: pd.DataFrame, animals: pd.DataFrame, diets: pd.DataFrame,
                diet_nutrients: pd.DataFrame) -> dict:
    """The selected studies' part of the KG, from the cohort's days.

    studies: id, animals, breeds (the breed, or how many); treatments: study, label, animals, days, additive
    (only when it says more than the label), diet ids; diets: {id: nutrients (mean CP, NDF, starch), analyses};
    lines: study, treatment, diet, ingredient name as recorded, share of the diet; names: {name: how many of
    the studies use it}. An ingredient name is a value in the KG, so the same text in two studies is one node.
    """
    out = {"studies": [], "treatments": [], "diets": {}, "lines": [], "names": {}}
    breed = (animals.drop_duplicates("animal_id").set_index("animal_id")["Breed"]
             if {"animal_id", "Breed"} <= set(animals.columns) else pd.Series(dtype=object))
    picked = list(dict.fromkeys(str(x) for x in studies))
    sid = days["studyId"].astype(str)
    mask = sid.isin(picked)
    mine, mine_sid = days[mask], sid[mask]
    # diets of these studies' treatments, their ingredient lines and nutrient means: looked up once, not per treatment
    tids = set(mine["treatment_id"].dropna()) if "treatment_id" in mine else set()
    pairs = [t.loc[t["treatment_id"].isin(tids), ["treatment_id", "diet_id"]]
             for t in (diets, diet_nutrients) if {"treatment_id", "diet_id"} <= set(t.columns)]
    pairs = pd.concat(pairs).dropna().drop_duplicates() if pairs else pd.DataFrame(columns=["treatment_id", "diet_id"])
    diets_of = pairs.groupby("treatment_id", sort=False)["diet_id"].agg(list).to_dict()
    all_diets = list(dict.fromkeys(pairs["diet_id"]))
    ingredients_of = {did: list(zip(g["name"], g["share"]))
                      for did, g in _ingredient_lines(diets, all_diets).groupby("diet_id", sort=False)}
    summary_of = _diet_summaries(diet_nutrients, all_diets)
    users: dict[str, set] = {}
    for s in picked:
        sd = mine[mine_sid == s]
        if sd.empty:
            continue
        ids = sd["animal_id"].unique()
        b = breed.reindex(ids).dropna().astype(str).str.strip()
        kinds = b[(b != "") & (b != UNKNOWN)].value_counts()
        out["studies"].append({"id": s, "animals": len(ids),
                               "breeds": kinds.index[0] if len(kinds) == 1 else
                               (f"{len(kinds)} breeds" if len(kinds) else "")})
        if "treatment_id" not in sd:
            continue
        sd = sd.dropna(subset=["treatment_id"])
        per = sd.groupby("treatment_id", sort=False)
        info = pd.DataFrame({"animals": per["animal_id"].nunique(), "days": per.size()})
        label = (per["Treatment"].first().reindex(info.index) if "Treatment" in sd
                 else pd.Series(np.nan, index=info.index, dtype=object))
        info["label"] = label.where(label.notna(), info.index.to_series()).astype(str)
        add_col = "MethaneInhibitingFeedAdditive"
        additive = (per[add_col].agg(lambda x: x.dropna().astype(str).str.strip().mode().iloc[0]
                                     if x.notna().any() else None) if add_col in sd else pd.Series(dtype=object))
        order = sorted(range(len(info)), key=lambda k: _natural(info["label"].iloc[k]))
        for tid, row in info.iloc[order].iterrows():
            add = additive.get(tid)
            if add is not None and (add == "" or add.lower() == row["label"].strip().lower()):
                add = None
            diet_ids = diets_of.get(tid, [])
            out["treatments"].append({"id": tid, "study": s, "label": row["label"], "animals": int(row["animals"]),
                                      "days": int(row["days"]), "additive": add, "diets": diet_ids})
            for did in diet_ids:
                if did in out["diets"]:
                    continue
                nutrients, analyses = summary_of.get(did, ({}, 0))
                ingredients = ingredients_of.get(did, [])
                out["diets"][did] = {"nutrients": nutrients, "analyses": analyses, "ingredients": ingredients}
                for name, share in ingredients:
                    out["lines"].append({"study": s, "treatment": row["label"], "diet": did, "name": name,
                                         "share": share})
                    users.setdefault(name, set()).add(s)
    out["names"] = {n: len(v) for n, v in users.items()}
    return out


def _natural(text) -> list:
    """Sort key that puts T2 before T10."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", str(text))]


def _share_range(shares: list, unit: str) -> str:
    """'45%' or '12–45%' over the non-missing shares; '' when none."""
    s = [float(x) for x in shares if x is not None and pd.notna(x)]
    if not s:
        return ""
    lo, hi = min(s), max(s)
    return _share_text(lo, unit) if lo == hi else f"{_number(lo)}–{_share_text(hi, unit)}"


def studies_dot(g: dict, share_unit: str = "%", max_treatments: int = 6, max_lines: int = 12) -> str:
    """Graphviz DOT of study_graph(): one box per study with its treatments (at most `max_treatments`) and their
    diets, each diet listing its ingredients and mean nutrients. Ingredient names that two or more of the
    studies use get their own node on the right, with one dashed line from each study that uses them (names
    matched exactly as recorded; shown bold in the diets)."""
    lines = _dot_start(ranksep=0.5)
    shared = sorted((n for n, k in g["names"].items() if k > 1), key=lambda n: (-g["names"][n], n.lower()))
    name_node = {n: f"n{k}" for k, n in enumerate(shared)}
    edges, links = [], []
    aqua = KG_GROUPS["diet"][0]
    for i, s in enumerate(g["studies"]):
        facts = _count_text("Animal", s["animals"]) + (f" · {s['breeds']}" if s["breeds"] else "")
        body = [_node(f"s{i}", f"<b>Study {_html(s['id'])}</b><br/>{_small(_html(facts))}", "design", strong=True)]
        mine = [t for t in g["treatments"] if t["study"] == s["id"]]
        drawn: dict = {}  # diet id -> its node in this study's box
        for j, t in enumerate(mine[:max_treatments]):
            tn = f"s{i}t{j}"
            days_text = f"{t['days']:,} day" + ("" if t["days"] == 1 else "s")
            label = (f"<b>Treatment {_html(t['label'])}</b><br/>"
                     + _small(_html(f"{_count_text('Animal', t['animals'])} · {days_text}")))
            if t["additive"]:
                label += "<br/>" + _small(_html(f"additive: {t['additive']}"), 9)
            if not t["diets"]:
                label += "<br/>" + _small("<i>no diet recorded</i>", 9, MUTED)
            body.append(_node(tn, label, "design"))
            edges.append(f'  "s{i}" -> "{tn}" [dir=back, tooltip="belongsToStudy"];')
            for did in t["diets"]:
                if did not in drawn:
                    drawn[did] = f"s{i}d{len(drawn)}"
                    body.append(_node(drawn[did], _diet_table(g["diets"][did], share_unit, max_lines, set(shared)),
                                      "diet"))
                edges.append(f'  "{tn}" -> "{drawn[did]}" [tooltip="usesDiet"];')
        more = len(mine) - max_treatments
        if more > 0:
            body.append(_node(f"s{i}more", _small(f"+ {more} more treatment" + ("" if more == 1 else "s"), 10, MUTED),
                              "design", fill="#ffffff"))
            edges.append(f'  "s{i}" -> "s{i}more" [dir=back, style=dashed];')
        lines += _cluster(f"study{i}", "", body)
        # one dashed line from this study's box to each shared name any of its diets use
        uses: dict = {}
        for did in dict.fromkeys(d for t in mine for d in t["diets"]):
            for name, share in g["diets"][did]["ingredients"]:
                if name in name_node:
                    entry = uses.setdefault(name, {"tail": None, "diets": set(), "shares": []})
                    entry["diets"].add(did)
                    entry["shares"].append(share)
                    if entry["tail"] is None and did in drawn:
                        entry["tail"] = drawn[did]
        fallback = next(iter(drawn.values()), f"s{i}")
        for name, e in uses.items():
            k = len(e["diets"])
            share = _share_range(e["shares"], share_unit)
            tip = f"Study {s['id']} uses {name} in {k} diet" + ("" if k == 1 else "s") + (f" ({share})" if share else "")
            links.append(f'  "{e["tail"] or fallback}" -> "{name_node[name]}" [ltail=cluster_study{i}, style=dashed, '
                         f'color="{aqua}", arrowhead=none, tooltip="{_dot_label(tip)}"];')
    for n in shared:
        used_by = _small(f"in {g['names'][n]} studies", 9)
        lines.append(_node(name_node[n], f"<b>{_html(n)}</b><br/>{used_by}", "diet",
                           tooltip=f"Ingredient name used by {g['names'][n]} of these studies"))
    return "\n".join(lines + edges + links + ["}"])


# --- one animal ---
def animal_graph(animal_id, days: pd.DataFrame, animals: pd.DataFrame, diets: pd.DataFrame,
                 diet_nutrients: pd.DataFrame, unit_visits: dict | None = None) -> dict:
    """One animal's neighbourhood in the KG, from its cohort days: its study, the treatments its design
    records point to (with days), each treatment's diet (ingredients with their shares, mean nutrients), its
    observations by kind (counts) and, if `unit_visits` ({unit id: visits}) is given, its GreenFeed units."""
    a = days[days["animal_id"] == animal_id]
    info = animals[animals["animal_id"] == animal_id] if "animal_id" in animals else animals.iloc[0:0]
    name = info["AnimalIdentifier"].iloc[0] if len(info) and "AnimalIdentifier" in info else animal_id

    def fmt(x):  # 3.0 -> 3
        return int(x) if isinstance(x, (float, np.floating)) and float(x).is_integer() else x

    facts = [f"{c}: {fmt(info[c].iloc[0])}" for c in ["Breed", "Parity", "DaysInMilk"]
             if len(info) and c in info and pd.notna(info[c].iloc[0])]
    treatments = []
    if "treatment_id" in a and len(a):
        per = a.dropna(subset=["treatment_id"]).groupby("treatment_id")
        label = per["Treatment"].first() if "Treatment" in a else pd.Series(dtype=object)
        for tid, n in per.size().items():
            ids, lines, records = _cohort_diets(a[a["treatment_id"] == tid], diets, diet_nutrients)
            ing = _ingredient_lines(diets, ids)
            nutrients, analyses = _diet_summary(diet_nutrients, ids)
            treatments.append({"id": tid, "label": label.get(tid, tid), "days": int(n),
                               "diet_lines": len(lines), "diet_records": len(records),
                               "ingredients": list(ing[["name", "share"]].itertuples(index=False, name=None)),
                               "nutrients": nutrients, "analyses": analyses})

    def total(c):
        return int(pd.to_numeric(a[c], errors="coerce").fillna(0).sum()) if c in a else 0

    obs = {"DesignRecord": total("nDesignRecords"), "MilkObservation": total("nMilkRecords"),
           "BodyWeightObservation": int(a["BodyWeight"].notna().sum()) if "BodyWeight" in a else 0,
           "IntakeDayObservation": int(a["DryMatterIntake"].notna().sum()) if "DryMatterIntake" in a else 0,
           "GreenFeedVisit": total("nVisits")}
    return {"name": name, "study": a["studyId"].iloc[0] if len(a) else None, "facts": facts,
            "treatments": treatments, "observations": obs, "units": unit_visits or {}}


def _diet_table(t: dict, share_unit: str, max_lines: int, bold=frozenset()) -> str:
    """HTML-like label of a diet: its ingredients with their shares (names in `bold` drawn bold), then its mean
    nutrients."""
    rows = ['<tr><td align="left" colspan="2"><b>Diet</b></td></tr>']
    for name, share in t["ingredients"][:max_lines]:
        text = f"<b>{_html(name)}</b>" if name in bold else _html(name)
        rows.append(f'<tr><td align="left">{_small(text)}</td>'
                    f'<td align="right">{_small(_html(_share_text(share, share_unit)))}</td></tr>')
    hidden = len(t["ingredients"]) - max_lines
    if hidden > 0:
        rows.append(f'<tr><td align="left" colspan="2">{_small(f"+ {hidden} more", 9, MUTED)}</td></tr>')
    if t["nutrients"]:
        text = " · ".join(f"{k} {_number(v)}" for k, v in t["nutrients"].items()) + " g/kg DM"
        n = t["analyses"]
        text += f" (mean of {n} analys" + ("is)" if n == 1 else "es)")
        rows.append(f'<tr><td align="left" colspan="2">{_small(_html(text), 9)}</td></tr>')
    elif t["analyses"] == 0:
        rows.append(f'<tr><td align="left" colspan="2">{_small("<i>no nutrient analysis</i>", 9, MUTED)}</td></tr>')
    return f'<table border="0" cellborder="0" cellspacing="0" cellpadding="1">{"".join(rows)}</table>'


def animal_dot(g: dict, share_unit: str = "%", max_lines: int = 12) -> str:
    """Graphviz DOT of animal_graph(): the animal, its study, its records by kind, its treatments and their
    diets (ingredients and nutrients) and its GreenFeed units, labelled with the KG link names."""
    lines = _dot_start()
    facts = "".join("<br/>" + _small(_html(f)) for f in g["facts"])
    lines.append(_node("animal", f"<b>Animal {_html(g['name'])}</b>{facts}", "design", strong=True))
    if g["study"] is not None:
        lines.append(_node("study", f"<b>Study {_html(g['study'])}</b>", "design"))
        lines.append('  "animal" -> "study" [label="belongsToStudy"];')
    for i, (cls, n) in enumerate(g["observations"].items()):
        if not n:
            continue
        lines.append(_node(f"obs{i}", f"<b>{cls}</b><br/>{_small(_html(_count_text(cls, n)))}", KG_CLASSES[cls][0]))
        lines.append(f'  "obs{i}" -> "animal" [label="aboutAnimal"];')
        if cls == "DesignRecord":
            for j, t in enumerate(g["treatments"]):
                days_text = f"{t['days']:,} day" + ("" if t["days"] == 1 else "s")
                lines.append(_node(f"trt{j}", f"<b>Treatment {_html(t['label'])}</b><br/>{_small(days_text)}",
                                   "design"))
                lines.append(f'  "obs{i}" -> "trt{j}" [label="receivesTreatment"];')
                if t["diet_lines"] or t["diet_records"]:
                    lines.append(_node(f"diet{j}", _diet_table(t, share_unit, max_lines), "diet"))
                    lines.append(f'  "trt{j}" -> "diet{j}" [label="usesDiet"];')
        if cls == "GreenFeedVisit":
            for j, (unit, n_unit) in enumerate(sorted(g["units"].items(), key=lambda kv: -kv[1])):
                short = str(unit).rstrip("/").split("/")[-1]
                lines.append(_node(f"unit{j}", f"<b>GreenFeedUnit {_html(short)}</b><br/>"
                                   + _small(f"{n_unit:,} visit" + ("" if n_unit == 1 else "s")), "greenfeed"))
                lines.append(f'  "obs{i}" -> "unit{j}" [label="recordedBy"];')
    return "\n".join(lines + ["}"])
