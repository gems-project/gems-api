from __future__ import annotations

import os
import time
from typing import Protocol

import requests


class UserLike(Protocol):
    email: str
    email_verified: bool


_CACHE_TTL_SECONDS = 30
_API_AUTHZ_CACHE: dict[str, tuple[float, bool]] = {}
_API_ACCESS_LIST_CACHE: tuple[float, set[str]] | None = None


def _allowed_users() -> set[str]:
    raw = os.environ.get("ALLOWED_USERS", "") or ""
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def has_dashboard_access(user: UserLike | None) -> bool:
    if user is None or not user.email_verified:
        return False
    return user.email.strip().lower() in _allowed_users()


def api_check(user: UserLike, bearer_token: str | None = None) -> bool:
    api_base_url = os.environ.get("GEMS_API_BASE_URL", "").strip().rstrip("/")
    if not api_base_url:
        return False

    email = user.email.strip().lower()
    cache_key = email
    cached = _API_AUTHZ_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    allowed = _api_allowed_users_from_service(api_base_url)
    if allowed is not None:
        result = email in allowed
        _API_AUTHZ_CACHE[cache_key] = (now, result)
        return result

    token = (bearer_token or "").strip()
    if not token:
        _API_AUTHZ_CACHE[cache_key] = (now, False)
        return False

    try:
        response = requests.get(
            f"{api_base_url}/authz/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if response.status_code == 404:
            allowed = False
        else:
            response.raise_for_status()
            allowed = bool(response.json().get("api_access", False))
    except Exception:
        allowed = False

    _API_AUTHZ_CACHE[cache_key] = (now, allowed)
    return allowed


def _api_allowed_users_from_service(api_base_url: str) -> set[str] | None:
    global _API_ACCESS_LIST_CACHE
    now = time.time()
    if _API_ACCESS_LIST_CACHE and now - _API_ACCESS_LIST_CACHE[0] < _CACHE_TTL_SECONDS:
        return _API_ACCESS_LIST_CACHE[1]

    shared_secret = os.environ.get("DASHBOARD_API_AUTHZ_SECRET", "").strip()
    if not shared_secret:
        return None

    try:
        response = requests.get(
            f"{api_base_url}/authz/allowed-users",
            headers={"X-Dashboard-Authz-Secret": shared_secret},
            timeout=10,
        )
        response.raise_for_status()
        allowed = {
            str(email).strip().lower()
            for email in response.json().get("allowed_users", [])
            if str(email).strip()
        }
        _API_ACCESS_LIST_CACHE = (now, allowed)
        return allowed
    except Exception:
        return None


def has_api_access(user: UserLike | None, bearer_token: str | None = None) -> bool:
    if not has_dashboard_access(user):
        return False
    return api_check(user, bearer_token)