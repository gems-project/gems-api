"""Write FAKE Gold tables for testing the KG job and the Analysis page without real data.

Columns come from the term map (docs/ontology/mapping/column_term_map.tsv), so the files have the
same tables and columns as the Gold export. Every value is invented with a fixed seed: no real data
is read. Known Gold quirks are built in (-999 gas codes, unmatched GreenFeed tags, separate milkings).

    python tools_make_synthetic.py --out C:/Temp/gems_synth/gold

Then run the KG job on it (gems-personal-folder/pipeline/kg/kg_local.py --gold <out> --access <out>/studyaccess.csv)
to get the kg_v1 analysis tables the Analysis page reads. Write the output outside OneDrive.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DEFAULT_ONTOLOGY = ROOT.parent / "docs" / "ontology"
UNITS_ROW = "__UNITS__"
NO_READING = -999

# Tables the KG job needs for the analysis tables (kg_tables.NEEDED) plus the visit crosswalk and GreenFeed units.
TABLES = ["animalcharacteristics", "experimentaldesign", "milk", "bodyweight", "intakeperday",
          "feedcomponents", "dietnutrientcomposition", "greenfeedsettings",
          "greenfeedrawvisitationdata", "greenfeedanimalcrosswalk"]

INGREDIENTS = {"dairy": [("Corn silage", 45), ("Haylage", 15), ("Corn grain", 20), ("Soybean meal", 12), ("Mineral mix", 8)],
               "beef": [("Barley silage", 60), ("Barley grain", 30), ("Canola meal", 7), ("Mineral mix", 3)]}
BREEDS = {"dairy": ["Holstein-Friesian", "Jersey"], "beef": ["Angus", "Angus x Hereford"]}


def read_columns(ontology: Path) -> dict[str, list[dict]]:
    by_table = defaultdict(list)
    with open(ontology / "mapping" / "column_term_map.tsv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            by_table[r["table"]].append(r)
    return by_table


def generic(rng, col: dict, n: int) -> list:
    """Filler for columns the scenario below does not set: plausible type, meaningless value."""
    dt = col["datatype"]
    if dt == "double":
        return list(np.round(rng.uniform(1, 100, n), 2))
    if dt == "integer":
        return list(rng.integers(0, 10, n))
    if dt == "boolean":
        return list(rng.choice(["true", "false"], n))
    if dt == "date":
        return ["2024-01-01"] * n
    if dt == "time":
        return [f"{h:02d}:00:00" for h in rng.integers(0, 24, n)]
    return [f"{col['column']} {k}" for k in rng.integers(1, 4, n)]


class Study:
    def __init__(self, rng, number: int):
        self.rng = rng
        self.id = f"SYN{number:02d}"
        self.kind = "dairy" if number % 2 else "beef"
        self.start = date(2024, 1, 8) + timedelta(days=int(rng.integers(0, 300)))
        self.days = [self.start + timedelta(days=d) for d in range(int(rng.integers(35, 57)))]
        self.animals = [f"{self.id}-A{a:02d}" for a in range(1, int(rng.integers(10, 17)))]
        self.rfid = {a: f"99900000{number:02d}{i:05d}" for i, a in enumerate(self.animals, 1)}
        self.treatments = ["Control", "3-NOP"]
        self.crossover = number % 3 == 0
        self.unit = f"GF{number:02d}1"

    def treatment(self, animal: str, day: date) -> str:
        i = self.animals.index(animal)
        half = (day - self.start).days >= len(self.days) // 2
        return self.treatments[(i + (half if self.crossover else 0)) % 2]

    def period(self, day: date) -> str:
        return "2" if self.crossover and (day - self.start).days >= len(self.days) // 2 else "1"

    def phase(self, day: date) -> str:
        return "Adaptation" if (day - self.start).days % (len(self.days) // 2 or 1) < 10 else "Data Collection"


def scenario(s: Study) -> dict[str, list[dict]]:
    """Rows per table for one study, with only the columns that matter for analysis set."""
    rng, out = s.rng, defaultdict(list)
    base_ch4 = 420 if s.kind == "dairy" else 210
    for a in s.animals:
        out["animalcharacteristics"].append({
            "AnimalIdentifier": a, "RadioFrequencyIdentification": s.rfid[a], "Breed": rng.choice(BREEDS[s.kind]),
            "ProductionType": "Dairy" if s.kind == "dairy" else "Beef", "AnimalType": "Cow" if s.kind == "dairy" else "Steer",
            "Parity": int(rng.integers(1, 5)) if s.kind == "dairy" else "", "Age": round(float(rng.uniform(2, 7)), 1)})
        cow_ch4 = rng.normal(base_ch4, base_ch4 * 0.1)
        bw0 = rng.normal(650 if s.kind == "dairy" else 450, 40)
        for d_i, day in enumerate(s.days):
            ds, trt = day.isoformat(), s.treatment(a, day)
            effect = 0.72 if trt == "3-NOP" else 1.0
            out["experimentaldesign"].append({
                "AnimalIdentifier": a, "Date": ds, "Period": s.period(day), "ExperimentalPhase": s.phase(day),
                "ExperimentalDesign": "Crossover" if s.crossover else "Randomized complete block", "Treatment": trt,
                "MethaneInhibitingFeedAdditive": "3-NOP" if trt == "3-NOP" else "Control",
                "MethaneInhibitingFeedAdditiveDose": "60 mg/kg DM" if trt == "3-NOP" else "",
                "Housing": "Freestall" if s.kind == "dairy" else "Pen", "ExperimentalLocation": f"Synthetic farm {s.id}"})
            dmi = rng.normal(24 if s.kind == "dairy" else 10, 1.5)
            out["intakeperday"].append({"AnimalIdentifier": a, "Date": ds, "DryMatterIntake": round(dmi, 2),
                                        "FeedIntakeMeasurementMethod": "Feed bins"})
            if d_i % 7 == 0:
                out["bodyweight"].append({"AnimalIdentifier": a, "Date": ds, "BodyWeight": round(bw0 + d_i * 0.3 + rng.normal(0, 8), 1)})
            if s.kind == "dairy":
                for t in ("AM", "PM"):  # separate milkings: the KG job sums them per day
                    out["milk"].append({"AnimalIdentifier": a, "Date": ds, "MilkingTime": t,
                                        "MilkProduction": round(rng.normal(18, 2.5), 1), "MilkFat": round(rng.normal(3.9, 0.3), 2),
                                        "MilkProtein": round(rng.normal(3.2, 0.2), 2), "MilkLactose": round(rng.normal(4.7, 0.1), 2)})
            for _ in range(int(rng.integers(0, 5))):
                start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=int(rng.integers(0, 1430)))
                ch4 = rng.normal(cow_ch4 * effect, 60)
                visit = {"RFID": s.rfid[a], "FID": s.unit, "Farm_Name": f"Synthetic {s.id}",
                         "Start_Time": start.isoformat(), "End_Time": (start + timedelta(minutes=4)).isoformat(),
                         "Good_Data_Duration": f"00:0{int(rng.integers(2, 6))}:{int(rng.integers(0, 60)):02d}",
                         "Hour_Of_Day": round(start.hour + start.minute / 60, 2),
                         "CH4_Massflow_g_d": round(ch4, 1), "CO2_Massflow_g_d": round(ch4 * 30 + rng.normal(0, 400), 1),
                         "O2_Massflow_g_d": round(ch4 * 21 + rng.normal(0, 300), 1), "H2_Massflow_g_d": round(abs(rng.normal(1.5 if effect == 1 else 3, 0.5)), 2),
                         "Was_Interrupted": "false"}
                if rng.random() < 0.01:
                    visit[rng.choice(["O2_Massflow_g_d", "H2_Massflow_g_d"])] = NO_READING
                out["greenfeedrawvisitationdata"].append(visit)
                out["greenfeedanimalcrosswalk"].append({"RFID": s.rfid[a], "Start_Time": visit["Start_Time"], "Farm_Name": visit["Farm_Name"],
                                                        "canonicalAnimalIdentifier": a, "matchMethod": "rfid", "matchOk": "true",
                                                        "inDesignWindow": "true"})
    for k in range(int(len(s.days) * 0.5)):  # visits of tags that are not study animals
        start = datetime.combine(rng.choice(s.days), datetime.min.time()) + timedelta(minutes=int(rng.integers(0, 1430)))
        tag = f"99988800000{k:04d}"
        out["greenfeedrawvisitationdata"].append({"RFID": tag, "FID": s.unit, "Farm_Name": f"Synthetic {s.id}",
                                                  "Start_Time": start.isoformat(), "End_Time": (start + timedelta(minutes=3)).isoformat(),
                                                  "CH4_Massflow_g_d": round(rng.normal(base_ch4, 80), 1), "Was_Interrupted": "false"})
        out["greenfeedanimalcrosswalk"].append({"RFID": tag, "Start_Time": start.isoformat(), "Farm_Name": f"Synthetic {s.id}",
                                                "canonicalAnimalIdentifier": "", "matchMethod": "", "matchOk": "false", "inDesignWindow": ""})
    for trt in s.treatments:
        for name, prop in INGREDIENTS[s.kind]:
            out["feedcomponents"].append({"Treatment": trt, "Ingredients": name, "Proportion": prop,
                                          "CrudeProtein": round(rng.uniform(60, 450), 1), "NeutralDetergentFiber": round(rng.uniform(100, 500), 1)})
        for day in s.days[::7]:
            out["dietnutrientcomposition"].append({"Treatment": trt, "Date": day.isoformat(), "ProportionForage": 60 if s.kind == "dairy" else 70,
                                                   "CrudeProtein": round(rng.normal(165, 8), 1), "NeutralDetergentFiber": round(rng.normal(320, 15), 1),
                                                   "Starch": round(rng.normal(230, 15), 1)})
    out["greenfeedsettings"].append({"GreenFeedUnitIdentifier": s.unit, "MaximumDropsPerVisit": 8, "TimeBetweenDrops": 40,
                                     "SharedAccessToGreenFeed": "Yes", "GasMeasurementLocation": "Barn"})
    return out


def frame(rng, rows: list[dict], cols: list[dict], study_id: str) -> pd.DataFrame:
    n = len(rows)
    data = {}
    for c in cols:
        name = c["column"]
        if name == "studyId":
            data[name] = [study_id] * n
        elif any(name in r for r in rows):
            data[name] = [r.get(name, "") for r in rows]
        else:
            data[name] = generic(rng, c, n)
    return pd.DataFrame(data, columns=[c["column"] for c in cols])


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="output folder for gold<table>.csv (outside OneDrive)")
    p.add_argument("--ontology", default=str(DEFAULT_ONTOLOGY), help="folder with mapping/column_term_map.tsv")
    p.add_argument("--studies", type=int, default=4)
    p.add_argument("--seed", type=int, default=20261030)
    args = p.parse_args()

    rng = np.random.default_rng(args.seed)
    columns = read_columns(Path(args.ontology))
    studies = [Study(rng, i) for i in range(1, args.studies + 1)]
    per_table = defaultdict(list)
    for s in studies:
        for table, rows in scenario(s).items():
            per_table[table].append(frame(rng, rows, columns[table], s.id))

    os.makedirs(args.out, exist_ok=True)
    for table in TABLES:
        cols = columns[table]
        units = pd.DataFrame([{c["column"]: (UNITS_ROW if c["column"] == "studyId" else c["unit"]) for c in cols}])
        df = pd.concat([units] + per_table[table], ignore_index=True)
        df.to_csv(os.path.join(args.out, f"gold{table}.csv"), index=False)
        print(f"gold{table}: {len(df) - 1:,} rows x {df.shape[1]} columns")
    access = pd.DataFrame({"studyId": [s.id for s in studies], "isOpenData": [1 if i % 2 else 0 for i in range(len(studies))]})
    access.to_csv(os.path.join(args.out, "studyaccess.csv"), index=False)
    print(f"studyaccess: {len(access)} studies ({int(access['isOpenData'].sum())} open)")


if __name__ == "__main__":
    main()
