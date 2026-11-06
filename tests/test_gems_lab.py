"""Standardisation lab (repeatability, records needed), model code export and the AI number check,
on hand-worked examples."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from gems_analysis import (  # noqa: E402
    model_code,
    records_needed,
    reliability_curve,
    repeatability,
    repeatability_frame,
    unverified_numbers,
)


def days(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["studyId", "animal_id", "Treatment", "Period", "CH4_g_d"])


# Study S1, Control: animals A [10, 12], B [14, 16], C [18, 20]. By hand (nested one-way ANOVA):
# animal means 11, 15, 19 around 15 -> SSB = 2 x (16 + 0 + 16) = 64, df 3 - 1 = 2, MSB = 32;
# SSW = 3 x 2 = 6, df 6 - 3 = 3, MSW = 2; n0 = (6 - 12/6) / 2 = 2; between = (32 - 2) / 2 = 15, within = 2.
S1 = [("S1", "A", "Control", "1", 10), ("S1", "A", "Control", "1", 12), ("S1", "B", "Control", "1", 14),
      ("S1", "B", "Control", "1", 16), ("S1", "C", "Control", "1", 18), ("S1", "C", "Control", "1", 20)]


def test_repeatability_matches_the_hand_worked_anova():
    out = repeatability(repeatability_frame(days(S1), "CH4_g_d"), "CH4_g_d").iloc[0]
    assert (out["animals"], out["animal_periods"], out["records"]) == (3, 3, 6)
    assert out["sd_between"] ** 2 == pytest.approx(15.0)
    assert out["sd_within"] ** 2 == pytest.approx(2.0)
    assert out["r"] == pytest.approx(15 / 17)


def test_treatment_differences_do_not_count_as_animal_differences():
    # Same study, a second treatment whose animals are all 100 g/d higher: the animal variance must not change.
    shifted = [("S1", a.lower(), "Additive", "1", y + 100) for _, a, _, _, y in S1]
    out = repeatability(repeatability_frame(days(S1 + shifted), "CH4_g_d"), "CH4_g_d").iloc[0]
    assert out["sd_between"] ** 2 == pytest.approx(15.0)
    assert out["sd_within"] ** 2 == pytest.approx(2.0)


def test_studies_are_separate_and_too_small_studies_are_blank():
    tiny = [("S2", "X", "Control", "1", 5), ("S2", "X", "Control", "1", 7)]  # one animal: nothing to compare
    out = repeatability(repeatability_frame(days(S1 + tiny), "CH4_g_d"), "CH4_g_d").set_index("studyId")
    assert out.loc["S1", "r"] == pytest.approx(15 / 17)
    assert np.isnan(out.loc["S2", "r"])


def test_reliability_curve_and_records_needed():
    assert reliability_curve(0.5, np.array([1, 3])).tolist() == pytest.approx([0.5, 0.75])  # 3 x 0.5 / (1 + 2 x 0.5)
    assert records_needed(15 / 17, 0.9) == 2  # 9 x (2/15) = 1.2 -> 2
    assert records_needed(0.5, 0.9) == 9  # 9 x 1 = 9 exactly
    assert records_needed(0.0, 0.9) == np.inf


def test_model_code_period_level_two_levels():
    code = model_code("CH4_g_d", ["DryMatterIntake"], "Animal-period mean", "Control", True, "Common practice", False)
    assert "group_by(studyId, animal_id, Treatment, Period)" in code["R"]
    assert "lmer(CH4_g_d ~ Treatment + DryMatterIntake + (1 | studyId/animal_id)" in code["R"]
    assert 'relevel(factor(d$Treatment), ref = "Control")' in code["R"]
    assert 'groups="studyId", re_formula="1"' in code["Python"]
    assert '"Common practice"' in code["Python"]
    compile(code["Python"], "model.py", "exec")


def test_model_code_python_runs_and_matches_the_design(tmp_path):
    rng = np.random.default_rng(3)
    rows = []
    for s in range(3):
        for a in range(8):
            for trt in ["Control", "3-NOP"]:
                level = 400 + 30 * s + rng.normal(0, 20) - (60 if trt == "3-NOP" else 0)
                for day in range(5):
                    rows.append((f"S{s}", f"S{s}-A{a}", trt, "1", level + rng.normal(0, 10), 20 + rng.normal(0, 2)))
    pd.DataFrame(rows, columns=["studyId", "animal_id", "Treatment", "Period", "CH4_g_d", "DryMatterIntake"]) \
        .to_csv(tmp_path / "gems_cohort_animal_days.csv", index=False)
    code = model_code("CH4_g_d", ["DryMatterIntake"], "Animal-period mean", "Control", True, "Every matched visit", False)
    here = os.getcwd()
    os.chdir(tmp_path)
    try:
        scope: dict = {}
        exec(compile(code["Python"].replace("print(m.summary())", ""), "model.py", "exec"), scope)
    finally:
        os.chdir(here)
    m = scope["m"]
    assert len(scope["d"]) == 3 * 8 * 2  # one row per animal, treatment and period
    assert m.params["Treatment[T.3-NOP]"] == pytest.approx(-60, abs=15)


def test_unverified_numbers_flags_only_values_not_in_the_results():
    text = ("Methane fell by 68.2 g/d (95% CI −82.0 to −54.1) across 12 studies; p < 0.001. "
            "Between-study share 45%. See the 3 points below. A drop of 75.3 g/d is also claimed.")
    known = [-68.21, -82.03, -54.12, 12, 0.0004, 45.0]
    assert unverified_numbers(text, known) == ["75.3"]
