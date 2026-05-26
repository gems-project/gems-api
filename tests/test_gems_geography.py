"""Tests for affiliation parsing and geocode helpers."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from gems_geography import (  # noqa: E402
    dedupe_institution_labels,
    fallback_coordinates,
    geocode_query,
    humanize_affiliation,
    institution_key,
    preferred_country,
    split_camel_case,
)


def test_split_camel_case_affiliation():
    raw = "LethbridgeResearchAndDevelopmentCentreCanada"
    assert "Lethbridge" in split_camel_case(raw)
    assert "Canada" in split_camel_case(raw)


def test_italian_university_humanize():
    raw = "UniversitàCattolicaDelSacroCuore"
    label = humanize_affiliation(raw)
    assert "Cattolica" in label
    assert "Sacro Cuore" in label
    assert "del" in label.lower()
    assert " " not in label.split()[0][-2:]  # no space inside Università


def test_ampersand_agriculture_canada():
    label = humanize_affiliation("Agriculture&Agri Food Canada")
    assert "Agriculture" in label
    assert "Canada" in label


def test_dedupe_cornell():
    labels = dedupe_institution_labels(
        ["Cornell University", "Cornell University", "University Of Guelph"]
    )
    assert labels.count("Cornell University") == 1
    assert len(labels) == 2


def test_preferred_country_canada():
    assert preferred_country("LethbridgeResearchAndDevelopmentCentreCanada") == "Canada"


def test_geocode_query_cattolica():
    q = geocode_query("UniversitàCattolicaDelSacroCuore")
    assert "Italy" in q or "Cattolica" in q


def test_geocode_query_fbn():
    q = geocode_query("ResearchInstituteForFarmAnimalBiology(FBN)")
    assert "Germany" in q or "FBN" in q


def test_parentheses_spacing():
    label = humanize_affiliation("ResearchInstituteForFarmAnimalBiology(FBN)")
    assert "(FBN)" in label or "(fbn)" in label.lower()


def test_fallback_fbn_germany():
    coords = fallback_coordinates("ResearchInstituteForFarmAnimalBiology(FBN)")
    assert coords is not None
    assert coords["country"] == "Germany"


def test_fallback_cattolica_italy():
    coords = fallback_coordinates("UniversitàCattolicaDelSacroCuore")
    assert coords is not None
    assert coords["country"] == "Italy"


def test_institution_key_stable():
    a = institution_key("Cornell University")
    b = institution_key("cornell university")
    assert a == b
