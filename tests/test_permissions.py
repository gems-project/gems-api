from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))

import permissions


@dataclass
class User:
    email: str
    email_verified: bool


def test_not_logged_in_has_no_access():
    assert not permissions.has_dashboard_access(None)
    assert not permissions.has_api_access(None)


def test_logged_in_not_verified_has_no_access(monkeypatch):
    monkeypatch.setenv("ALLOWED_USERS", "analyst@example.com")
    user = User("analyst@example.com", False)
    assert not permissions.has_dashboard_access(user)
    assert not permissions.has_api_access(user)


def test_dashboard_only_user(monkeypatch):
    monkeypatch.setenv("ALLOWED_USERS", "dashboard@example.com")
    monkeypatch.setenv("GEMS_API_BASE_URL", "")
    user = User("dashboard@example.com", True)
    assert permissions.has_dashboard_access(user)
    assert not permissions.has_api_access(user)


def test_api_tier_user(monkeypatch):
    monkeypatch.setenv("ALLOWED_USERS", "api@example.com")
    monkeypatch.setattr(permissions, "api_check", lambda user, bearer_token=None: True)
    user = User("api@example.com", True)
    assert permissions.has_dashboard_access(user)
    assert permissions.has_api_access(user, "token")