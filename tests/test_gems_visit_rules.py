"""GreenFeed visit rules (Analysis page, QC tab), checked on a small hand-made record.

Animal A (study S1, Control, period 1):
  2024-01-01 adaptation day: 01:00 300 g/d
  2024-01-02 measurement day: 01:00 310 (1 min), 04:00 320, 07:00 330 (interrupted), 10:00 340 (airflow 20),
                              13:00 900, 16:00 350 (interrupted flag unreadable)
  2024-01-03 measurement day: 01:00 360, 04:00 370
Animal B (study S1, Control, period 1): 2024-01-02 04:00 400
Plus one visit without a CH4 reading (-999) and one not matched to an animal: both are never used.
All visits last 3 min and have airflow 40 L/s unless noted.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from gems_analysis import (  # noqa: E402
    apply_daily_gases,
    prepare_visits,
    run_visit_rules,
    visit_attrition,
)

RAW = pd.DataFrame([
    # animal, start, CH4, minutes, airflow, interrupted
    ("A", "2024-01-01 01:00:00", 300, 3, 40, "false"),
    ("A", "2024-01-02 01:00:00", 310, 1, 40, "false"),
    ("A", "2024-01-02 04:00:00", 320, 3, 40, "false"),
    ("A", "2024-01-02 07:00:00", 330, 3, 40, "true"),
    ("A", "2024-01-02 10:00:00", 340, 3, 20, "false"),
    ("A", "2024-01-02 13:00:00", 900, 3, 40, "false"),
    ("A", "2024-01-02 16:00:00", 350, 3, None, "garbled"),
    ("A", "2024-01-03 01:00:00", 360, 3, 40, "false"),
    ("A", "2024-01-03 04:00:00", 370, 3, 40, "false"),
    ("B", "2024-01-02 04:00:00", 400, 3, 40, "false"),
    ("A", "2024-01-03 07:00:00", -999, 3, 40, "false"),  # no CH4 reading
    (None, "2024-01-03 07:00:00", 380, 3, 40, "false"),  # not matched to an animal
], columns=["animal_id", "Start_Time", "CH4_Massflow_g_d", "minutes", "Average_Airflow_L_s", "Was_Interrupted"])
RAW["studyId"] = "S1"
RAW["goodDataSeconds"] = RAW.pop("minutes") * 60.0
RAW.loc[2, "goodDataSeconds"] = np.nan
RAW["Good_Data_Duration"] = "00:03:00"  # used where goodDataSeconds is missing

DAYS = pd.DataFrame({
    "animal_id": ["A", "A", "A", "B"],
    "Date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-02"]),
    "Treatment": "Control", "Period": "1",
    "ExperimentalPhase": ["Adaptation", "Data collection", "Data collection", "Data collection"],
})


@pytest.fixture(scope="module")
def visits() -> pd.DataFrame:
    return prepare_visits(RAW, DAYS)


def status_of(v: pd.DataFrame, status: np.ndarray, animal: str, time: str) -> int:
    row = v.index[(v["animal_id"] == animal) & (v["start"] == pd.Timestamp(time))][0]
    return int(status[row])


def test_prepare_keeps_matched_visits_with_a_reading(visits):
    assert len(visits) == 10  # the -999 visit and the unmatched visit are out
    assert visits["minutes"].tolist().count(3.0) == 9  # one duration came from Good_Data_Duration
    assert visits["non_measurement"].sum() == 1  # the adaptation day
    assert visits["interrupted"].eq(True).sum() == 1 and visits["interrupted"].isna().sum() == 1


def test_no_rules_keeps_everything_and_daily_mean_is_plain(visits):
    out = run_visit_rules(visits, {})
    assert (out["status"] == 0).all()
    day = out["daily"].loc[("A", pd.Timestamp("2024-01-02"))]
    assert day["CH4_g_d"] == pytest.approx((310 + 320 + 330 + 340 + 900 + 350) / 6)
    assert day["nVisits"] == 6


@pytest.mark.parametrize("rules, animal, time, code", [
    ({"exclude_phases": True}, "A", "2024-01-01 01:00", 1),
    ({"min_minutes": 2.0}, "A", "2024-01-02 01:00", 2),
    ({"drop_interrupted": True}, "A", "2024-01-02 07:00", 3),
    ({"min_airflow": 26.0}, "A", "2024-01-02 10:00", 4),
    ({"max_ch4": 500.0}, "A", "2024-01-02 13:00", 5),
    ({"mad_k": 3.5}, "A", "2024-01-02 13:00", 6),  # 900 vs median 340, MAD 20: robust z = 18.9
])
def test_each_visit_rule_removes_exactly_its_visit(visits, rules, animal, time, code):
    status = run_visit_rules(visits, rules)["status"]
    assert status_of(visits, status, animal, time) == code
    assert (status != 0).sum() == 1


def test_rules_that_cannot_be_checked_keep_the_visit(visits):
    status = run_visit_rules(visits, {"drop_interrupted": True, "min_airflow": 26.0})["status"]
    assert status_of(visits, status, "A", "2024-01-02 16:00") == 0  # unreadable flag, no airflow


def test_minimum_visits_per_day(visits):
    status = run_visit_rules(visits, {"min_visits_day": 2})["status"]
    assert status_of(visits, status, "A", "2024-01-01 01:00") == 7  # only visit that day
    assert status_of(visits, status, "B", "2024-01-02 04:00") == 7
    assert (status != 0).sum() == 2


@pytest.mark.parametrize("rules", [{"min_days": 2}, {"min_visits": 5}, {"min_bins": 6}])
def test_animal_period_minimums(visits, rules):
    # A: 3 days, 9 visits, 6 three-hour periods (01, 04, 07, 10, 13, 16 h); B: 1 day, 1 visit, 1 period
    status = run_visit_rules(visits, rules)["status"]
    assert status_of(visits, status, "B", "2024-01-02 04:00") == 8
    assert (status[visits["animal_id"] == "A"] == 0).all()


def test_rules_combine_in_order_and_daily_uses_kept_visits(visits):
    out = run_visit_rules(visits, {"min_minutes": 2.0, "max_ch4": 500.0})
    day = out["daily"].loc[("A", pd.Timestamp("2024-01-02"))]
    assert day["CH4_g_d"] == pytest.approx((320 + 330 + 340 + 350) / 4)  # 1-min visit and 900 left out
    assert day["nVisits"] == 4


def test_attrition_counts_visits_days_and_animals(visits):
    rules = {"min_minutes": 2.0, "max_ch4": 500.0}
    a = visit_attrition(visits, run_visit_rules(visits, rules)["status"], rules)
    assert a["visits"].tolist() == [10, 9, 8]
    assert a["removed"].tolist() == [0, 1, 1]
    assert a["animal_days"].tolist() == [4, 4, 4]
    assert a["animals"].tolist() == [2, 2, 2]
    only_b = visit_attrition(visits, run_visit_rules(visits, rules)["status"], rules,
                             cohort=(visits["animal_id"] == "B").to_numpy())
    assert only_b["visits"].tolist() == [1, 1, 1]


def test_daily_gases_replace_the_cohort_values_and_methane_yield_follows(visits):
    daily = run_visit_rules(visits, {"min_minutes": 2.0, "max_ch4": 500.0})["daily"]
    cohort = pd.DataFrame({"animal_id": ["A", "A"], "Date": pd.to_datetime(["2024-01-02", "2024-01-04"]),
                           "CH4_g_d": [999.0, 999.0], "DryMatterIntake": [20.0, 20.0]})
    apply_daily_gases(cohort, daily)
    assert cohort.loc[0, "CH4_g_d"] == pytest.approx(335.0)
    assert cohort.loc[0, "MethaneYield"] == pytest.approx(335.0 / 20)
    assert np.isnan(cohort.loc[1, "CH4_g_d"]) and np.isnan(cohort.loc[1, "MethaneYield"])  # no kept visit that day
