# -*- coding: utf-8 -*-
"""Read Easy Auth identity headers and enforce the dashboard data allowlist."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

import requests
import streamlit as st

from permissions import has_dashboard_access


@dataclass(frozen=True)
class CurrentUser:
    email: str
    email_verified: bool
    auth0_user_id: str
    bearer_token: str = ""


def _headers() -> dict[str, str]:
    try:
        ctx_headers = getattr(st, "context", None)
        if ctx_headers is not None and hasattr(ctx_headers, "headers"):
            raw = dict(ctx_headers.headers or {})
            return {k.lower(): v for k, v in raw.items()}
    except Exception:
        pass
    return {}


def _decode_client_principal(headers: dict[str, str]) -> dict[str, Any]:
    raw = headers.get("x-ms-client-principal", "")
    if not raw:
        return {}
    try:
        padded = raw + "=" * (-len(raw) % 4)
        return json.loads(base64.b64decode(padded).decode("utf-8"))
    except Exception:
        return {}


def _claims_from_principal(principal: dict[str, Any]) -> dict[str, str]:
    claims: dict[str, str] = {}
    for claim in principal.get("claims", []) or []:
        typ = str(claim.get("typ", "") or "")
        val = str(claim.get("val", "") or "")
        if typ and val:
            claims[typ] = val
    return claims


def _truthy(raw: str | bool | None) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"true", "1", "yes"}


def get_current_user_info() -> CurrentUser:
    """Return signed-in identity details from Easy Auth/Auth0 headers."""
    headers = _headers()
    principal = _decode_client_principal(headers)
    claims = _claims_from_principal(principal)

    email = (
        claims.get("email")
        or claims.get("emails")
        or headers.get("x-ms-client-principal-name")
        or os.environ.get("LOCAL_DEV_USER", "local-dev@example.com")
    )
    auth0_user_id = (
        claims.get("sub")
        or headers.get("x-ms-client-principal-id")
        or os.environ.get("LOCAL_DEV_AUTH0_USER_ID", "")
    )
    email_verified = _truthy(
        claims.get("email_verified") or os.environ.get("LOCAL_DEV_EMAIL_VERIFIED", "true")
    )
    return CurrentUser(
        email=email.strip().lower(),
        email_verified=email_verified,
        auth0_user_id=auth0_user_id,
        bearer_token=(
            headers.get("x-ms-token-auth0-access-token")
            or headers.get("x-ms-token-auth0-id-token")
            or headers.get("authorization", "").removeprefix("Bearer ").strip()
        ),
    )


def get_current_user() -> str:
    return get_current_user_info().email


def _dashboard_allowed_user(email: str | None) -> bool:
    if not email:
        return False
    from permissions import _allowed_users

    return email.strip().lower() in _allowed_users()


def is_authorized(user: str | None) -> bool:
    info = get_current_user_info()
    return has_dashboard_access(info)


def _auth0_management_token() -> str:
    token = os.environ.get("AUTH0_MANAGEMENT_API_TOKEN", "").strip()
    if token:
        return token

    domain = os.environ.get("AUTH0_DOMAIN", "").strip().rstrip("/")
    client_id = os.environ.get("AUTH0_MANAGEMENT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AUTH0_MANAGEMENT_CLIENT_SECRET", "").strip()
    if not (domain and client_id and client_secret):
        return ""

    response = requests.post(
        f"https://{domain}/oauth/token",
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "audience": f"https://{domain}/api/v2/",
            "grant_type": "client_credentials",
        },
        timeout=20,
    )
    response.raise_for_status()
    return str(response.json().get("access_token", ""))


def resend_verification_email(user_id: str) -> tuple[bool, str]:
    domain = os.environ.get("AUTH0_DOMAIN", "").strip().rstrip("/")
    if not domain:
        return False, "Set AUTH0_DOMAIN to enable verification email resend."

    token = _auth0_management_token()
    if not token:
        return (
            False,
            "Verification email resend is not configured for this dashboard. "
            "Please check your Auth0 verification email (including spam) or ask the "
            "dashboard administrator to send a verification email from Auth0.",
        )

    response = requests.post(
        f"https://{domain}/api/v2/jobs/verification-email",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"user_id": user_id},
        timeout=20,
    )
    if response.status_code >= 400:
        return False, f"Auth0 resend failed: {response.status_code} {response.text}"
    return True, "Verification email sent. Check your inbox, then refresh this page."


def render_email_verification_banner(info: CurrentUser | None = None) -> None:
    info = info or get_current_user_info()
    st.warning(
        "Please verify your email — check your inbox for a verification link from Auth0. "
        "Refresh after clicking."
    )
    if st.button("Resend verification email"):
        ok, message = resend_verification_email(info.auth0_user_id)
        if ok:
            st.success(message)
        else:
            st.info(message)


def require_authorized_user() -> str:
    info = get_current_user_info()
    if has_dashboard_access(info):
        return info.email

    if _dashboard_allowed_user(info.email) and not info.email_verified:
        render_email_verification_banner(info)
    else:
        st.error("You are not authorized to access the data on this page.")

    st.write(
        f"Signed in as **{info.email}**. The public landing page is open to anyone, "
        "but the Explore, Modeling, Chat, and API Access pages are restricted. "
        "Ask the dashboard administrator to add your email to the allowlist if you need data access."
    )
    st.markdown(
        '<a href="/" style="display:inline-block;margin-top:0.5rem;margin-right:0.5rem;'
        "padding:0.4rem 0.9rem;background:#1f6b42;color:#fff;border-radius:8px;"
        'text-decoration:none;font-weight:600;">Back to home</a>'
        '<a href="/.auth/logout" style="display:inline-block;margin-top:0.5rem;'
        "padding:0.4rem 0.9rem;background:#6b7280;color:#fff;border-radius:8px;"
        'text-decoration:none;font-weight:600;">Sign out</a>',
        unsafe_allow_html=True,
    )
    st.stop()
    return info.email