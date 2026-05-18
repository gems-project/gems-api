from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

from chat_sql import SQLValidationError, validate_aggregate_query

ALLOWED = frozenset({"goldbodyweight"})
REDACTED = frozenset({"workbookPath", "contractName"})


def _validate(sql: str):
    return validate_aggregate_query(sql, ALLOWED, "gems_catalog", "gold_v1", REDACTED)


def test_rejects_drop_table():
    with pytest.raises(SQLValidationError):
        _validate("DROP TABLE gems_catalog.gold_v1.goldbodyweight")


def test_rejects_delete_from():
    with pytest.raises(SQLValidationError):
        _validate("DELETE FROM gems_catalog.gold_v1.goldbodyweight")


def test_rejects_select_star():
    with pytest.raises(SQLValidationError):
        _validate("SELECT * FROM gems_catalog.gold_v1.goldbodyweight LIMIT 10")


def test_rejects_select_without_aggregation():
    with pytest.raises(SQLValidationError):
        _validate("SELECT AnimalIdentifier FROM gems_catalog.gold_v1.goldbodyweight")


def test_rejects_large_limit_without_aggregation():
    with pytest.raises(SQLValidationError):
        _validate("SELECT AnimalIdentifier FROM gems_catalog.gold_v1.goldbodyweight LIMIT 1000")


def test_allows_aggregate_count():
    assert _validate("SELECT COUNT(*) AS n FROM gems_catalog.gold_v1.goldbodyweight")
