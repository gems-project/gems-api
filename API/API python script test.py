import json
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

API_KEY = os.environ["GEMS_API_KEY"]
BASE_URL = "https://gems-api.bovi-analytics.org"
DATA_DIR = Path("gems_data")
DATA_DIR.mkdir(exist_ok=True)

headers = {"X-API-Key": API_KEY}

def read_metadata(table):
    path = DATA_DIR / f"{table}.metadata.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())

def write_metadata(table, metadata):
    path = DATA_DIR / f"{table}.metadata.json"
    path.write_text(json.dumps(metadata, indent=2, default=str))

def get_json(path):
    response = requests.get(f"{BASE_URL}{path}", headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()

tables = get_json("/tables")["tables"]
updated = 0
skipped = 0

print("Checking GEMS tables...")

for table in tables:
    remote = get_json(f"/version/{table}")
    local = read_metadata(table)
    remote_version = remote["version"]
    local_version = None if local is None else local.get("version")

    if local_version == remote_version:
        print(f"{table} is already up to date. version={remote_version}")
        skipped += 1
        continue

    if local_version is None:
        print(f"{table} has no local copy. Downloading version {remote_version}...")
    else:
        print(
            f"{table} has a newer version. "
            f"local={local_version} remote={remote_version}. Downloading..."
        )

    response = requests.get(
        f"{BASE_URL}/export/{table}.csv",
        headers=headers,
        timeout=600,
    )
    response.raise_for_status()

    csv_path = DATA_DIR / f"{table}.csv"
    csv_path.write_bytes(response.content)
    write_metadata(table, remote)
    print(f"Saved {csv_path}")
    updated += 1

print(f"Done. Updated {updated} table(s); skipped {skipped} table(s).")