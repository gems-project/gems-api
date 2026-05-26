"""Tests for affiliation parsing and geocode helpers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from gems_geography import (  # noqa: E402
    fallback_coordinates,
    geocode_query,
    humanize_affiliation,
    preferred_country,
    split_camel_case,
)


def test_split_camel_case_affiliation():
    raw = "LethbridgeResearchAndDevelopmentCentreCanada"
    assert "Lethbridge" in split_camel_case(raw)
    assert "Canada" in split_camel_case(raw)


def test_preferred_country_canada():
    assert preferred_country("LethbridgeResearchAndDevelopmentCentreCanada") == "Canada"


def test_geocode_query_humanizes():
    q = geocode_query("UniversityOfGuelph")
    assert "Guelph" in q


def test_fallback_lethbridge_not_zurich():
    coords = fallback_coordinates("LethbridgeResearchAndDevelopmentCentreCanada")
    assert coords is not None
    assert coords["country"] == "Canada"
    assert abs(coords["lat"] - 49.694) < 2


def test_fallback_eth_zurich_only_when_appropriate():
    coords = fallback_coordinates("ETHZurich")
    assert coords is not None
    assert coords["country"] == "Switzerland"


def test_humanize_preserves_spaced_names():
    assert humanize_affiliation("Cornell University") == "Cornell University"
