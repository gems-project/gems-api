"""GEMS Dashboard — landing page and navigation.

Pages are registered with ``st.navigation`` using root-level ``page_*.py``
scripts. (Azure Oryx often omits a ``pages/`` subfolder from the runtime
extract, which breaks Streamlit's automatic multipage discovery.)

The hero is rendered with ``st.markdown(unsafe_allow_html=True)`` because
``data:`` image URLs work reliably in the top-level document, while
``st.components.v1.html`` iframes often block them via CSP.

Other HTML blocks use ``textwrap.dedent`` before Markdown where needed.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

_DASHBOARD_ROOT = Path(__file__).resolve().parent
load_dotenv(_DASHBOARD_ROOT / ".env", override=True)
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

from gems_auth import get_current_user, is_authorized  # noqa: E402
from gems_data import GemsData  # noqa: E402
from gems_logo_data import (  # noqa: E402
    GEMS_LOGO_PNG_B64,
    GLOBAL_METHANE_HUB_PNG_B64,
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
        'font-size:0.8rem;">Cornell University · lead coordinator</span>'
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
          practices — so every partner can produce comparable, defensible
          emissions measurements.
        </p>
      </div>
    </div>
  </div>
</div>
""".strip()


def _render_home() -> None:
    user = get_current_user()
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
            "Use the links above to explore data, download CSVs, fit models, and chat."
        )
    else:
        st.sidebar.warning(
            "You are signed in but not yet authorized to access the data pages. "
            "Contact the dashboard administrator to request access."
        )

    # Hero via st.markdown so embedded <img data:...> logos render (iframe CSP blocks them).
    st.markdown(_hero_html(), unsafe_allow_html=True)

    col_map, col_mission = st.columns([1.55, 1], gap="large")

    with col_map:
        st.markdown("#### Contributing sites around the world")

        pins = [
            ("Cornell University (Ithaca, NY)", 42.44, -76.50),
            ("University of Guelph (Ontario, CA)", 43.55, -80.25),
            ("Agriculture & Agri-Food Canada (Ottawa)", 45.42, -75.70),
            ("UC Davis (California)", 38.54, -121.74),
            ("Texas / Gulf region", 30.0, -97.5),
            ("Mexico / Central America", 19.4, -99.1),
            ("Colombia / northern Andes", 4.7, -74.1),
            ("Argentina / Pampas", -34.6, -58.4),
            ("Brazil / Cerrado", -15.8, -47.9),
            ("United Kingdom", 52.5, -1.9),
            ("Netherlands", 52.1, 5.3),
            ("ETH Zürich (Switzerland)", 47.37, 8.55),
            ("Ireland", 53.3, -6.3),
            ("Kenya (East Africa)", -1.3, 36.8),
            ("South Africa", -25.7, 28.2),
            ("India (IARI region)", 28.6, 77.2),
            ("China (Inner Mongolia)", 40.8, 111.7),
            ("University of New England (NSW, Australia)", -30.5, 151.65),
            ("New Zealand", -41.3, 174.8),
        ]

        fig = go.Figure(
            go.Scattergeo(
                lat=[p[1] for p in pins],
                lon=[p[2] for p in pins],
                text=[p[0] for p in pins],
                hoverinfo="text",
                mode="markers",
                marker=dict(
                    symbol="circle",
                    size=13,
                    color="#1565C0",
                    line=dict(color="#0D47A1", width=1.5),
                    opacity=0.88,
                ),
            )
        )
        fig.update_geos(
            projection_type="natural earth",
            showland=True,
            landcolor="#EAF3EE",
            showocean=True,
            oceancolor="#F4F8FA",
            showcountries=True,
            countrycolor="#C8D4CD",
            showcoastlines=True,
            coastlinecolor="#9FB3A7",
            showframe=False,
            lataxis=dict(range=[-55, 75]),
        )
        fig.update_layout(
            height=380,
            margin=dict(l=0, r=0, t=5, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Approximate locations of contributing sites. The GEMS data warehouse "
            "aggregates GreenFeed measurements from partners on every continent."
        )

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
                  <div class="gems-icon">⬇️</div>
                  <h4>Download</h4>
                  <p>Grab a full CSV of any table, or use incremental download to get only the
                  rows added since your last pull. Watermarks are tracked per user, so repeat
                  downloads are efficient.</p>
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

    st.markdown('<div class="gems-divider"></div>', unsafe_allow_html=True)

    try:
        data = GemsData()
        h = data.health()
        status = h.get("status", "unknown")
        table_count = int(h.get("allowed_table_count", 0))
        catalog = h.get("catalog", "?")
        schema = h.get("schema", "?")
    except Exception:
        status = "error"
        table_count = 0
        catalog = schema = "?"
        h = {}

    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.markdown(
        f'<div class="gems-stat"><div class="v">50+</div>'
        f'<div class="l">Partner institutions</div></div>',
        unsafe_allow_html=True,
    )
    sc2.markdown(
        f'<div class="gems-stat"><div class="v">{table_count}</div>'
        f'<div class="l">Tables available</div></div>',
        unsafe_allow_html=True,
    )
    sc3.markdown(
        f'<div class="gems-stat"><div class="v">Live</div>'
        f'<div class="l">Data from Databricks</div></div>',
        unsafe_allow_html=True,
    )
    sc4.markdown(
        f'<div class="gems-stat"><div class="v">Secure</div>'
        f'<div class="l">Azure Entra ID sign-in</div></div>',
        unsafe_allow_html=True,
    )

    st.markdown("#### Connection status")
    if status == "ok":
        st.success(
            f"Connected to `{catalog}.{schema}` — {table_count} tables available."
        )
    elif status == "error":
        st.error(
            "Could not reach Databricks. Check `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`, "
            "`DATABRICKS_TOKEN`, and `ALLOWED_TABLES`."
        )
    else:
        st.warning(
            f"Status `{status}` with {table_count} allowed tables. "
            "Check the `DATABRICKS_*` env vars and `ALLOWED_TABLES`."
        )

    st.markdown(
        '<div class="gems-footer">Authentication by Azure App Service (Entra ID). '
        "If you are signed in with the wrong account, clear your browser's Microsoft "
        "session and return here.</div>",
        unsafe_allow_html=True,
    )


pg = st.navigation(
    [
        st.Page(_render_home, title="Home", icon="🌱", default=True),
        st.Page("page_explore.py", title="Explore", icon="🔎"),
        st.Page("page_modeling.py", title="Modeling", icon="📈"),
        st.Page("page_chat.py", title="Chat", icon="💬"),
        st.Page("page_download.py", title="Download", icon="⬇️"),
    ]
)
pg.run()
