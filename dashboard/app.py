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
    render_email_verification_banner,
)
from gems_logo_data import (  # noqa: E402
    GEMS_LOGO_PNG_B64,
    GLOBAL_METHANE_HUB_PNG_B64,
)
from gems_ui import apply_theme, render_html, sidebar_user  # noqa: E402
from llm_client import check_llm_endpoint  # noqa: E402

st.set_page_config(
    page_title="GEMS Dashboard",
    layout="wide",
    initial_sidebar_state="expanded",
    page_icon="🌱",
)

apply_theme()
check_llm_endpoint()


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


_HOME_CATALOG = "gems_catalog"
_HOME_SCHEMA = "gems_schema"
_RESOURCES_DIR = _DASHBOARD_ROOT / "resources"
_LOCATION_CACHE_PATH = _RESOURCES_DIR / "location_cache.json"
_CONSORTIUM_MEMBERS = [
    ("Cornell University", "cornell_university.png"),
    ("University of California", "university_of_california.png"),
    ("University of Guelph", "university_of_guelph.png"),
    ("ETH Zurich", "eth_zurich.png"),
    ("University of New England", "university_of_new_england.png"),
    ("Agriculture and Agri-Food Canada", "agriculture_and_agri_food_canada.png"),
]


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


def _fq(table: str) -> str:
    return f"`{_HOME_CATALOG}`.`{_HOME_SCHEMA}`.`{table}`"


def _col(name: str) -> str:
    return f"`{name}`"


def _norm_col(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


@st.cache_data(ttl=3600, show_spinner=False)
def _table_columns(table: str) -> list[str]:
    try:
        with _home_connect() as conn, conn.cursor() as cur:
            cur.execute(f"DESCRIBE TABLE {_fq(table)}")
            rows = cur.fetchall()
        cols = []
        for row in rows:
            name = str(row[0] or "").strip()
            if name and not name.startswith("#"):
                cols.append(name)
        return cols
    except Exception:
        return []


def _resolve_col(table: str, *candidates: str) -> str | None:
    columns = _table_columns(table)
    by_norm = {_norm_col(col): col for col in columns}
    for candidate in candidates:
        resolved = by_norm.get(_norm_col(candidate))
        if resolved:
            return resolved
    return None


def _fetch_one(sql: str):
    with _home_connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    return row


@st.cache_data(ttl=3600, show_spinner=False)
def _study_count_live():
    for table in ("bronzeanimalcharacteristics", "goldanimalcharacteristics"):
        study_col = _resolve_col(table, "studyId", "studyID", "study_id", "StudyId")
        if not study_col:
            continue
        try:
            row = _fetch_one(f"SELECT COUNT(DISTINCT {_col(study_col)}) FROM {_fq(table)}")
            if row and row[0] not in (None, 0):
                return int(row[0])
        except Exception:
            continue
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def _animal_count_live():
    for table in ("bronzeanimalcharacteristics", "goldanimalcharacteristics"):
        study_col = _resolve_col(table, "studyId", "studyID", "study_id", "StudyId")
        animal_col = _resolve_col(table, "AnimalIdentifier", "animalIdentifier", "animal_id")
        if not study_col or not animal_col:
            continue
        try:
            row = _fetch_one(
                f"SELECT COUNT(DISTINCT CONCAT({_col(study_col)}, '__', {_col(animal_col)})) "
                f"FROM {_fq(table)}"
            )
            if row and row[0] not in (None, 0):
                return int(row[0])
        except Exception:
            continue
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def _date_range_live():
    for table in ("goldexperimentaldesign", "bronzeexperimentaldesign"):
        date_col = _resolve_col(table, "Date", "date", "measurementDate", "MeasurementDate")
        if not date_col:
            continue
        try:
            date_expr = _col(date_col)
            row = _fetch_one(
                "SELECT "
                f"MIN(CASE WHEN {date_expr} IS NOT NULL AND {date_expr} > DATE '1990-01-01' "
                f"THEN {date_expr} END) AS min_date, "
                f"MAX(CASE WHEN {date_expr} IS NOT NULL AND {date_expr} <= current_date() "
                f"THEN {date_expr} END) AS max_date "
                f"FROM {_fq(table)}"
            )
            if row and row[0] is not None and row[1] is not None:
                return [_json_date(row[0]), _json_date(row[1])]
        except Exception:
            continue
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def _site_rows_live() -> list[dict]:
    for table in ("bronzeexperimentaldesign", "goldexperimentaldesign"):
        study_col = _resolve_col(table, "studyId", "studyID", "study_id", "StudyId")
        location_col = _resolve_col(
            table,
            "ExperimentalLocation",
            "experimentalLocation",
            "Experimental location",
            "experimental_location",
        )
        if not study_col or not location_col:
            continue
        try:
            loc = _col(location_col)
            with _home_connect() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT {loc}, COUNT(DISTINCT {_col(study_col)}) AS study_count "
                    f"FROM {_fq(table)} "
                    f"WHERE {loc} IS NOT NULL "
                    f"AND TRIM(CAST({loc} AS STRING)) <> '' "
                    f"GROUP BY {loc}"
                )
                rows = cur.fetchall()
            sites = [
                {"location": str(row[0]).strip(), "study_count": int(row[1] or 0)}
                for row in rows
                if row and str(row[0] or "").strip()
            ]
            if sites:
                return sites
        except Exception:
            continue
    return []


@st.cache_data(ttl=3600, show_spinner=False)
def _institution_count_live():
    sites = _site_rows_live()
    if sites:
        return len({site["location"] for site in sites if site.get("location")})
    return None


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


def _stat_value(cache_key: str, fetcher, formatter=lambda value: f"{value:,}") -> str:
    value = fetcher()
    if value is not None:
        st.session_state[cache_key] = value
    else:
        value = st.session_state.get(cache_key)
    if value is None:
        return "Loading..."
    return formatter(value)


def _date_stat_value() -> str:
    value = _date_range_live()
    if value:
        st.session_state["home_stat_date_range"] = value
        return _date_range_label(value)
    cached = st.session_state.get("home_stat_date_range")
    if cached:
        return _date_range_label(cached)
    return "No data"


def _date_range_label(value: list[str] | None) -> str:
    if not value:
        return "No data"
    return f"{_format_month(value[0])} - {_format_month(value[1])}"


def _read_location_cache() -> dict:
    try:
        if _LOCATION_CACHE_PATH.exists():
            return json.loads(_LOCATION_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_location_cache(cache: dict) -> None:
    try:
        _RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
        _LOCATION_CACHE_PATH.write_text(
            json.dumps(cache, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception:
        pass


@st.cache_data(ttl=3600, show_spinner=False)
def _geocode_sites(sites: list[dict]) -> list[dict]:
    cache = _read_location_cache()
    changed = False
    geolocator = None
    located: list[dict] = []
    for site in sites:
        location = site["location"]
        cached = cache.get(location)
        if cached is None:
            try:
                if geolocator is None:
                    from geopy.geocoders import Nominatim

                    geolocator = Nominatim(user_agent="gems-dashboard")
                result = geolocator.geocode(location, timeout=8)
                if result:
                    cached = {"lat": result.latitude, "lon": result.longitude}
                    cache[location] = cached
                    changed = True
            except Exception:
                cached = None
        if cached:
            located.append(
                {
                    "location": location,
                    "study_count": site.get("study_count", 0),
                    "lat": float(cached["lat"]),
                    "lon": float(cached["lon"]),
                }
            )
    if changed:
        _write_location_cache(cache)
    return located


def _render_stat_cards() -> None:
    institutions = _stat_value("home_stat_institutions", _institution_count_live)
    studies = _stat_value("home_stat_studies", _study_count_live)
    animals = _stat_value("home_stat_animals", _animal_count_live)
    date_range = _date_stat_value()

    sc1, sc2, sc3, sc4 = st.columns(4)
    cards = [
        (sc1, institutions, "Partner institutions"),
        (sc2, studies, "Studies"),
        (sc3, animals, "Animals"),
        (sc4, date_range, "Date range"),
    ]
    for col, value, label in cards:
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


def _render_site_map() -> None:
    st.markdown("#### Contributing sites around the world")
    sites = _site_rows_live()
    if sites:
        st.session_state["home_site_rows"] = sites
    else:
        sites = st.session_state.get("home_site_rows", [])

    located = _geocode_sites(sites) if sites else []
    if not located:
        st.info("Loading contributing-site map...")
        return

    import folium
    from folium.plugins import MarkerCluster
    from streamlit_folium import st_folium

    center_lat = sum(site["lat"] for site in located) / len(located)
    center_lon = sum(site["lon"] for site in located) / len(located)
    fmap = folium.Map(location=[center_lat, center_lon], zoom_start=2, tiles="CartoDB positron")
    cluster = MarkerCluster().add_to(fmap)
    for site in located:
        folium.Marker(
            location=[site["lat"], site["lon"]],
            popup=folium.Popup(
                f"<strong>{site['location']}</strong><br>{site['study_count']} studies",
                max_width=280,
            ),
            tooltip=site["location"],
            icon=folium.Icon(color="darkgreen", icon="leaf", prefix="fa"),
        ).add_to(cluster)
    st_folium(fmap, height=390, use_container_width=True)
    st.caption(
        "Locations are pulled from Databricks and geocoded with a committed local cache."
    )


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
    user = user_info.email
    sidebar_user(user)
    st.sidebar.markdown(
        '<a href="/.auth/logout" style="display:inline-block;margin:0.25rem 0 0.75rem 0;'
        "padding:0.38rem 0.85rem;background:#6b7280;color:#fff;border-radius:8px;"
        'text-decoration:none;font-weight:600;">Sign out</a>',
        unsafe_allow_html=True,
    )
    authorized = is_authorized(user)
    if authorized:
        st.sidebar.caption(
            "Use the links above to explore data, fit models, chat, and manage API access."
        )
    else:
        if not user_info.email_verified:
            render_email_verification_banner(user_info)
        st.sidebar.warning(
            "You are signed in but not yet authorized to access the data pages. "
            "Contact the dashboard administrator to request access."
        )

    st.markdown(_hero_html(), unsafe_allow_html=True)

    _render_consortium_section()
    st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)
    _render_stat_cards()
    st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)

    col_map, col_mission = st.columns([1.55, 1], gap="large")
    with col_map:
        _render_site_map()

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
        '<div class="gems-footer">Authentication by Auth0 through Azure App Service '
        "Authentication. Use Sign out if you are signed in with the wrong account.</div>",
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


