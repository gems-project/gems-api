"""Build site_geocache.json from goldcontributor.Affiliation (not experimental location)."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from geopy.geocoders import Nominatim

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from gems_geography import geocode_query, humanize_affiliation  # noqa: E402


def _connect():
    from databricks import sql as dsql

    host = os.environ["DATABRICKS_HOST"].strip().removeprefix("https://").rstrip("/")
    return dsql.connect(
        server_hostname=host,
        http_path=os.environ["DATABRICKS_HTTP_PATH"].strip(),
        access_token=os.environ["DATABRICKS_TOKEN"].strip(),
    )


def _fetch_affiliations() -> list[str]:
    catalog = os.environ.get("GEMS_CATALOG", "gems_catalog")
    schema = os.environ.get("GEMS_SCHEMA", "gold_v1")
    sql = f"""
    SELECT DISTINCT `Affiliation`
    FROM `{catalog}`.`{schema}`.`goldcontributor`
    WHERE `Affiliation` IS NOT NULL
    AND TRIM(CAST(`Affiliation` AS STRING)) <> ''
    ORDER BY `Affiliation`
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [str(row[0]).strip() for row in cur.fetchall() if str(row[0] or "").strip()]


def main() -> int:
    out = ROOT / "resources" / "site_geocache.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    affiliations = _fetch_affiliations()
    print(f"Distinct Affiliation count: {len(affiliations)}")
    print(f"Sample: {[humanize_affiliation(a) for a in affiliations[:10]]}")

    geolocator = Nominatim(user_agent="gems-dashboard")
    cache = dict(existing)
    failed: list[str] = []

    for idx, raw in enumerate(affiliations, start=1):
        if raw in cache:
            continue
        query = geocode_query(raw)
        result = None
        for attempt in range(2):
            try:
                result = geolocator.geocode(query, timeout=8, addressdetails=True)
                if result:
                    break
            except Exception:
                result = None
            if attempt == 0:
                time.sleep(1.1)
        if result:
            cache[raw] = {
                "lat": float(result.latitude),
                "lon": float(result.longitude),
                "country": (result.raw.get("address") or {}).get("country"),
                "label": humanize_affiliation(raw),
            }
            print(f"[{idx}/{len(affiliations)}] geocoded: {humanize_affiliation(raw)}")
        else:
            failed.append(raw)
            print(f"[{idx}/{len(affiliations)}] failed: {humanize_affiliation(raw)}")
        time.sleep(1.1)

    out.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {out}")
    print(f"Failed to geocode ({len(failed)}): {[humanize_affiliation(f) for f in failed]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
