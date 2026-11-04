"""Log of data downloads from the dashboard: who downloaded what, how many rows, and the cohort recipe.

Rows go to Azure Table Storage (AZURE_TABLES_CONNECTION_STRING, the account that holds the API keys;
table AZURE_DOWNLOADS_TABLE, default gemsDownloads), one partition per month. Every download is also
printed to stdout, so it shows in the App Service log stream even when the table is not configured.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

_CLIENT: Any | None = None
_CLIENT_TRIED = False


def _table_client() -> Any | None:
    """The table client, made once per process (plain global: downloads are logged off the script thread)."""
    global _CLIENT, _CLIENT_TRIED
    if _CLIENT_TRIED:
        return _CLIENT
    _CLIENT_TRIED = True
    conn = os.environ.get("AZURE_TABLES_CONNECTION_STRING", "").strip()
    if not conn:
        return None
    try:
        from azure.data.tables import TableServiceClient

        svc = TableServiceClient.from_connection_string(conn)
        name = os.environ.get("AZURE_DOWNLOADS_TABLE", "gemsDownloads").strip()
        svc.create_table_if_not_exists(name)
        _CLIENT = svc.get_table_client(name)
    except Exception as exc:  # noqa: BLE001
        print(f"[downloads] table storage unavailable: {exc}", flush=True)
    return _CLIENT


def log_download(email: str, page: str, item: str, rows: int, studies: list[str], recipe: dict) -> None:
    """Record one download. Never raises: a logging failure must not block the user."""
    now = datetime.now(timezone.utc)
    entity = {
        "PartitionKey": now.strftime("%Y-%m"),
        "RowKey": f"{now.strftime('%Y%m%dT%H%M%S%f')}_{uuid.uuid4().hex[:8]}",
        "email": (email or "").strip().lower(),
        "page": page,
        "item": item,
        "rows": int(rows),
        "studies": ",".join(studies)[:30000],
        "recipe": json.dumps(recipe, default=str)[:30000],
    }
    print(f"[downloads] {entity['email']} {page}/{item} rows={rows} studies={len(studies)}", flush=True)
    client = _table_client()
    if client is None:
        return
    try:
        client.create_entity(entity)
    except Exception as exc:  # noqa: BLE001
        print(f"[downloads] could not write the log row: {exc}", flush=True)
