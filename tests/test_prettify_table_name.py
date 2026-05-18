from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from utils import prettify_table_name


def test_plain_lowercase_names():
    assert prettify_table_name("animalcharacteristics") == "Animal Characteristics"
    assert prettify_table_name("intakeperday") == "Intake Per Day"


def test_greenfeed_stays_one_word():
    assert prettify_table_name("greenfeedrawvisitationdata") == "GreenFeed Raw Visitation Data"
    assert prettify_table_name("goldgreenfeedrawvisitationdata") == "GreenFeed Raw Visitation Data"


def test_strips_bronze_and_gold_prefixes():
    assert prettify_table_name("bronzeanimalcharacteristics") == "Animal Characteristics"
    assert prettify_table_name("goldanimalcharacteristics") == "Animal Characteristics"


def test_camel_case_and_digits():
    assert prettify_table_name("greenFeedRawV2Data") == "GreenFeed Raw V 2 Data"