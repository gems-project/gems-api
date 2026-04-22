"""Diagnose what python-dotenv reads from dashboard/.env.

Run from repo root (with the dashboard venv active):

    dashboard\\.venv\\Scripts\\activate
    python tools\\debug_env.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / "dashboard" / ".env"

print(f".env path: {ENV_PATH}")
print(f"exists:    {ENV_PATH.exists()}")
print(f"size:      {ENV_PATH.stat().st_size if ENV_PATH.exists() else 'n/a'} bytes")
print()

with open(ENV_PATH, "rb") as f:
    head = f.read(4)
if head.startswith(b"\xef\xbb\xbf"):
    print("WARNING: file starts with a UTF-8 BOM — this can confuse dotenv on the first line.")
else:
    print(f"first 4 bytes (hex): {head.hex()}  (no BOM)")
print()

raw = dotenv_values(ENV_PATH)
print("dotenv_values parsed keys:")
for k, v in raw.items():
    if v is None:
        display = "<None>"
    elif k in ("DATABRICKS_TOKEN", "OPENAI_API_KEY"):
        display = f"{v[:6]}...{v[-4:]} (len={len(v)})"
    else:
        display = repr(v)
    print(f"  {k} = {display}")
print()

load_dotenv(ENV_PATH, override=True)
print("After load_dotenv(override=True), os.environ says:")
for k in (
    "DATABRICKS_HOST",
    "DATABRICKS_HTTP_PATH",
    "DATABRICKS_TOKEN",
    "GEMS_CATALOG",
    "GEMS_SCHEMA",
    "ALLOWED_TABLES",
):
    v = os.environ.get(k)
    if v is None:
        display = "<NOT SET>"
    elif k == "DATABRICKS_TOKEN":
        display = f"{v[:6]}...{v[-4:]} (len={len(v)})"
    else:
        display = repr(v)
    print(f"  {k} = {display}")

sys.exit(0)
