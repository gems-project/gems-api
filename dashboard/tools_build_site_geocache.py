from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from geopy.geocoders import Nominatim

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True)


def _connect():
    from databricks import sql as dsql

    host = os.environ["DATABRICKS_HOST"].strip().removeprefix("https://").rstrip("/")
    return dsql.connect(
        server_hostname=host,
        http_path=os.environ["DATABRICKS_HTTP_PATH"].strip(),
        access_token=os.environ["DATABRICKS_TOKEN"].strip(),
    )


def _normalize_site_for_geocode(location: str) -> str:
    text = (location or "").strip()
    if text.lower() == "cornell":
        return "Cornell University, Ithaca, New York, United States"
    return text


def _fetch_sites() -> list[str]:
    catalog = os.environ.get("GEMS_CATALOG", "gems_catalog")
    schema = os.environ.get("GEMS_SCHEMA", "gold_v1")
    sql = """
    SELECT DISTINCT `ExperimentalLocation`
    FROM `{catalog}`.`{schema}`.`bronzeexperimentaldesign`
    WHERE `ExperimentalLocation` IS NOT NULL
    AND TRIM(CAST(`ExperimentalLocation` AS STRING)) <> ''
    ORDER BY `ExperimentalLocation`
    """.format(catalog=catalog, schema=schema)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [str(row[0]).strip() for row in cur.fetchall() if str(row[0] or "").strip()]


def main() -> int:
    out = ROOT / "resources" / "site_geocache.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    sites = _fetch_sites()
    print(f"Distinct ExperimentalLocation count: {len(sites)}")
    print(f"Sample: {sites[:10]}")

    geolocator = Nominatim(user_agent="gems-dashboard")
    cache = dict(existing)
    failed: list[str] = []

    for idx, site in enumerate(sites, start=1):
        if site in cache:
            continue
        result = None
        for attempt in range(2):
            try:
                result = geolocator.geocode(_normalize_site_for_geocode(site), timeout=8, addressdetails=True)
                if result:
                    break
            except Exception:
                result = None
            if attempt == 0:
                time.sleep(1.1)
        if result:
            cache[site] = {
                "lat": float(result.latitude),
                "lon": float(result.longitude),
                "country": (result.raw.get("address") or {}).get("country"),
            }
            print(f"[{idx}/{len(sites)}] geocoded: {site}")
        else:
            failed.append(site)
            print(f"[{idx}/{len(sites)}] failed: {site}")
        time.sleep(1.1)

    out.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out}")
    print(f"Failed to geocode ({len(failed)}): {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())