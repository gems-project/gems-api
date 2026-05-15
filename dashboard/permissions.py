from __future__ import annotations

import os
import time
from typing import Protocol

import requests


class UserLike(Protocol):
    email: str
    email_verified: bool


_CACHE_TTL_SECONDS = 300
_API_AUTHZ_CACHE: dict[str, tuple[float, bool]] = {}


def _allowed_users() -> set[str]:
    raw = os.environ.get("ALLOWED_USERS", "") or ""
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def has_dashboard_access(user: UserLike | None) -> bool:
    if user is None or not user.email_verified:
        return False
    return user.email.strip().lower() in _allowed_users()


def api_check(user: UserLike, bearer_token: str | None = None) -> bool:
    api_base_url = os.environ.get("GEMS_API_BASE_URL", "").strip().rstrip("/")
    token = (bearer_token or "").strip()
    if not api_base_url or not token:
        return False

    cache_key = f"{user.email.strip().lower()}:{token[-24:]}"
    cached = _API_AUTHZ_CACHE.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

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


def has_api_access(user: UserLike | None, bearer_token: str | None = None) -> bool:
    if not has_dashboard_access(user):
        return False
    return api_check(user, bearer_token)