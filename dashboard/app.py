# -*- coding: utf-8 -*-
"""GEMS Dashboard - landing page and navigation.

Pages are registered with ``st.navigation`` using root-level ``page_*.py``
scripts. (Azure Oryx often omits a ``pages/`` subfolder from the runtime
extract, which breaks Streamlit's automatic multipage discovery.)

The hero is rendered with ``st.markdown(unsafe_allow_html=True)`` because
``data:`` image URLs work reliably in the top-level document, while
``st.components.v1.html`` iframes often block them via CSP.

Other HTML blocks use ``textwrap.dedent`` before Markdown where needed.
"""

from __future__ import annotations

import html
import json
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

_DASHBOARD_ROOT = Path(__file__).resolve().parent
load_dotenv(_DASHBOARD_ROOT / ".env", override=True)
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from gems_auth import (  # noqa: E402
    get_current_user_info,
    is_authorized,
    is_signed_in,
    render_email_verification_banner,
    sign_in_button_html,
    sign_out_button_html,
)
from gems_logo_data import (  # noqa: E402
    GEMS_LOGO_PNG_B64,
    GLOBAL_METHANE_HUB_PNG_B64,
)
from gems_geography import (  # noqa: E402
    english_country,
    fallback_coordinates as _geo_fallback_coordinates,
    geocode_query as _geo_geocode_query,
    humanize_affiliation,
    institution_key,
    pinned_coordinates as _geo_pinned_coordinates,
)
from gems_ui import apply_theme, sidebar_user  # noqa: E402

st.set_page_config(
    page_title="GEMS Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="🌱",
)

apply_theme()


def _resolve_asset_path(filename: str) -> Path | None:
    """Return path to a file under dashboard/assets/, or None.

    Tries the script directory (local and Azure), process cwd, and the usual
    App Service wwwroot. On Linux, also matches a case-insensitive filename.
    """
    roots = (
        _DASHBOARD_ROOT / "assets",
        Path.cwd() / "assets",
        Path("/home/site/wwwroot") / "assets",
    )
    want = filename.lower()
    for root in roots:
        direct = root / filename
        if direct.is_file():
            return direct
        if not root.is_dir():
            continue
        for f in root.iterdir():
            if f.is_file() and f.name.lower() == want:
                return f
    return None


def _clean_b64(s: str) -> str:
    return "".join(s.split())


_HOME_CATALOG = os.environ.get("GEMS_CATALOG", "gems_catalog")
_HOME_SCHEMA = os.environ.get("GEMS_SCHEMA", "gold_v1")
_RESOURCES_DIR = _DASHBOARD_ROOT / "resources"
_SITE_GEOCACHE_PATH = _RESOURCES_DIR / "site_geocache.json"
_CONTACTS = [
    ("Puchun Niu", "pn287@cornell.edu"),
    ("Hanlu Zhang", "hz785@cornell.edu"),
    ("Miel Hostens", "miel.hostens@cornell.edu"),
]
# (snapshot key, gold table) for the "What's in the warehouse" list.
_WAREHOUSE_TABLES = [
    ("visits", "goldgreenfeedrawvisitationdata"),
    ("intake", "goldintakeperday"),
    ("milk", "goldmilk"),
    ("bodyweight", "goldbodyweight"),
    ("chamber", "goldrespirationchambermeasurement"),
]
_TYPE_COLORS = {"Dairy": "#1E88E5", "Beef": "#F4511E", "Mixed": "#8E24AA"}
_DEFAULT_SITE_ROWS = [
    {"location": "Cornell University, Ithaca, United States", "study_count": 0},
    {"location": "University of California, Davis, United States", "study_count": 0},
    {"location": "University of Guelph, Guelph, Canada", "study_count": 0},
    {"location": "ETH Zurich, Zurich, Switzerland", "study_count": 0},
    {"location": "University of New England, Armidale, Australia", "study_count": 0},
    {"location": "Agriculture and Agri-Food Canada, Ottawa, Canada", "study_count": 0},
]
_SITE_GEOCACHE: dict = {}


def _production_label(value) -> str | None:
    """Normalise free-text ProductionType ('dairy', 'Daity', ...) to Dairy/Beef."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text.startswith("dai"):
        return "Dairy"
    if text.startswith("beef"):
        return "Beef"
    return text.title()


def _home_connect():
    from databricks import sql as dsql

    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://") :]
    http_path = os.environ.get("DATABRICKS_HTTP_PATH", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if not (host and http_path and token):
        raise RuntimeError("Databricks connection settings are incomplete")
    return dsql.connect(server_hostname=host, http_path=http_path, access_token=token)


def _fq(table: str, schema: str | None = None) -> str:
    return f"`{_HOME_CATALOG}`.`{schema or _HOME_SCHEMA}`.`{table}`"


def _col(name: str) -> str:
    return f"`{name}`"


def _norm_col(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


class _HomeQueries:
    """Home-page queries sharing one Databricks cursor (one connection per load)."""

    def __init__(self, cur):
        self.cur = cur
        self._columns: dict[tuple[str, str | None], list[str]] = {}

    def resolve(self, table: str, *candidates: str, schema: str | None = None) -> str | None:
        key = (table, schema)
        if key not in self._columns:
            try:
                self.cur.execute(f"DESCRIBE TABLE {_fq(table, schema)}")
                names = [str(row[0] or "").strip() for row in self.cur.fetchall()]
                self._columns[key] = [n for n in names if n and not n.startswith("#")]
            except Exception:
                self._columns[key] = []
        by_norm = {_norm_col(col): col for col in self._columns[key]}
        for candidate in candidates:
            resolved = by_norm.get(_norm_col(candidate))
            if resolved:
                return resolved
        return None

    def rows(self, sql: str) -> list:
        self.cur.execute(sql)
        return self.cur.fetchall()

    def study_animal_counts(self) -> tuple[int | None, int | None]:
        table = "goldanimalcharacteristics"
        study_col = self.resolve(table, "studyId", "studyID", "study_id", "StudyId")
        animal_col = self.resolve(table, "AnimalIdentifier", "animalIdentifier", "animal_id")
        if not study_col:
            return None, None
        animal_expr = (
            f"COUNT(DISTINCT CONCAT({_col(study_col)}, '__', {_col(animal_col)}))"
            if animal_col
            else "NULL"
        )
        row = self.rows(
            f"SELECT COUNT(DISTINCT {_col(study_col)}), {animal_expr} FROM {_fq(table)}"
        )[0]
        return (int(row[0]) if row[0] else None, int(row[1]) if row[1] else None)

    def date_range(self) -> list[str] | None:
        table = "goldexperimentaldesign"
        date_col = self.resolve(table, "Date", "date", "measurementDate", "MeasurementDate")
        if not date_col:
            return None
        date_expr = _col(date_col)
        row = self.rows(
            "SELECT "
            f"MIN(CASE WHEN {date_expr} > DATE '1990-01-01' THEN {date_expr} END), "
            f"MAX(CASE WHEN {date_expr} <= current_date() THEN {date_expr} END) "
            f"FROM {_fq(table)}"
        )[0]
        if row[0] is None or row[1] is None:
            return None
        return [_json_date(row[0]), _json_date(row[1])]

    def site_rows(self) -> list[dict]:
        """Gold studies grouped by institution (distinct studyId per institution)."""
        table = "goldcontributor"
        study_col = self.resolve(table, "studyId", "studyID", "study_id", "StudyId")
        affiliation_col = self.resolve(table, "Affiliation", "affiliation")
        if not study_col or not affiliation_col:
            return []
        aff, sid = _col(affiliation_col), _col(study_col)
        rows = self.rows(
            f"SELECT DISTINCT {aff}, {sid} FROM {_fq(table)} "
            f"WHERE {aff} IS NOT NULL AND TRIM(CAST({aff} AS STRING)) <> '' "
            f"AND {sid} IS NOT NULL AND TRIM(CAST({sid} AS STRING)) <> ''"
        )
        study_sets: dict[str, set[str]] = {}
        labels: dict[str, str] = {}
        for raw_aff, raw_study in rows:
            label = humanize_affiliation(str(raw_aff).strip())
            key = institution_key(label)
            if not key:
                continue
            if len(label) > len(labels.get(key, "")):
                labels[key] = label
            study_sets.setdefault(key, set()).add(str(raw_study).strip())
        return [
            {
                "cache_key": key,
                "location": labels[key],
                "label": labels[key],
                "study_count": len(study_sets[key]),
                "study_ids": sorted(study_sets[key]),
            }
            for key in sorted(labels, key=lambda k: labels[k].lower())
        ]

    def study_details(self) -> dict[str, dict]:
        """Per gold study: production type, animal count, years, GreenFeed visits."""
        details: dict[str, dict] = {}

        def entry(study_id) -> dict:
            return details.setdefault(
                str(study_id).strip(),
                {"type": None, "animals": 0, "start": None, "end": None, "visits": 0},
            )

        type_votes: dict[str, dict[str, int]] = {}
        for sid, prod, n in self.rows(
            "SELECT studyId, ProductionType, COUNT(*) "
            f"FROM {_fq('goldanimalcharacteristics')} GROUP BY studyId, ProductionType"
        ):
            label = _production_label(prod)
            if label:
                votes = type_votes.setdefault(str(sid).strip(), {})
                votes[label] = votes.get(label, 0) + int(n)
        for sid, animals in self.rows(
            f"SELECT studyId, COUNT(DISTINCT AnimalIdentifier) "
            f"FROM {_fq('goldanimalcharacteristics')} GROUP BY studyId"
        ):
            entry(sid)["animals"] = int(animals or 0)
        for sid, votes in type_votes.items():
            entry(sid)["type"] = max(votes, key=votes.get)
        for sid, start, end in self.rows(
            "SELECT studyId, YEAR(MIN(Date)), YEAR(MAX(Date)) "
            f"FROM {_fq('goldexperimentaldesign')} "
            "WHERE Date > DATE '1990-01-01' AND Date <= current_date() GROUP BY studyId"
        ):
            item = entry(sid)
            item["start"], item["end"] = start, end
        for sid, visits in self.rows(
            f"SELECT studyId, COUNT(*) FROM {_fq('goldgreenfeedrawvisitationdata')} "
            "GROUP BY studyId"
        ):
            entry(sid)["visits"] = int(visits or 0)
        return details

    def warehouse_counts(self) -> dict[str, int]:
        """Row counts for the headline record types shown on the home page."""
        counts: dict[str, int] = {}
        for key, table in _WAREHOUSE_TABLES:
            try:
                counts[key] = int(self.rows(f"SELECT COUNT(*) FROM {_fq(table)}")[0][0])
            except Exception:
                continue
        return counts


@st.cache_data(ttl=3600, show_spinner=False)
def _home_snapshot_live() -> dict:
    """All live home-page figures, fetched over a single Databricks connection.

    Raises when Databricks is unreachable so that the failure is not cached.
    """
    snapshot: dict = {
        "studies": None,
        "animals": None,
        "date_range": None,
        "sites": [],
        "study_details": {},
        "warehouse": {},
    }
    with _home_connect() as conn, conn.cursor() as cur:
        q = _HomeQueries(cur)
        steps = (
            ("counts", q.study_animal_counts),
            ("date_range", q.date_range),
            ("sites", q.site_rows),
            ("study_details", q.study_details),
            ("warehouse", q.warehouse_counts),
        )
        for name, fetch in steps:
            try:
                value = fetch()
            except Exception:
                continue
            if name == "counts":
                snapshot["studies"], snapshot["animals"] = value
            else:
                snapshot[name] = value
    return snapshot


def _home_snapshot() -> dict:
    """Live snapshot, or the last one this session saw if Databricks is unavailable."""
    try:
        snapshot = _home_snapshot_live()
        st.session_state["home_snapshot"] = snapshot
        return snapshot
    except Exception:
        return st.session_state.get("home_snapshot") or {}


def _json_date(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _format_month(value: str) -> str:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%b %Y")
    except Exception:
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%b %Y")
        except Exception:
            return value[:7] if value else "Loading..."


def _count_label(value) -> str:
    return f"{value:,}" if value is not None else "Not available"


def _date_range_label(value: list[str] | None) -> str:
    if not value:
        return "No data"
    return f"{_format_month(value[0])} - {_format_month(value[1])}"


def _normalize_site_for_geocode(location: str) -> str:
    return _geo_geocode_query(location)


def _read_site_geocache() -> dict:
    try:
        if _SITE_GEOCACHE_PATH.exists():
            return json.loads(_SITE_GEOCACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_site_geocache(cache: dict) -> None:
    try:
        _RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
        _SITE_GEOCACHE_PATH.write_text(
            json.dumps(cache, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _geocode_one(location: str) -> dict | None:
    try:
        from geopy.geocoders import Nominatim

        from gems_geography import countries_match, preferred_country

        geolocator = Nominatim(user_agent="gems-dashboard")
        want_country = preferred_country(location)

        def _try(query: str) -> dict | None:
            result = geolocator.geocode(query, timeout=8, addressdetails=True)
            if not result:
                return None
            got_country = (result.raw.get("address") or {}).get("country")
            if want_country and got_country and not countries_match(want_country, got_country):
                return None
            return {
                "lat": float(result.latitude),
                "lon": float(result.longitude),
                "country": got_country,
            }

        coords = _try(_normalize_site_for_geocode(location))
        if coords:
            return coords
        if want_country:
            return _try(f"{humanize_affiliation(location)}, {want_country}")
    except Exception:
        return None
    return None


@st.cache_data(ttl=86400, show_spinner=False)
def _locate_institutions(sites: tuple[tuple[str, str, str], ...]) -> dict[str, dict | None]:
    """Coordinates per institution key: pins, then disk cache, Nominatim, keyword fallback.

    Cached for a day, including misses, so unknown institutions do not trigger
    a Nominatim round-trip on every page load.
    """
    global _SITE_GEOCACHE
    cache = dict(_SITE_GEOCACHE)
    changed = False
    result: dict[str, dict | None] = {}
    for cache_key, label, location in sites:
        coords = _geo_pinned_coordinates(label)
        if coords is None:
            coords = cache.get(cache_key) or cache.get(location)
        if coords is None:
            coords = _geocode_one(label) or _geo_fallback_coordinates(label)
            if coords:
                cache[cache_key] = coords
                changed = True
        result[cache_key] = (
            {
                "lat": float(coords["lat"]),
                "lon": float(coords["lon"]),
                "country": coords.get("country"),
            }
            if coords
            else None
        )
    if changed:
        _SITE_GEOCACHE = cache
        _write_site_geocache(cache)
    return result


def _geocode_sites(sites: list[dict]) -> list[dict]:
    keyed = []
    for site in sites:
        label = site.get("label") or site["location"]
        cache_key = site.get("cache_key") or institution_key(label)
        keyed.append((cache_key, label, site["location"], site))
    coords_by_key = _locate_institutions(tuple((k, lab, loc) for k, lab, loc, _ in keyed))
    located: list[dict] = []
    for cache_key, label, _, site in keyed:
        coords = coords_by_key.get(cache_key)
        if coords:
            located.append(
                {
                    "cache_key": cache_key,
                    "location": label,
                    "study_count": site.get("study_count", 0),
                    "study_ids": site.get("study_ids", []),
                    **coords,
                }
            )
    return located


_HOME_CSS = """
<style>
  .gems-kpis {
    display: grid; grid-template-columns: repeat(6, minmax(0, 1fr));
    gap: 0.75rem; margin: 0.4rem 0 0.2rem 0;
  }
  @media (max-width: 1100px) { .gems-kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
  @media (max-width: 560px)  { .gems-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  .gems-kpi {
    background: #ffffff; border: 1px solid #E1E7E3; border-radius: 12px;
    padding: 0.85rem 0.9rem; border-top: 4px solid var(--accent, #2E7D32);
  }
  .gems-kpi .v { font-size: 1.65rem; font-weight: 750; color: #1A2B20; line-height: 1.15; }
  .gems-kpi .l { font-size: 0.8rem; color: #5B6B61; margin-top: 0.15rem; }

  .gems-inside { list-style: none; padding: 0; margin: 0.2rem 0 0 0; }
  .gems-inside li {
    display: flex; align-items: baseline; gap: 0.6rem;
    padding: 0.55rem 0; border-bottom: 1px solid #EEF2EF;
  }
  .gems-inside li:last-child { border-bottom: none; }
  .gems-inside .n { font-weight: 750; color: #1B5E20; font-size: 1.15rem; min-width: 6.2rem; }
  .gems-inside .t { color: #37473C; font-size: 0.92rem; }

  .gems-site-head { font-size: 1.05rem; font-weight: 700; color: #1A2B20; margin: 0.2rem 0 0.1rem 0; }
  .gems-site-stats { color: #37473C; font-size: 0.9rem; margin: 0.55rem 0 0.6rem 0; line-height: 1.5; }
  .gems-site-stats b { color: #1A2B20; }
  .gems-pill {
    display: inline-block; border-radius: 999px; padding: 0.12rem 0.6rem;
    font-size: 0.78rem; font-weight: 650; color: #fff; margin-right: 0.3rem;
  }
</style>
"""


def _countries_with_studies(located: list[dict]) -> dict[str, set[str]]:
    by_country: dict[str, set[str]] = {}
    for site in located:
        country = english_country(site.get("country"))
        if country:
            by_country.setdefault(country, set()).update(site.get("study_ids", []))
    return by_country


def _site_type_counts(site: dict, details: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sid in site.get("study_ids", []):
        label = (details.get(sid) or {}).get("type") or "Other"
        counts[label] = counts.get(label, 0) + 1
    return counts


def _site_type(counts: dict[str, int]) -> str:
    known = {k: v for k, v in counts.items() if k in ("Dairy", "Beef")}
    if len(known) > 1:
        return "Mixed"
    return next(iter(known), "Mixed")


def _year_span(values: list) -> str:
    years = sorted({int(v) for v in values if v})
    if not years:
        return "-"
    return str(years[0]) if years[0] == years[-1] else f"{years[0]}–{years[-1]}"


def _render_hero() -> None:
    gems_b64 = _clean_b64(GEMS_LOGO_PNG_B64)
    gmh_b64 = _clean_b64(GLOBAL_METHANE_HUB_PNG_B64)
    chip = (
        "display:inline-block;background:rgba(255,255,255,0.18);padding:0.25rem 0.75rem;"
        "border-radius:999px;margin:0.6rem 0.4rem 0 0;font-size:0.8rem;"
    )
    logo = "height:46px;width:auto;object-fit:contain;filter:brightness(0) invert(1);opacity:0.92;"
    st.markdown(
        f"""
<div style="font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  background:linear-gradient(120deg,#0F3D24 0%,#1B5E20 45%,#2E7D32 80%,#43A047 100%);
  color:#fff;border-radius:16px;padding:1.5rem 1.75rem;margin:0 0 1rem 0;
  box-shadow:0 10px 24px -12px rgba(31,107,66,0.55);
  display:flex;flex-wrap:wrap;gap:1.25rem;align-items:center;justify-content:space-between;">
  <div style="flex:1 1 420px;min-width:0;">
    <h1 style="margin:0 0 0.35rem 0;font-size:clamp(1.4rem,2.6vw,2.15rem);font-weight:750;
      line-height:1.15;color:#fff;">GreenFeed Emissions Measurement System</h1>
    <p style="margin:0;font-size:1.02rem;line-height:1.5;opacity:0.95;color:#fff;max-width:46rem;">
      Research herds around the world pooling GreenFeed methane measurements into one
      standardized, FAIR data warehouse &mdash; so every partner's numbers can be compared.
    </p>
    <span style="{chip}">Global Methane Hub</span>
    <span style="{chip}">Cornell University &middot; lead coordinator</span>
  </div>
  <div style="display:flex;align-items:center;gap:1.1rem;">
    <img src="data:image/png;base64,{gems_b64}" alt="GEMS" style="{logo}" />
    <div style="width:1px;height:34px;background:rgba(255,255,255,0.3);"></div>
    <img src="data:image/png;base64,{gmh_b64}" alt="Global Methane Hub" style="{logo}" />
  </div>
</div>
""".strip(),
        unsafe_allow_html=True,
    )


def _map_figure(located: list[dict], details: dict, by_country: dict[str, set[str]]):
    import plotly.graph_objects as go

    fig = go.Figure()
    if by_country:
        names = sorted(by_country)
        fig.add_trace(
            go.Choropleth(
                locations=names,
                z=[len(by_country[c]) for c in names],
                locationmode="country names",
                colorscale=[[0, "#C5E8C8"], [1, "#4CAF50"]],
                showscale=False,
                marker_line_color="#FFFFFF",
                marker_line_width=0.6,
                hovertemplate="<b>%{location}</b><br>%{z} gold studies<extra></extra>",
            )
        )
    groups: dict[str, list[dict]] = {}
    for site in located:
        counts = _site_type_counts(site, details)
        groups.setdefault(_site_type(counts), []).append({**site, "types": counts})
    for type_name in ("Dairy", "Beef", "Mixed"):
        sites = groups.get(type_name)
        if not sites:
            continue
        fig.add_trace(
            go.Scattergeo(
                name=f"{type_name} studies" if type_name != "Mixed" else "Dairy and beef",
                lon=[s["lon"] for s in sites],
                lat=[s["lat"] for s in sites],
                customdata=[s["cache_key"] for s in sites],
                text=[
                    f"<b>{s['location']}</b><br>{s['study_count']} gold "
                    f"stud{'y' if s['study_count'] == 1 else 'ies'}"
                    + "".join(f"<br>{k}: {v}" for k, v in sorted(s["types"].items()))
                    + "<br><i>Click for details</i>"
                    for s in sites
                ],
                hovertemplate="%{text}<extra></extra>",
                mode="markers",
                marker=dict(
                    size=[12 + 7 * math.sqrt(s["study_count"]) for s in sites],
                    color=_TYPE_COLORS[type_name],
                    opacity=0.88,
                    line=dict(color="#FFFFFF", width=1.6),
                ),
            )
        )
    fig.update_geos(
        projection_type="natural earth",
        showland=True,
        landcolor="#F1EFE7",
        showocean=True,
        oceancolor="#D4E9F7",
        showlakes=True,
        lakecolor="#D4E9F7",
        showcountries=True,
        countrycolor="#FFFFFF",
        showcoastlines=False,
        showframe=False,
        lataxis_range=[-50, 76],
        lonaxis_range=[-150, 165],
        bgcolor="rgba(0,0,0,0)",
    )
    fig.update_layout(
        height=360,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(
            orientation="h",
            x=0.01,
            y=0.03,
            bgcolor="rgba(255,255,255,0.88)",
            bordercolor="#E1E7E3",
            borderwidth=1,
            font=dict(size=12),
        ),
        clickmode="event+select",
        hoverlabel=dict(bgcolor="#ffffff", font_size=13),
    )
    return fig


def _render_site_panel(located: list[dict], details: dict) -> None:
    by_key = {site["cache_key"]: site for site in located}
    options = sorted(by_key, key=lambda k: (-by_key[k]["study_count"], by_key[k]["location"]))
    if st.session_state.get("home_site_pick") not in by_key:
        st.session_state["home_site_pick"] = options[0]
    key = st.selectbox(
        "Site",
        options,
        key="home_site_pick",
        format_func=lambda k: f"{by_key[k]['location']} ({by_key[k]['study_count']})",
        label_visibility="collapsed",
    )
    site = by_key[key]
    counts = _site_type_counts(site, details)
    pills = "".join(
        f'<span class="gems-pill" style="background:{_TYPE_COLORS.get(k, "#78909C")}">'
        f"{v} {k.lower()}</span>"
        for k, v in sorted(counts.items())
    )
    rows = [details.get(sid) or {} for sid in site.get("study_ids", [])]
    animals = sum(r.get("animals", 0) for r in rows)
    years = _year_span([r.get("start") for r in rows] + [r.get("end") for r in rows])
    st.markdown(
        f'<div class="gems-site-head">{html.escape(site["location"])}</div>'
        f'<div class="gems-muted" style="margin-bottom:0.45rem;">'
        f'{html.escape(english_country(site.get("country")) or "")} &middot; {years}</div>'
        f"{pills}"
        f'<div class="gems-site-stats"><b>{site["study_count"]:,}</b> studies &middot; '
        f"<b>{animals:,}</b> animals</div>",
        unsafe_allow_html=True,
    )


def _render_map_section(snapshot: dict) -> list[dict]:
    sites = snapshot.get("sites") or []
    details = snapshot.get("study_details") or {}
    showing_defaults = not sites
    located = _geocode_sites(sites or _DEFAULT_SITE_ROWS)
    by_country = _countries_with_studies(located)

    st.markdown("### Where the data comes from")
    col_map, col_panel = st.columns([2.6, 1], gap="medium")
    with col_map:
        event = st.plotly_chart(
            _map_figure(located, details, by_country),
            use_container_width=True,
            on_select="rerun",
            selection_mode="points",
            key="home_map",
            config={"displayModeBar": False, "scrollZoom": False},
        )
        picked = next(
            (p.get("customdata") for p in (event.selection.points if event else []) if p.get("customdata")),
            None,
        )
        if isinstance(picked, list):
            picked = picked[0] if picked else None
        if picked and picked != st.session_state.get("home_map_last"):
            st.session_state["home_map_last"] = picked
            st.session_state["home_site_pick"] = picked
    with col_panel:
        if showing_defaults:
            st.info("Live study data is unavailable right now; showing consortium members.")
        else:
            st.markdown(
                '<div class="gems-muted">Click a circle on the map, or choose a site:</div>',
                unsafe_allow_html=True,
            )
            _render_site_panel(located, details)
    if not showing_defaults:
        all_ids = {sid for site in sites for sid in site.get("study_ids", [])}
        mapped = {sid for site in located for sid in site.get("study_ids", [])}
        note = (
            f"Circle size = number of gold studies. {len(mapped):,} studies from "
            f"{len(located)} institutions in {len(by_country)} countries."
        )
        if len(mapped) < len(all_ids):
            note += f" {len(all_ids) - len(mapped)} studies could not be placed on the map."
        st.caption(note)
    return located


def _render_kpis(snapshot: dict, located: list[dict]) -> None:
    warehouse = snapshot.get("warehouse") or {}
    date_range = snapshot.get("date_range")
    if date_range:
        first, last = int(date_range[0][:4]), int(date_range[1][:4])
        years, years_label = f"{last - first + 1} years", f"of data, {first}–{last}"
    else:
        years, years_label = "-", "Years of data"
    tiles = [
        (_count_label(snapshot.get("studies")), "Gold studies", "#2E7D32"),
        (_count_label(snapshot.get("animals")), "Animals", "#00897B"),
        (_count_label(warehouse.get("visits")), "GreenFeed visits", "#1E88E5"),
        (_count_label(len(_countries_with_studies(located)) or None), "Countries", "#F4511E"),
        (_count_label(len(snapshot.get("sites") or []) or None), "Institutions", "#8E24AA"),
        (years, years_label, "#6D4C41"),
    ]
    st.markdown(
        '<div class="gems-kpis">'
        + "".join(
            f'<div class="gems-kpi" style="--accent:{accent}"><div class="v">{value}</div>'
            f'<div class="l">{label}</div></div>'
            for value, label, accent in tiles
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def _render_story(snapshot: dict) -> None:
    import plotly.graph_objects as go

    details = snapshot.get("study_details") or {}
    warehouse = snapshot.get("warehouse") or {}
    col_chart, col_inside = st.columns([1.7, 1], gap="large")
    with col_chart:
        st.markdown("### GEMS gold studies by start year")
        st.markdown(
            '<div class="gems-muted">Studies currently in the GEMS gold dataset, counted by '
            "the year each experiment started.</div>",
            unsafe_allow_html=True,
        )
        by_year: dict[str, dict[int, int]] = {}
        for item in details.values():
            if item.get("start"):
                label = item.get("type") or "Other"
                by_year.setdefault(label, {})
                by_year[label][int(item["start"])] = by_year[label].get(int(item["start"]), 0) + 1
        if by_year:
            years = range(
                min(min(v) for v in by_year.values()), max(max(v) for v in by_year.values()) + 1
            )
            fig = go.Figure()
            for label in sorted(by_year, key=lambda k: (k not in _TYPE_COLORS, k)):
                fig.add_trace(
                    go.Bar(
                        name=label,
                        x=list(years),
                        y=[by_year[label].get(y, 0) for y in years],
                        marker_color=_TYPE_COLORS.get(label, "#90A4AE"),
                        hovertemplate=f"%{{x}}: %{{y}} {label.lower()} studies<extra></extra>",
                    )
                )
            fig.update_layout(
                barmode="stack",
                height=300,
                margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", y=1.08, x=0),
                xaxis=dict(dtick=1, showgrid=False),
                yaxis=dict(title="Studies", gridcolor="#EEF2EF", rangemode="tozero"),
                bargap=0.25,
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.caption("Study dates load from Databricks when available.")
    with col_inside:
        st.markdown("### What's inside")
        items = [
            ("visits", "GreenFeed visits with CH₄, CO₂, O₂ and H₂"),
            ("intake", "daily feed-intake records"),
            ("milk", "milk yield and composition records"),
            ("bodyweight", "body-weight records"),
            ("chamber", "respiration-chamber readings"),
        ]
        rows = "".join(
            f'<li><span class="n">{warehouse[key]:,}</span><span class="t">{text}</span></li>'
            for key, text in items
            if warehouse.get(key)
        )
        st.markdown(
            f'<ul class="gems-inside">{rows}</ul>'
            '<div class="gems-muted" style="margin-top:0.5rem;">Plus diets, feed '
            "ingredients, digestibility and GreenFeed settings for every study.</div>",
            unsafe_allow_html=True,
        )


def _render_actions(authorized: bool) -> None:
    st.markdown("### Work with the data")
    if not authorized:
        st.markdown(
            '<div class="gems-muted" style="margin:-0.3rem 0 0.5rem 0;">Sign in and request '
            "access to use these tools.</div>",
            unsafe_allow_html=True,
        )
    actions = [
        ("page_explore.py", "Explore", "🔎", "Browse tables, preview rows and draw quick charts."),
        ("page_modeling.py", "Modeling", "📈", "Fit regression and mixed models, no code needed."),
        ("page_chat.py", "Chat", "💬", "Ask questions in plain English; read-only queries."),
        ("page_api_access.py", "API Access", "🔑", "Get a personal key to pull data into Python or R."),
    ]
    cols = st.columns(4, gap="medium")
    for col, (page, label, icon, blurb) in zip(cols, actions):
        with col, st.container(border=True):
            st.page_link(page, label=f"**{label}**", icon=icon)
            st.caption(blurb)


def _render_sidebar_contacts() -> None:
    people = "".join(
        f'<div style="margin-bottom:0.55rem;"><div style="font-weight:650;color:#1A2B20;">'
        f'{html.escape(name)}</div><a href="mailto:{email}" style="color:#1B5E20;'
        f'font-size:0.88rem;">{email}</a></div>'
        for name, email in _CONTACTS
    )
    st.sidebar.markdown(
        f'<div style="font-weight:700;color:#1B5E20;margin:0.4rem 0 0.5rem 0;">Contact</div>{people}',
        unsafe_allow_html=True,
    )


_SITE_GEOCACHE = _read_site_geocache()


def _render_home() -> None:
    user_info = get_current_user_info()
    authorized = False
    if is_signed_in(user_info):
        sidebar_user(user_info.email)
        st.sidebar.markdown(sign_out_button_html(), unsafe_allow_html=True)
        authorized = is_authorized(user_info.email)
        if authorized:
            st.sidebar.caption(
                "Use the links above to explore data, fit models, chat, and manage API access."
            )
        else:
            if not user_info.email_verified:
                render_email_verification_banner(user_info)
            st.sidebar.warning(
                "You are signed in, but data pages still require administrator approval "
                "and a verified email. Contact the dashboard administrator to request access."
            )
    else:
        st.sidebar.markdown("### GEMS Dashboard")
        st.sidebar.info(
            "Browse this overview without an account. You may sign in, but access to "
            "Explore, Modeling, Chat, and API Access still requires administrator "
            "approval and a verified email."
        )
        st.sidebar.markdown(sign_in_button_html("Sign in"), unsafe_allow_html=True)
    st.sidebar.markdown("---")
    _render_sidebar_contacts()

    st.markdown(_HOME_CSS, unsafe_allow_html=True)
    _render_hero()
    with st.spinner("Loading live figures from Databricks..."):
        snapshot = _home_snapshot()
    located = _render_map_section(snapshot)
    _render_kpis(snapshot, located)
    st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)
    _render_story(snapshot)
    st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)
    _render_actions(authorized)


pg = st.navigation(
    [
        st.Page(_render_home, title="Home", icon="🌱", default=True),
        st.Page("page_explore.py", title="Explore", icon="🔎"),
        st.Page("page_modeling.py", title="Modeling", icon="📈"),
        st.Page("page_chat.py", title="Chat", icon="💬"),
        st.Page("page_api_access.py", title="API Access", icon="🔑"),
    ]
)
pg.run()


