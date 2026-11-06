"""Explore and KG view helpers (Analysis page phase D), on hand-made examples."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from gems_analysis import (  # noqa: E402
    animal_dot,
    animal_graph,
    animal_spread,
    kg_schema_counts,
    schema_dot,
    studies_dot,
    study_graph,
    treatment_runs,
)


def test_treatment_runs_cut_where_treatment_or_period_changes():
    d = pd.DataFrame({
        "Date": pd.to_datetime(["2024-01-07", "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
                                "2024-01-05", "2024-01-06", "2024-01-08", "2024-01-09"]),
        "Treatment": ["Control", "Control", "Control", "Control", "3-NOP", "3-NOP", "3-NOP", "Control", None],
        "Period": ["3", "1", "1", "1", "2", "2", "2", "3", None],
    })  # given out of order; the last day has no design record
    runs = treatment_runs(d)
    assert runs[["Treatment", "Period", "days"]].values.tolist() == [["Control", "1", 3], ["3-NOP", "2", 3],
                                                                     ["Control", "3", 2]]
    assert runs["start"].dt.day.tolist() == [1, 4, 7] and runs["end"].dt.day.tolist() == [3, 6, 8]


def test_animal_spread_mean_sd_and_main_treatment():
    d = pd.DataFrame({"animal_id": ["B", "A", "A", "A", "B", "B", "C"],
                      "y": [20.0, 10.0, 12.0, 14.0, 20.0, 20.0, np.nan],
                      "Treatment": ["3-NOP", "Control", "Control", "Control", "3-NOP", "Control", "Control"]})
    s = animal_spread(d, "y")
    assert s["animal_id"].tolist() == ["A", "B"]  # lowest mean first; C has no values
    a, b = s.iloc[0], s.iloc[1]
    assert (a["days"], a["mean"], a["sd"], a["treatment"], a["treatments"]) == (3, 12.0, pytest.approx(2.0), "Control", 1)
    assert (b["days"], b["mean"], b["sd"], b["treatment"], b["treatments"]) == (3, 20.0, 0.0, "3-NOP", 2)


DAYS = pd.DataFrame({
    "studyId": ["S1", "S1", "S1", "S2"], "animal_id": ["a1", "a1", "a2", "a3"],
    "treatment_id": ["t1", "t2", "t1", "t3"], "Treatment": ["Control", "3-NOP", "Control", "Control"],
    "nDesignRecords": [1, 1, 1, 1], "nMilkRecords": [2, 0, 2, np.nan],
    "BodyWeight": [600.0, np.nan, np.nan, 550.0], "DryMatterIntake": [20.0, 21.0, np.nan, np.nan],
    "nVisits": [3, 2, np.nan, 1],
})
# t1's diet has a blank ingredient name; " Corn silage " (t2) is the same name once its outer spaces go.
DIETS = pd.DataFrame({"treatment_id": ["t1"] * 3 + ["t2"] * 2 + ["tX"] * 4,
                      "diet_id": ["d1"] * 3 + ["d2"] * 2 + ["dX"] * 4,
                      "Ingredients": ["Soybean meal", "Corn silage", "", " Corn silage ", "Barley", "Hay", "Hay",
                                      "Hay", "Hay"],
                      "Proportion": [30.0, 50.0, 20.0, 60.0, 40.0, 25.0, 25.0, 25.0, 25.0]})
NUTRIENTS = pd.DataFrame({"treatment_id": ["t1", "t3", "t3"], "diet_id": ["d1", "d3", "d3"],
                          "CrudeProtein": [160.0, 170.0, 180.0], "NeutralDetergentFiber": [300.0, np.nan, np.nan]})


def test_schema_counts_follow_the_cohort():
    c = kg_schema_counts(DAYS, DIETS, NUTRIENTS)
    assert c == {"Study": 2, "Animal": 3, "Treatment": 3, "DesignRecord": 4, "MilkObservation": 4,
                 "BodyWeightObservation": 2, "IntakeDayObservation": 2, "GreenFeedVisit": 6,
                 "Diet": 3, "DietComponent": 5, "DietNutrientRecord": 3, "GreenFeedUnit": None,
                 "ingredientNames": 3}  # Soybean meal, Corn silage, Barley (the blank name is not counted)
    assert kg_schema_counts(DAYS, DIETS, NUTRIENTS, units={"u1", "u2"})["GreenFeedUnit"] == 2
    assert kg_schema_counts(DAYS, DIETS.drop(columns="Ingredients"), NUTRIENTS)["ingredientNames"] is None


def test_schema_dot_names_counts_and_links():
    dot = schema_dot(kg_schema_counts(DAYS, DIETS, NUTRIENTS))
    for a, link, b in [("Animal", "belongsToStudy", "Study"), ("DesignRecord", "receivesTreatment", "Treatment"),
                       ("Treatment", "usesDiet", "Diet"), ("GreenFeedVisit", "recordedBy", "GreenFeedUnit"),
                       ("GreenFeedVisit", "aboutAnimal", "Animal"), ("Diet", "hasIngredient", "DietComponent")]:
        assert f'"{a}" -> "{b}" [label="{link}"];' in dot
    # the four kinds of records about an animal share one arrow, drawn from their box
    assert dot.count("ltail=cluster_records") == 1 and '"MilkObservation" -> "Animal"' not in dot
    assert '<b>Study</b><br/><font point-size="10" color="#52514e">2 studies</font>' in dot
    assert ">3 animals<" in dot and ">2 days with body weight<" in dot and ">3 nutrient analyses<" in dot
    assert ">5 ingredient lines<" in dot and ">3 different ingredient names<" in dot
    assert "load the visits to count" in dot  # no GreenFeed unit count yet
    assert 'tooltip="Studies in your cohort."' in dot
    assert dot.count("{") == dot.count("}")


def test_animal_graph_and_dot():
    animals = pd.DataFrame({"animal_id": ["a1"], "AnimalIdentifier": ["Cow 7"], "Breed": ['Holstein "HF"'],
                            "Parity": [2.0]})
    g = animal_graph("a1", DAYS, animals, DIETS, NUTRIENTS, {"/id/gfunit/S1/FID123": 5})
    assert g["name"] == "Cow 7" and g["study"] == "S1" and g["facts"] == ['Breed: Holstein "HF"', "Parity: 2"]
    assert [(t["label"], t["days"], t["diet_lines"], t["diet_records"]) for t in g["treatments"]] == \
        [("Control", 1, 3, 1), ("3-NOP", 1, 2, 0)]
    control, nop = g["treatments"]
    assert control["ingredients"] == [("Corn silage", 50.0), ("Soybean meal", 30.0), ("(no name)", 20.0)]
    assert control["nutrients"] == {"CP": 160.0, "NDF": 300.0} and control["analyses"] == 1
    assert nop["ingredients"] == [("Corn silage", 60.0), ("Barley", 40.0)] and nop["nutrients"] == {}
    assert g["observations"] == {"DesignRecord": 2, "MilkObservation": 2, "BodyWeightObservation": 1,
                                 "IntakeDayObservation": 2, "GreenFeedVisit": 5}
    dot = animal_dot(g)
    assert "<b>Animal Cow 7</b>" in dot and "Breed: Holstein &quot;HF&quot;" in dot  # escaped for the label
    assert "<b>Treatment Control</b>" in dot and ">1 day<" in dot and 'label="receivesTreatment"' in dot
    assert 'label="usesDiet"' in dot and ">Corn silage<" in dot and ">50%<" in dot
    assert "CP 160 · NDF 300 g/kg DM (mean of 1 analysis)" in dot
    assert "no nutrient analysis" in dot  # 3-NOP's diet has ingredients but no analysis
    assert "<b>GreenFeedUnit FID123</b>" in dot and ">5 visits<" in dot and 'label="recordedBy"' in dot
    assert dot.count("{") == dot.count("}")
    many = {**control, "ingredients": [(f"Feed {k}", 1.0) for k in range(15)]}
    assert "+ 3 more" in animal_dot({**g, "treatments": [many]}, max_lines=12)


# Two studies: S1 (two Holsteins, Control and T2 with 3-NOP), S2 (one Jersey on treatment "1").
DAYS2 = pd.DataFrame({
    "studyId": ["S1", "S1", "S1", "S1", "S2"], "animal_id": ["a1", "a1", "a2", "a1", "b1"],
    "treatment_id": ["t1", "t1", "t1", "t2", "t3"], "Treatment": ["Control", "Control", "Control", "T2", "1"],
    "MethaneInhibitingFeedAdditive": ["Control", "Control", "Control", "3-NOP", np.nan],
})
ANIMALS2 = pd.DataFrame({"animal_id": ["a1", "a2", "b1"], "Breed": ["Holstein", "Holstein", "Jersey"]})
DIETS2 = pd.DataFrame({"treatment_id": ["t1", "t1", "t2", "t2", "t3", "t3"],
                       "diet_id": ["d1", "d1", "d2", "d2", "d3", "d3"],
                       "Ingredients": ["Corn silage", "Soybean meal", "Corn silage", "Barley", "Corn silage",
                                       "corn silage"],
                       "Proportion": [50.0, 50.0, 60.0, 40.0, 70.0, 30.0]})
NUTRIENTS2 = pd.DataFrame({"treatment_id": ["t1", "t1"], "diet_id": ["d1", "d1"],
                           "CrudeProtein": [160.0, 170.0], "NeutralDetergentFiber": [300.0, 320.0]})


def test_study_graph_two_studies():
    g = study_graph(["S1", "S2", "S9"], DAYS2, ANIMALS2, DIETS2, NUTRIENTS2)  # S9 has no days: left out
    assert g["studies"] == [{"id": "S1", "animals": 2, "breeds": "Holstein"},
                            {"id": "S2", "animals": 1, "breeds": "Jersey"}]
    assert [(t["study"], t["label"], t["animals"], t["days"], t["additive"], t["diets"]) for t in g["treatments"]] == [
        ("S1", "Control", 2, 3, None, ["d1"]),  # additive "Control" says nothing more than the label
        ("S1", "T2", 1, 1, "3-NOP", ["d2"]),
        ("S2", "1", 1, 1, None, ["d3"])]
    assert g["diets"]["d1"] == {"nutrients": {"CP": 165.0, "NDF": 310.0}, "analyses": 2,
                                "ingredients": [("Corn silage", 50.0), ("Soybean meal", 50.0)]}
    assert g["diets"]["d3"] == {"nutrients": {}, "analyses": 0,
                                "ingredients": [("Corn silage", 70.0), ("corn silage", 30.0)]}
    # names are matched exactly as recorded: "Corn silage" links the two studies, "corn silage" does not
    assert g["names"] == {"Corn silage": 2, "Soybean meal": 1, "Barley": 1, "corn silage": 1}
    assert len(g["lines"]) == 6
    assert [s["id"] for s in study_graph(["S2", "S1"], DAYS2, ANIMALS2, DIETS2, NUTRIENTS2)["studies"]] == ["S2", "S1"]


def test_studies_dot():
    g = study_graph(["S1", "S2"], DAYS2, ANIMALS2, DIETS2, NUTRIENTS2)
    dot = studies_dot(g)
    assert "subgraph cluster_study0" in dot and "subgraph cluster_study1" in dot
    assert "<b>Study S1</b>" in dot and "2 animals · Holstein" in dot
    assert '"s0" -> "s0t0" [dir=back, tooltip="belongsToStudy"];' in dot  # arrow points at the study
    assert '"s0t0" -> "s0d0" [tooltip="usesDiet"];' in dot
    assert "additive: 3-NOP" in dot and "Treatment T2" in dot
    assert "CP 165 · NDF 310 g/kg DM (mean of 2 analyses)" in dot and "no nutrient analysis" in dot
    assert ">Soybean meal<" in dot and ">50%<" in dot and ">70%<" in dot  # ingredients listed in each diet
    # only "Corn silage" is used by both studies: it gets a node, bold in the diets, one dashed line per study
    node_lines = [line for line in dot.splitlines() if line.lstrip().startswith('"n')]
    assert len(node_lines) == 1 and "<b>Corn silage</b>" in node_lines[0] and "in 2 studies" in node_lines[0]
    assert "<b>Corn silage</b>" in dot and ">corn silage<" in dot  # the lower-case spelling is a different name
    links = [line for line in dot.splitlines() if '-> "n0"' in line]
    assert len(links) == 2 and "ltail=cluster_study0" in links[0] and "ltail=cluster_study1" in links[1]
    assert "Study S1 uses Corn silage in 2 diets (50–60%)" in links[0]
    assert "Study S2 uses Corn silage in 1 diet (70%)" in links[1]
    assert dot.count("{") == dot.count("}")
    capped = studies_dot(g, max_treatments=1)  # S1 shows Control only, and says what it left out
    assert "+ 1 more treatment<" in capped and "Treatment T2" not in capped and capped.count('-> "n0"') == 2
    assert ">50 kg DM/d<" in studies_dot(g, "kg DM/d")  # other units get a space
