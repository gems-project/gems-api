"""GreenFeed QC tab diagnostics (visit duration, visits per day, hour of day, CH4 by hour, visits and
time-of-day coverage per animal-period), on a hand-made example."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from gems_analysis import _value_bins, prepare_visits, run_visit_rules, visit_qc_charts  # noqa: E402

# Cow A (dairy): day 1 at 08:10 (3 min, 400 g/d), 14:20 (1 min, 300), 20:05 (2.5 min, 500); day 2 at 09:00
# (4 min, 450). Steer B (beef): day 1 at 08:30 (2 min, 200) and 08:45 (12 s, 220).
RAW = pd.DataFrame({
    "studyId": ["S1"] * 6, "animal_id": ["A", "A", "A", "A", "B", "B"],
    "Start_Time": ["2024-01-01 08:10", "2024-01-01 14:20", "2024-01-01 20:05", "2024-01-02 09:00",
                   "2024-01-01 08:30", "2024-01-01 08:45"],
    "goodDataSeconds": [180, 60, 150, 240, 120, 12],
    "CH4_Massflow_g_d": [400.0, 300.0, 500.0, 450.0, 200.0, 220.0],
})
DAYS = pd.DataFrame({"animal_id": ["A", "A", "B"], "Date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-01"]),
                     "Treatment": ["T1", "T1", "T2"], "Period": ["1", "1", "1"]})
PRODUCTION = pd.Series({"A": "Dairy", "B": "Beef"})


@pytest.fixture()
def charts():
    v = prepare_visits(RAW, DAYS)
    status = run_visit_rules(v, {"min_minutes": 2.0})["status"]  # drops A's 1-min and B's 12-s visit
    return v, status, visit_qc_charts(v, status, production=PRODUCTION, min_n=1)


def test_duration_and_visits_per_day(charts):
    _, _, c = charts
    dur = c["duration"].set_index("x0")["visits"]
    assert dur.sum() == 6 and dur[dur > 0].index.tolist() == [0.0, 1.0, 2.0, 2.5, 3.0, 4.0]  # before the rule
    assert c["duration"]["x1"].iloc[-1] == 8.25  # last bin: 8 min or more
    assert c["per_day"]["animal_days"].tolist() == [1, 1, 1, 0, 0, 0, 0, 0]  # days with 1, 2 and 3 visits


def test_hours_and_ch4_by_hour(charts):
    _, _, c = charts
    hours = c["hours"].set_index("hour")["visits"]
    assert hours.sum() == 4 and (hours[8], hours[9], hours[20]) == (2, 1, 1)  # kept visits only
    # only A's day 1 keeps 2 visits: mean 450, so 400 is -11.1 % at 08:00 and 500 is +11.1 % at 20:00
    d = c["diurnal"]
    assert d["production"].tolist() == ["Dairy", "Dairy"] and d["hour"].tolist() == [8, 20]
    assert d["pct"].tolist() == pytest.approx([-100 / 9, 100 / 9])
    assert visit_qc_charts(*charts[:2], production=PRODUCTION)["diurnal"].empty  # fewer than 30 visits an hour


def test_animal_period_visits_and_coverage(charts):
    _, _, c = charts
    units = c["unit_visits"].set_index("x0")["n"]
    assert units.sum() == 2 and (units[1], units[3]) == (1, 1)  # B has 1 visit, A has 3
    # A's kept visits fall in the 3-h windows 06-09, 09-12 and 18-21; B's in 06-09
    assert c["coverage"].set_index("windows")["units"].to_dict() == {0: 0, 1: 1, 2: 0, 3: 1, 4: 0, 5: 0, 6: 0, 7: 0, 8: 0}


def test_cohort_mask(charts):
    v, status, _ = charts
    c = visit_qc_charts(v, status, cohort=(v["animal_id"] == "A").to_numpy(), production=PRODUCTION, min_n=1)
    assert c["hours"]["visits"].sum() == 3 and c["per_day"]["animal_days"].sum() == 2


def test_value_bins():
    b = _value_bins(np.arange(1, 101))  # 98th percentile ~98 -> bins of 4, the last one takes 100
    assert b["n"].sum() == 100 and (b["x1"] - b["x0"]).eq(4).all() and b["n"].iloc[0] == 3 and b["n"].iloc[-1] == 1
    assert _value_bins(np.array([], dtype=int)).empty
