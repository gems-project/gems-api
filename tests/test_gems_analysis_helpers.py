"""Analysis-page helpers: the plotting sample, means by study and treatment, and the model-result charts."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from gems_analysis import (  # noqa: E402
    box_stats,
    covariate_slopes,
    plot_sample,
    study_treatment_means,
    treatment_effects,
    variance_shares,
)


def test_box_stats_quartiles_and_tukey_whiskers():
    df = pd.DataFrame({"studyId": ["A"] * 10, "y": [1, 2, 3, 4, 5, 6, 7, 8, 9, 100.0]})
    b = box_stats(df, "y").iloc[0]
    assert (b["n"], b["q1"], b["median"], b["q3"]) == (10, 3.25, 5.5, 7.75)
    assert (b["lowerfence"], b["upperfence"]) == (1.0, 9.0)  # 100 is beyond 7.75 + 1.5 x 4.5 = 14.5
    assert b["beyond"] == 1


def test_covariate_slopes_separate_within_and_between_studies():
    # Inside every study y rises 2 per unit of x, but studies with higher x have lower y (study means fall 1 per unit).
    df = pd.DataFrame({"studyId": list("AAABBBCCC"), "x": [1, 2, 3, 5, 6, 7, 9, 10, 11.0],
                       "y": [12, 14, 16, 8, 10, 12, 4, 6, 8.0]})
    s = covariate_slopes(df, "y", "x")
    assert s["within"] == pytest.approx(2.0)
    assert s["between"] == pytest.approx(-1.0)
    assert s["overall"] == pytest.approx(-84 / 102)  # pooled: mixes the two (worked by hand)
    assert s["lines"]["slope"].tolist() == pytest.approx([2.0, 2.0, 2.0])
    assert s["lines"][["x0", "x1"]].values.tolist() == [[1, 3], [5, 7], [9, 11]]
    assert (s["xmin"], s["xmax"]) == (1.0, 11.0)


def test_covariate_slopes_skip_studies_without_spread_or_records():
    df = pd.DataFrame({"studyId": ["A", "A", "A", "B", "B"], "x": [2, 2, 2, 1, 3.0], "y": [5, 6, 7, 1, 2.0]})
    s = covariate_slopes(df, "y", "x")
    assert s["lines"].empty  # A: x never varies; B: only 2 records
    assert np.isnan(s["between"])  # fewer than 3 studies


def test_means_average_each_animal_first_then_t_interval():
    # Control: animal a1 measured 3 times (all 10), a2 once (12), a3 once (14); 3-NOP: one animal.
    df = pd.DataFrame({"studyId": ["A"] * 6, "Treatment": ["Control"] * 5 + ["3-NOP"],
                       "animal_id": ["a1", "a1", "a1", "a2", "a3", "a4"], "y": [10, 10, 10, 12, 14, 20.0]})
    m = study_treatment_means(df, "y").set_index("Treatment")
    c = m.loc["Control"]
    assert (c["animals"], c["records"]) == (3, 5)
    assert c["mean"] == pytest.approx(12.0)  # mean of the animal means 10, 12, 14 (the record mean is 11.2)
    half = 4.302653 * 2.0 / np.sqrt(3)  # t(0.975, 2 df) x SD / sqrt(n)
    assert c["ci_low"] == pytest.approx(12.0 - half, abs=1e-4)
    assert c["ci_high"] == pytest.approx(12.0 + half, abs=1e-4)
    one = m.loc["3-NOP"]
    assert one["animals"] == 1 and one["mean"] == pytest.approx(20.0)
    assert np.isnan(one["ci_low"]) and np.isnan(one["ci_high"])  # one animal: no interval


def test_treatment_effects_keep_only_treatment_rows_with_plain_names():
    coefs = pd.DataFrame({"term": ["Intercept", "Treatment[T.3-NOP]", "Treatment[T.High fat]", "DryMatterIntake"],
                          "estimate": [400.0, -68.0, -20.0, 14.0], "ci_low": [380.0, -82.0, -35.0, 10.0],
                          "ci_high": [420.0, -54.0, -5.0, 18.0], "p_value": [0.0, 0.0, 0.01, 0.0]})
    e = treatment_effects(coefs)
    assert e["treatment"].tolist() == ["3-NOP", "High fat"]
    assert e["estimate"].tolist() == [-68.0, -20.0]


def test_variance_shares_add_up_to_100_and_skip_missing():
    s = variance_shares([{"name": "Group", "variance": 45.0}, {"name": "animal_id", "variance": 30.0},
                         {"name": "residual", "variance": 25.0}, {"name": "broken", "variance": None}])
    assert s["name"].tolist() == ["Group", "animal_id", "residual"]
    assert s["share"].tolist() == pytest.approx([45.0, 30.0, 25.0])
    assert variance_shares([]).empty


def frame() -> pd.DataFrame:
    # study A: 1,000 Control + 20 3-NOP rows; study B: 5 rows with no treatment recorded
    return pd.DataFrame({
        "studyId": ["A"] * 1020 + ["B"] * 5,
        "Treatment": ["Control"] * 1000 + ["3-NOP"] * 20 + [None] * 5,
        "value": np.arange(1025, dtype=float),
    })


def test_caps_each_group_and_keeps_small_groups_whole():
    s = plot_sample(frame(), ["studyId", "Treatment"], per_group=300)
    assert (s["Treatment"] == "Control").sum() == 300
    assert (s["Treatment"] == "3-NOP").sum() == 20
    assert (s["studyId"] == "B").sum() == 5  # a blank treatment is a group of its own, not dropped
    assert len(s) == 325


def test_same_points_every_time_and_rows_unchanged():
    df = frame()
    a, b = plot_sample(df, ["studyId", "Treatment"]), plot_sample(df, ["studyId", "Treatment"])
    assert a.index.equals(b.index)
    pd.testing.assert_frame_equal(a, df.loc[a.index])  # a subset of the original rows, values untouched


def test_small_or_empty_input_comes_back_whole():
    df = frame().iloc[:10]
    assert len(plot_sample(df, ["studyId", "Treatment"])) == 10
    assert plot_sample(df.iloc[0:0], ["studyId"]).empty
