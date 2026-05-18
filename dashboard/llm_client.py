from __future__ import annotations

import os
from functools import lru_cache

from openai import OpenAI

DEFAULT_DATABRICKS_LLM_ENDPOINT = "databricks-claude-haiku-4-5"
DEFAULT_OPENAI_CHAT_MODEL = "gpt-4o"


def _databricks_host() -> str:
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    if not host:
        raise RuntimeError("DATABRICKS_HOST is not set")
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    return host.rstrip("/")


@lru_cache(maxsize=1)
def get_llm_client() -> OpenAI:
    if os.environ.get("DATABRICKS_LLM_ENDPOINT", "").strip():
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


def check_llm_endpoint() -> None:
    try:
        get_llm_client().chat.completions.create(
            model=get_llm_model(),
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=4,
        )
        print(f"[LLM] {get_llm_model()} reachable.")
    except Exception as e:
        print(f"[LLM] WARNING: endpoint check failed: {e}")