"""Per-user download watermarks, stored in Azure Table Storage.

Schema:
    PartitionKey = user UPN (sanitized)
    RowKey       = f"{table}:{watermark_column}"
    lastValue    = string (timestamp ISO-8601 or integer, depending on column type)
    lastValueType = "timestamp" | "bigint" | "string"
    updatedAt    = ISO-8601 timestamp

If `AZURE_TABLES_CONNECTION_STRING` is not set, the store silently becomes a no-op
(all get() return None, set() does nothing). The Download page handles that by
only offering full downloads.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

_PK_UNSAFE = re.compile(r"[^A-Za-z0-9_\-.@]")


def _sanitize_key(raw: str) -> str:
    return _PK_UNSAFE.sub("_", raw or "anonymous")[:255]


class WatermarkStore:
    def __init__(self) -> None:
        conn = os.environ.get("AZURE_TABLES_CONNECTION_STRING", "").strip()
        self.table_name = os.environ.get(
            "AZURE_TABLES_NAME", "gemsDownloadWatermarks"
        ).strip()
        self.enabled = bool(conn)
        self._client: Any | None = None
        if self.enabled:
            try:
                from azure.data.tables import TableServiceClient

                svc = TableServiceClient.from_connection_string(conn)
                try:
                    svc.create_table_if_not_exists(self.table_name)
                except Exception:
                    pass
                self._client = svc.get_table_client(self.table_name)
            except Exception:
                self.enabled = False
                self._client = None

    def get(self, user: str, table: str, column: str) -> dict | None:
        if not self.enabled or self._client is None:
            return None
        try:
            entity = self._client.get_entity(
                partition_key=_sanitize_key(user),
                row_key=_sanitize_key(f"{table}:{column}"),
            )
            return {
                "lastValue": entity.get("lastValue"),
                "lastValueType": entity.get("lastValueType"),
                "updatedAt": entity.get("updatedAt"),
            }
        except Exception:
            return None

    def set(
        self,
        user: str,
        table: str,
        column: str,
        last_value: str,
        value_type: str = "string",
    ) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            self._client.upsert_entity(
                {
                    "PartitionKey": _sanitize_key(user),
                    "RowKey": _sanitize_key(f"{table}:{column}"),
                    "lastValue": str(last_value),
                    "lastValueType": value_type,
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception:
            pass
