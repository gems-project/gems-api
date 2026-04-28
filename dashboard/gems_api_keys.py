from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any

_OWNER_UNSAFE = re.compile(r"[^A-Za-z0-9_\-.@]")
_KEY_PREFIX = "gems_live_"
_PARTITION = "api_key"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner_key(owner: str) -> str:
    return _OWNER_UNSAFE.sub("_", (owner or "anonymous").strip().lower())[:255]


def _hash_key(raw_key: str, pepper: str) -> str:
    return hmac.new(
        pepper.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


class ApiKeyStore:
    def __init__(self) -> None:
        conn = os.environ.get("AZURE_TABLES_CONNECTION_STRING", "").strip()
        self.table_name = os.environ.get("AZURE_API_KEYS_TABLE", "gemsApiKeys").strip()
        self.pepper = os.environ.get("API_KEY_PEPPER", "").strip()
        self.enabled = bool(conn and self.pepper)
        self._client: Any | None = None
        self.error: str | None = None
        if self.enabled:
            try:
                from azure.data.tables import TableServiceClient

                svc = TableServiceClient.from_connection_string(conn)
                svc.create_table_if_not_exists(self.table_name)
                self._client = svc.get_table_client(self.table_name)
            except Exception as exc:
                self.enabled = False
                self.error = str(exc)
                self._client = None

    def create_key(self, owner: str, name: str) -> tuple[str, dict]:
        if not self.enabled or self._client is None:
            raise RuntimeError("API key storage is not configured")

        raw_key = _KEY_PREFIX + secrets.token_urlsafe(32)
        key_hash = _hash_key(raw_key, self.pepper)
        created_at = _utc_now()
        entity = {
            "PartitionKey": _PARTITION,
            "RowKey": key_hash,
            "owner": owner.strip().lower(),
            "ownerKey": _owner_key(owner),
            "name": (name or "API key").strip()[:120],
            "keyPrefix": raw_key[:18] + "...",
            "createdAt": created_at,
            "lastUsedAt": "",
            "revokedAt": "",
        }
        self._client.upsert_entity(entity)
        return raw_key, self._public_entity(entity)

    def list_keys(self, owner: str) -> list[dict]:
        if not self.enabled or self._client is None:
            return []
        owner_key = _owner_key(owner)
        query = f"PartitionKey eq '{_PARTITION}' and ownerKey eq '{owner_key}'"
        rows = list(self._client.query_entities(query_filter=query))
        rows.sort(key=lambda item: str(item.get("createdAt", "")), reverse=True)
        return [self._public_entity(row) for row in rows]

    def revoke_key(self, key_hash: str, owner: str) -> bool:
        if not self.enabled or self._client is None:
            return False
        try:
            entity = self._client.get_entity(_PARTITION, key_hash)
            if entity.get("ownerKey") != _owner_key(owner):
                return False
            entity["revokedAt"] = _utc_now()
            self._client.upsert_entity(entity)
            return True
        except Exception:
            return False

    def _public_entity(self, entity: dict) -> dict:
        revoked_at = str(entity.get("revokedAt", "") or "")
        return {
            "id": str(entity.get("RowKey", "")),
            "name": str(entity.get("name", "")),
            "prefix": str(entity.get("keyPrefix", "")),
            "created_at": str(entity.get("createdAt", "")),
            "last_used_at": str(entity.get("lastUsedAt", "") or "Never"),
            "revoked_at": revoked_at,
            "status": "Revoked" if revoked_at else "Active",
        }