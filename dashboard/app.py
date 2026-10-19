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

import json
import os
import sys
import textwrap
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
    dedupe_institution_labels,
    fallback_coordinates as _geo_fallback_coordinates,
    geocode_query as _geo_geocode_query,
    humanize_affiliation,
    institution_key,
    pinned_coordinates as _geo_pinned_coordinates,
)
from gems_ui import apply_theme, render_html, sidebar_user  # noqa: E402

st.set_page_config(
    page_title="GEMS Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="🌱",
)

apply_theme()


def _md_html(body: str) -> str:
    return textwrap.dedent(body).strip()


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
_BRONZE_SCHEMA = os.environ.get("GEMS_BRONZE_SCHEMA", "gems_schema")
_RESOURCES_DIR = _DASHBOARD_ROOT / "resources"
_SITE_GEOCACHE_PATH = _RESOURCES_DIR / "site_geocache.json"
_CONSORTIUM_MEMBERS = [
    ("Cornell University", "cornell_university.png"),
    ("University of California", "university_of_california.png"),
    ("University of Guelph", "university_of_guelph.png"),
    ("ETH Zurich", "eth_zurich.png"),
    ("University of New England", "university_of_new_england.png"),
    ("Agriculture and Agri-Food Canada", "agriculture_and_agri_food_canada.png"),
]
_DEFAULT_SITE_ROWS = [
    {"location": "Cornell University, Ithaca, United States", "study_count": 0},
    {"location": "University of California, Davis, United States", "study_count": 0},
    {"location": "University of Guelph, Guelph, Canada", "study_count": 0},
    {"location": "ETH Zurich, Zurich, Switzerland", "study_count": 0},
    {"location": "University of New England, Armidale, Australia", "study_count": 0},
    {"location": "Agriculture and Agri-Food Canada, Ottawa, Canada", "study_count": 0},
]
_SITE_GEOCACHE: dict = {}
# Esri's light-gray canvas needs no API key (CARTO basemaps now require one).
_BASEMAP_TILES = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
    "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
)
_BASEMAP_ATTR = "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ"


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

    def bronze_partner_institutions(self) -> list[str]:
        """Unique partner affiliations from bronzecontributor (ingested, not QC'd)."""
        table = "bronzecontributor"
        affiliation_col = self.resolve(table, "Affiliation", "affiliation", schema=_BRONZE_SCHEMA)
        if not affiliation_col:
            return []
        aff = _col(affiliation_col)
        rows = self.rows(
            f"SELECT DISTINCT {aff} FROM {_fq(table, _BRONZE_SCHEMA)} "
            f"WHERE {aff} IS NOT NULL AND TRIM(CAST({aff} AS STRING)) <> ''"
        )
        return dedupe_institution_labels(
            [humanize_affiliation(str(row[0]).strip()) for row in rows]
        )


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
        "partners": [],
    }
    with _home_connect() as conn, conn.cursor() as cur:
        q = _HomeQueries(cur)
        steps = (
            ("counts", q.study_animal_counts),
            ("date_range", q.date_range),
            ("sites", q.site_rows),
            ("partners", q.bronze_partner_institutions),
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
                    "location": label,
                    "study_count": site.get("study_count", 0),
                    "study_ids": site.get("study_ids", []),
                    **coords,
                }
            )
    return located


def _render_stat_cards(snapshot: dict) -> None:
    partners = snapshot.get("partners") or []
    sites = snapshot.get("sites") or []
    institutions = _count_label(len(partners) if partners else None)
    studies = _count_label(snapshot.get("studies"))
    animals = _count_label(snapshot.get("animals"))
    date_range = _date_range_label(snapshot.get("date_range"))

    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.markdown(
        f'<div class="gems-stat"><div class="v">{institutions}</div>'
        f'<div class="l">Partner institutions</div></div>',
        unsafe_allow_html=True,
    )
    if partners:
        with sc1.popover("View partner institutions"):
            st.markdown("**Unique affiliations** from `bronzecontributor` (`gems_schema`):")
            st.caption(
                "Bronze = raw contributor records as ingested from data-entry templates "
                "(complete submissions, not yet quality-checked into gold)."
            )
            for name in partners:
                st.markdown(f"- {name}")
    else:
        sc1.caption("Partner list loads from Databricks when available.")

    sc2.markdown(
        f'<div class="gems-stat"><div class="v">{studies}</div>'
        f'<div class="l">Gold studies</div></div>',
        unsafe_allow_html=True,
    )
    gold_inst = [site["label"] for site in sites if site.get("label")]
    if gold_inst:
        with sc2.popover("View gold study institutions"):
            st.markdown("**Institutions in gold studies** from `goldcontributor` (`gold_v1`):")
            st.caption(
                "Gold = curated, quality-checked tables used for analysis and the map. "
                f"{len(gold_inst)} institution(s) among {studies} gold studies."
            )
            for name in gold_inst:
                st.markdown(f"- {name}")

    for col, value, label in (
        (sc3, animals, "Animals"),
        (sc4, date_range, "Date range"),
    ):
        col.markdown(
            f'<div class="gems-stat"><div class="v">{value}</div>'
            f'<div class="l">{label}</div></div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        """
        <div class="gems-trust-row">
          <span>Live | Data from Databricks</span>
          <span>Secure | Auth0 sign-in + allowlist</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_consortium_section() -> None:
    st.markdown("#### Consortium Members")
    logos_dir = _DASHBOARD_ROOT / "assets" / "logos"
    logo_files = sorted(logos_dir.glob("*")) if logos_dir.exists() else []

    if logo_files:
        cols = st.columns(min(6, len(_CONSORTIUM_MEMBERS)))
        for idx, (name, filename) in enumerate(_CONSORTIUM_MEMBERS):
            logo_path = logos_dir / filename
            with cols[idx % len(cols)]:
                if logo_path.exists():
                    st.image(str(logo_path), caption=name, use_container_width=True)
                else:
                    st.markdown(f'<div class="gems-member-card">{name}</div>', unsafe_allow_html=True)
    else:
        cards = "".join(
            f'<div class="gems-member-card">{name}</div>' for name, _ in _CONSORTIUM_MEMBERS
        )
        st.markdown(f'<div class="gems-member-grid">{cards}</div>', unsafe_allow_html=True)


def _render_site_map(snapshot: dict) -> None:
    st.markdown("#### Contributing sites around the world")
    sites = snapshot.get("sites") or []
    showing_defaults = not sites
    if showing_defaults:
        sites = _DEFAULT_SITE_ROWS

    located = _geocode_sites(sites)

    import folium
    from folium.plugins import MarkerCluster
    from streamlit_folium import st_folium

    center_lat = sum(site["lat"] for site in located) / len(located) if located else 20
    center_lon = sum(site["lon"] for site in located) / len(located) if located else 0
    fmap = folium.Map(location=[center_lat, center_lon], zoom_start=2, tiles=None)
    folium.TileLayer(
        tiles=_BASEMAP_TILES, attr=_BASEMAP_ATTR, name="Light gray", max_zoom=16
    ).add_to(fmap)
    cluster = MarkerCluster().add_to(fmap)
    for site in located:
        n = site["study_count"]
        folium.Marker(
            location=[site["lat"], site["lon"]],
            popup=folium.Popup(
                f"<strong>{site['location']}</strong><br>{n} gold stud{'y' if n == 1 else 'ies'}",
                max_width=280,
            ),
            tooltip=site["location"],
            icon=folium.Icon(color="darkgreen", icon="leaf", prefix="fa"),
        ).add_to(cluster)
    # returned_objects=[] stops pan/zoom from triggering a full Streamlit rerun.
    st_folium(fmap, height=390, use_container_width=True, returned_objects=[])

    if showing_defaults:
        st.caption("Live affiliation data is unavailable; showing consortium members.")
        return
    all_ids = {sid for site in sites for sid in site.get("study_ids", [])}
    mapped_ids = {sid for site in located for sid in site.get("study_ids", [])}
    caption = (
        f"Map shows {len(mapped_ids):,} gold studies across {len(located)} institutions."
        if len(mapped_ids) == len(all_ids)
        else (
            f"Map shows {len(mapped_ids):,} of {len(all_ids):,} gold studies "
            f"({len(located)} of {len(sites)} institutions located)."
        )
    )
    if sum(site.get("study_count", 0) for site in sites) > len(all_ids):
        caption += " Some studies list more than one institution."
    st.caption(caption)


_SITE_GEOCACHE = _read_site_geocache()


def _hero_html() -> str:
    """Hero HTML with embedded logos for st.markdown(unsafe_allow_html=True).

    ``st.markdown`` renders ``data:`` image URLs in the top-level document
    (not a sandboxed iframe), so CSP does not block the embedded logos.
    """
    gems_b64 = _clean_b64(GEMS_LOGO_PNG_B64)
    gmh_b64 = _clean_b64(GLOBAL_METHANE_HUB_PNG_B64)

    chips = (
        '<span style="display:inline-block;background:rgba(255,255,255,0.18);'
        "padding:0.25rem 0.75rem;border-radius:999px;margin:0.35rem 0.4rem 0 0;"
        'font-size:0.8rem;">Global Methane Hub</span>'
        '<span style="display:inline-block;background:rgba(255,255,255,0.18);'
        "padding:0.25rem 0.75rem;border-radius:999px;margin:0.35rem 0.4rem 0 0;"
        'font-size:0.8rem;">Cornell University | lead coordinator</span>'
        '<span style="display:inline-block;background:rgba(255,255,255,0.18);'
        "padding:0.25rem 0.75rem;border-radius:999px;margin:0.35rem 0.4rem 0 0;"
        'font-size:0.8rem;">50+ partner institutions</span>'
        '<span style="display:inline-block;background:rgba(255,255,255,0.18);'
        "padding:0.25rem 0.75rem;border-radius:999px;margin:0.35rem 0.4rem 0 0;"
        'font-size:0.8rem;">Real-time emissions data</span>'
    )

    logo_img_css = (
        "height:52px;width:auto;object-fit:contain;display:block;"
        "filter:brightness(0) invert(1);opacity:0.92;"
    )

    return f"""
<div style="font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  background:linear-gradient(135deg,#15502c 0%,#1f6b42 55%,#2a8254 100%);
  color:#fff;border-radius:16px;padding:1.75rem 1.75rem 1.6rem;
  box-shadow:0 10px 24px -12px rgba(31,107,66,0.55);margin:0 0 1.25rem 0;">
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1.5rem;align-items:start;">
    <div>
      <h1 style="margin:0 0 0.45rem 0;font-size:clamp(1.35rem,2.5vw,2.05rem);
        font-weight:700;line-height:1.2;color:#fff;">
        GreenFeed Emissions Measurement System
      </h1>
      <p style="margin:0 0 0.75rem 0;font-size:1.02rem;line-height:1.55;opacity:0.96;color:#fff;">
        A global collaboration using the GreenFeed system to track methane
        and other gas emissions from ruminants, building a standardized,
        FAIR data warehouse for climate-smart livestock research.
      </p>
      <div>{chips}</div>
    </div>
    <div style="display:flex;flex-direction:column;justify-content:center;height:100%;
      padding-right:0.25rem;">
      <div style="display:flex;align-items:center;justify-content:flex-end;
        gap:1.25rem;margin-bottom:1rem;">
        <img src="data:image/png;base64,{gems_b64}" alt="GEMS" style="{logo_img_css}" />
        <div style="width:1px;height:36px;background:rgba(255,255,255,0.25);"></div>
        <img src="data:image/png;base64,{gmh_b64}" alt="Global Methane Hub" style="{logo_img_css}" />
      </div>
      <div style="background:rgba(255,255,255,0.13);border:1px solid rgba(255,255,255,0.25);
        border-radius:12px;padding:1rem 1.1rem;">
        <div style="font-size:0.7rem;letter-spacing:0.12em;text-transform:uppercase;
          opacity:0.9;margin-bottom:0.4rem;font-weight:600;color:#fff;">
          Our mission
        </div>
        <p style="margin:0;font-size:0.94rem;line-height:1.55;color:#fff;">
          Develop science-based, standardized operating procedures for both
          utilizing and interpreting GreenFeed data under different management
          practices - so every partner can produce comparable, defensible
          emissions measurements.
        </p>
      </div>
    </div>
  </div>
</div>
""".strip()


def _render_home() -> None:
    user_info = get_current_user_info()
    if is_signed_in(user_info):
        sidebar_user(user_info.email)
        st.sidebar.markdown(sign_out_button_html(), unsafe_allow_html=True)
        if is_authorized(user_info.email):
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

    st.markdown(_hero_html(), unsafe_allow_html=True)

    _render_consortium_section()
    st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)
    with st.spinner("Loading live figures from Databricks..."):
        snapshot = _home_snapshot()
    _render_stat_cards(snapshot)
    st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)

    col_map, col_mission = st.columns([1.55, 1], gap="large")
    with col_map:
        _render_site_map(snapshot)

    with col_mission:
        st.markdown("#### Why GEMS?")
        render_html(
            _md_html(
                """
                <div class="gems-pillars">
                  <div class="gems-pillar">
                    <div class="p-head">
                      <span class="p-dot">01</span>
                      <h5>Global collaboration</h5>
                    </div>
                    <p>Over 50 institutions across every continent quantifying ruminant
                    methane emissions at scale.</p>
                  </div>
                  <div class="gems-pillar">
                    <div class="p-head">
                      <span class="p-dot">02</span>
                      <h5>Real-time tracking</h5>
                    </div>
                    <p>Continuous measurements from GreenFeed units, streamed into a shared
                    warehouse within hours.</p>
                  </div>
                  <div class="gems-pillar">
                    <div class="p-head">
                      <span class="p-dot">03</span>
                      <h5>FAIR, standardized data</h5>
                    </div>
                    <p>Findable, Accessible, Interoperable, Reusable &mdash; common schemas
                    and shared SOPs across every site.</p>
                  </div>
                  <div class="gems-pillar">
                    <div class="p-head">
                      <span class="p-dot">04</span>
                      <h5>Shared infrastructure</h5>
                    </div>
                    <p>One pipeline, one set of quality checks, one analytical toolkit &mdash;
                    every partner benefits from every improvement.</p>
                  </div>
                </div>
                <div class="gems-muted" style="margin-top:0.9rem;">
                Contact: <a href="mailto:gems@cornell.edu">gems@cornell.edu</a>
                </div>
                """
            )
        )

    st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)
    st.markdown("#### What you can do here")

    c1, c2 = st.columns(2, gap="medium")
    c3, c4 = st.columns(2, gap="medium")

    with c1:
        st.markdown(
            _md_html(
                """
                <div class="gems-card">
                  <div class="gems-icon">🔎</div>
                  <h4>Explore &amp; Visualize</h4>
                  <p>Browse any table, inspect column types, preview rows, and render quick
                  Plotly charts. Ask the AI assistant to interpret what you see — it only
                  receives summary statistics, never raw data.</p>
                </div>
                """
            ),
            unsafe_allow_html=True,
        )

    with c2:
        st.markdown(
            _md_html(
                """
                <div class="gems-card">
                  <div class="gems-icon">🔑</div>
                  <h4>API Access</h4>
                  <p>Generate personal API keys, query tables from Python or R, and use
                  version-aware refresh scripts that overwrite local files only when gold
                  table versions change.</p>
                </div>
                """
            ),
            unsafe_allow_html=True,
        )

    with c3:
        st.markdown(
            _md_html(
                """
                <div class="gems-card">
                  <div class="gems-icon">📈</div>
                  <h4>Modeling</h4>
                  <p>Fit linear regression or linear mixed models across one or several joined
                  tables — multiple predictors, multiple random slopes, and AI-written
                  interpretation of R², coefficients, AIC/BIC, and more.</p>
                </div>
                """
            ),
            unsafe_allow_html=True,
        )

    with c4:
        st.markdown(
            _md_html(
                """
                <div class="gems-card">
                  <div class="gems-icon">💬</div>
                  <h4>Chat with your data</h4>
                  <p>Ask questions in plain English. The assistant lists tables, reads schemas,
                  writes SELECT queries, and summarizes results — every query is validated as
                  read-only before it touches Databricks.</p>
                </div>
                """
            ),
            unsafe_allow_html=True,
        )

    st.markdown(
        '<div class="gems-footer">Home is public. Sign in with Auth0 to request or use '
        "data pages; administrator approval and a verified email are required for "
        "Explore, Modeling, Chat, and API Access. Use Sign out if you are signed in "
        "with the wrong account.</div>",
        unsafe_allow_html=True,
    )

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


