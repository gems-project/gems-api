"""Read the signed-in user from Azure App Service Easy Auth headers and enforce
an in-app allowlist.

Easy Auth injects ``X-MS-CLIENT-PRINCIPAL-NAME`` (the UPN, usually an email) on
every request after a successful Entra ID sign-in. When running locally we fall
back to ``LOCAL_DEV_USER`` so pages don't blow up during development.

Access control is driven by two environment variables (either or both may be
set; the check passes if the user matches **any** of them):

* ``ALLOWED_USERS`` — comma-separated list of exact emails/UPNs.
* ``ALLOWED_DOMAINS`` — comma-separated list of email domains (e.g.
  ``cornell.edu, nmbu.no``).

If **neither** variable is set, access is open (behaviour matches the previous
code). Call :func:`require_authorized_user` from the top of every page.
"""

from __future__ import annotations

import os

import streamlit as st


def get_current_user() -> str:
    """Return the signed-in user's UPN, or a local-dev fallback."""
    headers: dict[str, str] = {}
    try:
        ctx_headers = getattr(st, "context", None)
        if ctx_headers is not None and hasattr(ctx_headers, "headers"):
            raw = dict(ctx_headers.headers or {})
            headers = {k.lower(): v for k, v in raw.items()}
    except Exception:
        headers = {}

    for key in ("x-ms-client-principal-name", "x-ms-client-principal-id"):
        if key in headers and headers[key]:
            return headers[key]

    return os.environ.get("LOCAL_DEV_USER", "local-dev@example.com")


def _split_env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "") or ""
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def is_authorized(user: str | None) -> bool:
    """Return True if *user* passes the in-app allowlist.

    Policy: user passes if listed in ``ALLOWED_USERS`` OR their email domain
    is in ``ALLOWED_DOMAINS``. If neither env var is set, access is open.
    """
    allowed_users = _split_env_list("ALLOWED_USERS")
    allowed_domains = _split_env_list("ALLOWED_DOMAINS")

    if not allowed_users and not allowed_domains:
        return True

    if not user:
        return False

    u = user.strip().lower()
    if u in allowed_users:
        return True

    domain = u.rsplit("@", 1)[-1] if "@" in u else ""
    return bool(domain) and domain in allowed_domains


def require_authorized_user() -> str:
    """Gate a page: return the user, or render a 'not authorized' block and stop.

    Place this at the top of each page before doing anything expensive (data
    loads, API calls). When blocked it calls ``st.stop()`` so no further UI
    renders on the page.
    """
    user = get_current_user()
    if is_authorized(user):
        return user

    st.error("You are not authorized to access the data on this page.")
    st.write(
        f"Signed in as **{user}**. The public landing page is open to anyone, "
        "but the Explore, Modeling, Chat, and Download pages are restricted. "
        "Ask the dashboard administrator to add your email or domain to the "
        "allowlist if you need data access."
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
    return user
