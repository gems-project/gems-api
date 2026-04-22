"""Read the signed-in user from Azure App Service Easy Auth headers.

Easy Auth injects `X-MS-CLIENT-PRINCIPAL-NAME` (and a few siblings) on every request
after a successful Entra ID sign-in. When running locally we fall back to
`LOCAL_DEV_USER` so pages don't blow up during development.
"""

from __future__ import annotations

import os

import streamlit as st


def get_current_user() -> str:
    """Return the signed-in user's UPN, or a local-dev fallback."""
    headers: dict[str, str] = {}
    try:
        # Streamlit >= 1.37 exposes request headers via st.context.
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
