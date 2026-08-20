# -*- coding: utf-8 -*-
"""Read Easy Auth identity headers and enforce the dashboard data allowlist."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests
import streamlit as st

from permissions import has_dashboard_access

AUTH0_LOGIN_PATH = "/.auth/login/auth0"
AUTH_LOGOUT_PATH = "/.auth/logout"


@dataclass(frozen=True)
class CurrentUser:
    email: str
    email_verified: bool
    auth0_user_id: str
    bearer_token: str = ""
    is_authenticated: bool = True


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


def login_url(redirect_path: str = "/") -> str:
    """Easy Auth + Auth0 login entrypoint (provider name must be ``auth0``)."""
    redirect = redirect_path or "/"
    return f"{AUTH0_LOGIN_PATH}?post_login_redirect_uri={quote(redirect, safe='')}"


def logout_url() -> str:
    # Send users back to Home after Easy Auth logout (avoids a blank /.auth/logout page).
    return (
        f"{AUTH_LOGOUT_PATH}?post_logout_redirect_uri="
        f"{quote('https://gems.bovi-analytics.org/', safe='')}"
    )

def sign_in_button_html(label: str = "Sign in", redirect_path: str = "/") -> str:
    return (
        f'<a href="{login_url(redirect_path)}" style="display:inline-block;margin:0.25rem 0 0.75rem 0;'
        "padding:0.38rem 0.85rem;background:#1f6b42;color:#fff;border-radius:8px;"
        f'text-decoration:none;font-weight:600;">{label}</a>'
    )


def sign_out_button_html() -> str:
    return (
        f'<a href="{logout_url()}" style="display:inline-block;margin:0.25rem 0 0.75rem 0;'
        "padding:0.38rem 0.85rem;background:#6b7280;color:#fff;border-radius:8px;"
        'text-decoration:none;font-weight:600;">Sign out</a>'
    )


def get_current_user_info() -> CurrentUser:
    """Return Easy Auth/Auth0 identity, or an anonymous visitor when not signed in.

    Local Streamlit (no Easy Auth headers): set ``LOCAL_DEV_USER`` to simulate a
    signed-in account. If unset, the app behaves as a public visitor.
    """
    headers = _headers()
    principal = _decode_client_principal(headers)
    claims = _claims_from_principal(principal)

    email_from_auth = (
        claims.get("email")
        or claims.get("emails")
        or headers.get("x-ms-client-principal-name")
        or ""
    ).strip()
    auth0_user_id = (
        claims.get("sub")
        or headers.get("x-ms-client-principal-id")
        or ""
    ).strip()
    bearer_token = (
        headers.get("x-ms-token-auth0-access-token")
        or headers.get("x-ms-token-auth0-id-token")
        or headers.get("authorization", "").removeprefix("Bearer ").strip()
    )

    if email_from_auth or auth0_user_id or principal:
        if "email_verified" in claims:
            email_verified = _truthy(claims.get("email_verified"))
        else:
            email_verified = _truthy(os.environ.get("LOCAL_DEV_EMAIL_VERIFIED", "true"))
        return CurrentUser(
            email=(email_from_auth or auth0_user_id).strip().lower(),
            email_verified=email_verified,
            auth0_user_id=auth0_user_id,
            bearer_token=bearer_token,
            is_authenticated=True,
        )

    local_user = os.environ.get("LOCAL_DEV_USER", "").strip()
    if local_user:
        return CurrentUser(
            email=local_user.lower(),
            email_verified=_truthy(os.environ.get("LOCAL_DEV_EMAIL_VERIFIED", "true")),
            auth0_user_id=os.environ.get("LOCAL_DEV_AUTH0_USER_ID", "").strip(),
            bearer_token=bearer_token,
            is_authenticated=True,
        )

    return CurrentUser(
        email="",
        email_verified=False,
        auth0_user_id="",
        bearer_token="",
        is_authenticated=False,
    )


def get_current_user() -> str:
    return get_current_user_info().email


def is_signed_in(user: CurrentUser | None = None) -> bool:
    info = user or get_current_user_info()
    return bool(info.is_authenticated)


def _dashboard_allowed_user(email: str | None) -> bool:
    if not email:
        return False
    from permissions import _allowed_users

    return email.strip().lower() in _allowed_users()


def is_authorized(_user: str | None = None) -> bool:
    return has_dashboard_access(get_current_user_info())


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
    if not user_id:
        return (
            False,
            "Email verification resend requires the Auth0 user id, but Azure did not pass it "
            "to the dashboard. Contact the administrator or check your spam folder for the "
            "original Auth0 signup email.",
        )

    domain = os.environ.get("AUTH0_DOMAIN", "").strip().rstrip("/")
    if not domain:
        return (
            False,
            "Email verification is not configured on this server. Contact the administrator "
            "or check your spam folder for the original Auth0 signup email.",
        )

    token = _auth0_management_token()
    if not token:
        return (
            False,
            "Email verification is not configured on this server. Contact the administrator "
            "or check your spam folder for the original Auth0 signup email.",
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
            st.warning(message)


def require_authorized_user() -> str:
    """Require Auth0 sign-in + allowlist (+ verified email) for data pages."""
    info = get_current_user_info()

    if not info.is_authenticated:
        st.error("Sign in is required to access this page.")
        st.info(
            "You may sign in with Auth0, but access to Explore, Modeling, Chat, and API Access "
            "still requires administrator approval and a verified email."
        )
        st.markdown(
            sign_in_button_html("Sign in")
            + '<a href="/" style="display:inline-block;margin:0.25rem 0 0.75rem 0.5rem;'
            "padding:0.38rem 0.85rem;background:#6b7280;color:#fff;border-radius:8px;"
            'text-decoration:none;font-weight:600;">Back to home</a>',
            unsafe_allow_html=True,
        )
        st.stop()
        return ""

    if has_dashboard_access(info):
        return info.email

    if _dashboard_allowed_user(info.email) and not info.email_verified:
        render_email_verification_banner(info)
    else:
        st.error("You are not authorized to access the data on this page.")

    st.write(
        f"Signed in as **{info.email}**. The public landing page is open to anyone, "
        "but Explore, Modeling, Chat, and API Access require administrator approval "
        "and a verified email. Ask the dashboard administrator to add your email to "
        "the allowlist if you need data access."
    )
    st.markdown(
        '<a href="/" style="display:inline-block;margin-top:0.5rem;margin-right:0.5rem;'
        "padding:0.4rem 0.9rem;background:#1f6b42;color:#fff;border-radius:8px;"
        'text-decoration:none;font-weight:600;">Back to home</a>'
        f'<a href="{logout_url()}" style="display:inline-block;margin-top:0.5rem;'
        "padding:0.4rem 0.9rem;background:#6b7280;color:#fff;border-radius:8px;"
        'text-decoration:none;font-weight:600;">Sign out</a>',
        unsafe_allow_html=True,
    )
    st.stop()
    return info.email
