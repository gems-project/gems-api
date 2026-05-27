from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from openai import OpenAI

DEFAULT_DATABRICKS_LLM_ENDPOINT = "databricks-claude-opus-4-7"
DEFAULT_DATABRICKS_CHAT_LLM_ENDPOINT = "databricks-claude-haiku-4-5"
DEFAULT_OPENAI_CHAT_MODEL = "gpt-4o"


def _databricks_host() -> str:
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    if not host:
        raise RuntimeError("DATABRICKS_HOST is not set")
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    return host.rstrip("/")


def _is_databricks_endpoint() -> bool:
    return bool(os.environ.get("DATABRICKS_LLM_ENDPOINT", "").strip())


@lru_cache(maxsize=1)
def get_llm_client() -> OpenAI:
    if _is_databricks_endpoint():
        token = os.environ.get("DATABRICKS_TOKEN", "").strip()
        if not token:
            raise RuntimeError("DATABRICKS_TOKEN is not set")
        return OpenAI(
            api_key=token,
            base_url=f"{_databricks_host()}/serving-endpoints",
        )

    legacy_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if legacy_key:
        print("[LLM] DEPRECATED: using OPENAI_API_KEY fallback. Set DATABRICKS_LLM_ENDPOINT to use Databricks.")
        return OpenAI(api_key=legacy_key)

    raise RuntimeError(
        "No LLM is configured. Set DATABRICKS_LLM_ENDPOINT with DATABRICKS_HOST "
        "and DATABRICKS_TOKEN, or set OPENAI_API_KEY for local legacy fallback."
    )


def get_llm_model() -> str:
    endpoint = os.environ.get("DATABRICKS_LLM_ENDPOINT", "").strip()
    if endpoint:
        return endpoint
    return os.environ.get("OPENAI_CHAT_MODEL", DEFAULT_OPENAI_CHAT_MODEL).strip() or DEFAULT_OPENAI_CHAT_MODEL


def get_chat_llm_model() -> str:
    """Chat model — defaults to ``DATABRICKS_LLM_ENDPOINT`` (e.g. Opus 4.7).

    Set ``DATABRICKS_CHAT_LLM_ENDPOINT`` only if you want a *different* model for
    Chat (e.g. Haiku for speed). More capable models are usually slower, not faster.
    """
    chat = os.environ.get("DATABRICKS_CHAT_LLM_ENDPOINT", "").strip()
    if chat:
        return chat
    return get_llm_model()


def chat_completion(**kwargs: Any):
    """Databricks Claude serving endpoints reject the temperature parameter."""
    if _is_databricks_endpoint():
        kwargs.pop("temperature", None)
    return get_llm_client().chat.completions.create(**kwargs)


def check_llm_endpoint() -> None:
    """Optional health ping. Not run on Home page load (see app.py).

    Set ``GEMS_CHECK_LLM_ON_STARTUP=1`` to restore a ping when ``app`` imports
    this module (local debugging only).
    """
    try:
        chat_completion(
            model=get_llm_model(),
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=4,
        )
        print(f"[LLM] {get_llm_model()} reachable.")
    except Exception as e:
        print(f"[LLM] WARNING: endpoint check failed: {e}")


def maybe_check_llm_on_startup() -> None:
    """Run ``check_llm_endpoint`` only when explicitly enabled via env."""
    if os.environ.get("GEMS_CHECK_LLM_ON_STARTUP", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        check_llm_endpoint()
