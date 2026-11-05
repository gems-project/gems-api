"""Hand-checked numbers for the variables the Analysis page calculates per cow-day."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from gems_analysis import CALCULATED, add_calculated, calculated_dictionary, diet_means  # noqa: E402

# Treatment t1: two diet analyses (CP 160 and 170 -> 165 g/kg DM; GE 18.0 and 18.4 -> 18.2 MJ/kg DM).
# Treatment t2: one analysis without GE.
DIET = pd.DataFrame({
    "treatment_id": ["t1", "t1", "t2"],
    "CrudeProtein": [160.0, 170.0, 150.0],
    "NeutralDetergentFiber": [300.0, 320.0, 350.0],
    "GrossEnergy": [18.0, 18.4, None],
})


def day(**values) -> dict:
    base = {"CH4_g_d": 420.0, "DryMatterIntake": 24.0, "MilkProduction": 30.0, "MilkFat": 4.0, "MilkProtein": 3.3,
            "ForageIntake": 14.4, "BodyWeight": 600.0, "GrossEnergyIntake": None, "treatment_id": "t1",
            "treatmentConflict": False}
    base.update(values)
    return base


def calc(*rows: dict) -> pd.DataFrame:
    return add_calculated(pd.DataFrame(list(rows)), DIET)


def test_diet_means_average_all_analyses():
    means = diet_means(DIET)
    assert means.loc["t1", "CrudeProtein"] == pytest.approx(165.0)
    assert means.loc["t1", "GrossEnergy"] == pytest.approx(18.2)
    assert np.isnan(means.loc["t2", "GrossEnergy"])


def test_milk_and_methane_ratios():
    r = calc(day()).iloc[0]
    fpcm = 30.0 * (0.1226 * 4.0 + 0.0722 * 3.3 + 0.2534)  # 29.4618 kg/d
    assert r["MethaneYield"] == pytest.approx(17.5)  # 420 / 24
    assert r["MethaneIntensityMilk"] == pytest.approx(14.0)  # 420 / 30
    assert r["FPCM"] == pytest.approx(fpcm)
    assert r["FPCM"] == pytest.approx(29.4618)
    assert r["MethaneIntensityFPCM"] == pytest.approx(420.0 / fpcm)
    assert r["MilkFatYield"] == pytest.approx(1.2)
    assert r["MilkProteinYield"] == pytest.approx(0.99)
    assert r["FeedEfficiency"] == pytest.approx(fpcm / 24.0)
    assert r["ForageShare"] == pytest.approx(60.0)
    assert r["MethanePerMetabolicBW"] == pytest.approx(420.0 / 600.0 ** 0.75)
    assert r["DMIPercentBW"] == pytest.approx(4.0)


def test_nutrient_intakes_use_average_diet():
    r = calc(day()).iloc[0]
    assert r["CrudeProteinIntake"] == pytest.approx(3.96)  # 24 x 165 / 1000
    assert r["NeutralDetergentFiberIntake"] == pytest.approx(7.44)  # 24 x 310 / 1000
    assert np.isnan(r["StarchIntake"])  # no starch analysed


def test_gross_energy_recorded_first_then_from_diet():
    recorded, from_diet, neither = calc(day(GrossEnergyIntake=450.0), day(), day(treatment_id="t2")).to_dict("records")
    assert recorded["GrossEnergyIntakeAll"] == pytest.approx(450.0)
    assert recorded["GrossEnergyIntakeSource"] == "recorded"
    assert recorded["Ym"] == pytest.approx(420 * 0.05565 / 450 * 100)
    assert from_diet["GrossEnergyIntakeAll"] == pytest.approx(24 * 18.2)  # 436.8 MJ/d
    assert from_diet["GrossEnergyIntakeSource"] == "from diet"
    assert from_diet["Ym"] == pytest.approx(420 * 0.05565 / 436.8 * 100)  # about 5.35 %
    assert np.isnan(neither["GrossEnergyIntakeAll"])  # no recorded GE and no diet GE: no default is filled in
    assert neither["GrossEnergyIntakeSource"] is None
    assert np.isnan(neither["Ym"])


def test_missing_zero_and_conflicting_inputs_give_blanks():
    no_dmi, zero_dmi, conflict, no_treatment, no_weighing = calc(
        day(DryMatterIntake=None), day(DryMatterIntake=0.0), day(treatmentConflict=True), day(treatment_id=None),
        day(BodyWeight=None)).to_dict("records")
    assert np.isnan(no_dmi["MethaneYield"]) and np.isnan(no_dmi["CrudeProteinIntake"])
    assert np.isnan(zero_dmi["MethaneYield"])  # never divide by zero
    assert conflict["MethaneYield"] == pytest.approx(17.5)  # not diet-based, still calculated
    assert np.isnan(conflict["CrudeProteinIntake"]) and np.isnan(conflict["GrossEnergyIntakeAll"])
    assert np.isnan(no_treatment["CrudeProteinIntake"])
    assert np.isnan(no_weighing["MethanePerMetabolicBW"]) and np.isnan(no_weighing["DMIPercentBW"])


def test_conflict_flag_read_as_text():
    as_text, not_conflict = calc(day(treatmentConflict="true"), day(treatmentConflict="false")).to_dict("records")
    assert np.isnan(as_text["CrudeProteinIntake"])
    assert not_conflict["CrudeProteinIntake"] == pytest.approx(3.96)


def test_missing_columns_and_empty_diet_do_not_fail():
    out = add_calculated(pd.DataFrame({"CH4_g_d": [400.0], "DryMatterIntake": [20.0]}), pd.DataFrame())
    assert out.loc[0, "MethaneYield"] == pytest.approx(20.0)
    for c in CALCULATED:
        assert c in out
    assert np.isnan(out.loc[0, "FPCM"]) and np.isnan(out.loc[0, "CrudeProteinIntake"])


def test_dictionary_lists_every_calculated_column():
    d = calculated_dictionary()
    assert set(CALCULATED) | {"GrossEnergyIntakeSource"} == set(d["column"])
    assert d["derivedFrom"].str.startswith("calculated in the Analysis page").all()
