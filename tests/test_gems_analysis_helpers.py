"""Analysis-page helpers that keep the page fast: the plotting sample."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from gems_analysis import plot_sample  # noqa: E402


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
