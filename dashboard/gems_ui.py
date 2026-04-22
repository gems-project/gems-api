"""Shared UI helpers: global CSS, headers, cards, hero banners.

Imported by every page to give the app a consistent look.
"""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def logo_data_uri(filename: str) -> str:
    """Return a ``data:image/...;base64,...`` URI for a file in ``dashboard/assets/``.

    Returns an empty string if the file is missing, so the caller can cleanly
    fall back to text-only branding.
    """
    p = _ASSETS_DIR / filename
    if not p.exists():
        return ""
    ext = p.suffix.lstrip(".").lower()
    mime = "image/png" if ext == "png" else f"image/{ext}" if ext else "image/png"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


_CSS = """
<style>
  .block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1300px; }

  h1, h2, h3, h4 { color: #1A2B20; letter-spacing: -0.01em; }
  h1 { font-weight: 700; }
  h2 { font-weight: 650; }
  h3 { font-weight: 600; }

  .gems-hero {
    background: linear-gradient(135deg, #1B5E20 0%, #2E7D32 40%, #43A047 80%, #66BB6A 100%);
    color: #ffffff;
    padding: 2.2rem 2.2rem 2rem 2.2rem;
    border-radius: 16px;
    margin-bottom: 1.4rem;
    box-shadow: 0 10px 24px -12px rgba(46,125,50,0.5);
    position: relative;
    overflow: hidden;
  }
  .gems-hero::after {
    content: "";
    position: absolute;
    width: 340px; height: 340px; right: -110px; top: -110px;
    background: radial-gradient(circle at center, rgba(255,255,255,0.12), rgba(255,255,255,0) 60%);
    border-radius: 50%;
    pointer-events: none;
  }
  .gems-hero::before {
    content: "";
    position: absolute;
    width: 220px; height: 220px; left: -80px; bottom: -80px;
    background: radial-gradient(circle at center, rgba(255,255,255,0.08), rgba(255,255,255,0) 60%);
    border-radius: 50%;
    pointer-events: none;
  }
  .gems-hero-grid {
    display: grid;
    grid-template-columns: 1.25fr 1fr;
    gap: 1.8rem;
    position: relative; z-index: 1;
  }
  @media (max-width: 960px) {
    .gems-hero-grid { grid-template-columns: 1fr; }
  }
  .gems-hero-partners {
    display: flex; align-items: center; justify-content: center;
    gap: 1.6rem;
    background: linear-gradient(180deg, rgba(255,255,255,0.98) 0%, rgba(244,248,245,0.96) 100%);
    border: 1px solid rgba(255,255,255,0.35);
    border-radius: 12px;
    padding: 0.9rem 1.2rem;
    margin-bottom: 0.9rem;
    box-shadow: 0 6px 16px -8px rgba(0,0,0,0.28);
  }
  .gems-hero-partners img {
    object-fit: contain;
    display: block;
  }
  .gems-hero-partners img.gems-logo-gems { height: 72px; }
  .gems-hero-partners img.gems-logo-gmh  { height: 64px; }
  .gems-hero-partners .gems-logo-sep {
    width: 1px; align-self: stretch;
    background: linear-gradient(180deg, transparent 0%, #C8D4CD 25%, #C8D4CD 75%, transparent 100%);
  }
  @media (max-width: 720px) {
    .gems-hero-partners img.gems-logo-gems { height: 56px; }
    .gems-hero-partners img.gems-logo-gmh  { height: 50px; }
  }

  .gems-hero h1 { color: #ffffff; margin: 0 0 0.4rem 0; font-size: 2.1rem; }
  .gems-hero .gems-tagline {
    font-size: 1.05rem; opacity: 0.96; line-height: 1.5;
  }
  .gems-hero .gems-chip {
    display: inline-block; background: rgba(255,255,255,0.18);
    padding: 0.25rem 0.75rem; border-radius: 999px;
    margin-right: 0.4rem; margin-top: 0.7rem; font-size: 0.8rem;
    backdrop-filter: blur(2px);
  }
  .gems-mission {
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.22);
    border-radius: 12px;
    padding: 1.1rem 1.2rem;
    backdrop-filter: blur(4px);
  }
  .gems-mission .gems-mission-label {
    font-size: 0.72rem; letter-spacing: 0.14em;
    text-transform: uppercase; opacity: 0.85; margin-bottom: 0.45rem;
    font-weight: 600;
  }
  .gems-mission p {
    margin: 0; font-size: 0.95rem; line-height: 1.55; color: #ffffff;
  }

  .gems-pillars {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 0.9rem;
  }
  @media (max-width: 720px) {
    .gems-pillars { grid-template-columns: 1fr; }
  }
  .gems-pillar {
    position: relative;
    background: linear-gradient(140deg, #ffffff 0%, #F4F8F5 100%);
    border: 1px solid #E1E7E3;
    border-left: 4px solid #2E7D32;
    border-radius: 10px;
    padding: 0.95rem 1.05rem 0.95rem 1.05rem;
    transition: transform 0.12s ease, box-shadow 0.12s ease, border-left-color 0.12s ease;
  }
  .gems-pillar:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 14px -8px rgba(46,125,50,0.3);
    border-left-color: #66BB6A;
  }
  .gems-pillar .p-head {
    display: flex; align-items: center; gap: 0.55rem;
    margin-bottom: 0.3rem;
  }
  .gems-pillar .p-dot {
    width: 28px; height: 28px; border-radius: 50%;
    background: #E8F5E9; color: #1B5E20;
    display: inline-flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 0.9rem;
    border: 1px solid #C8E6C9;
  }
  .gems-pillar h5 {
    margin: 0; font-size: 0.98rem; color: #1B5E20;
    font-weight: 650;
  }
  .gems-pillar p {
    margin: 0; font-size: 0.9rem; line-height: 1.5; color: #37473C;
  }

  .gems-card {
    background: #ffffff; border: 1px solid #E1E7E3; border-radius: 12px;
    padding: 1.05rem 1.15rem; height: 100%;
    box-shadow: 0 1px 3px rgba(0,0,0,0.03);
    transition: transform 0.12s ease, box-shadow 0.12s ease, border-color 0.12s ease;
  }
  .gems-card:hover {
    border-color: #66BB6A;
    box-shadow: 0 6px 14px -6px rgba(46,125,50,0.25);
    transform: translateY(-1px);
  }
  .gems-card h4 { margin: 0 0 0.35rem 0; font-size: 1.05rem; color: #1B5E20; }
  .gems-card p  { margin: 0; color: #37473C; font-size: 0.92rem; line-height: 1.5; }
  .gems-card .gems-icon { font-size: 1.4rem; margin-bottom: 0.35rem; }

  .gems-stat {
    background: #F4F7F5; border-radius: 10px; padding: 0.9rem 1rem;
    border: 1px solid #E1E7E3; text-align: center;
  }
  .gems-stat .v { font-size: 1.6rem; font-weight: 700; color: #1B5E20; }
  .gems-stat .l { font-size: 0.82rem; color: #5B6B61; letter-spacing: 0.02em; }

  section[data-testid="stSidebar"] {
    background: #F7FAF8; border-right: 1px solid #E1E7E3;
  }
  section[data-testid="stSidebar"] h3 { color: #1B5E20; }

  div[data-testid="stMetric"] {
    background: #F4F7F5; border: 1px solid #E1E7E3; border-radius: 10px;
    padding: 0.7rem 0.9rem;
  }

  .gems-muted { color: #5B6B61; font-size: 0.9rem; }
  .gems-divider { border-top: 1px solid #E1E7E3; margin: 1.4rem 0; }

  .gems-footer {
    color: #5B6B61; font-size: 0.8rem; text-align: center;
    margin-top: 2rem;
  }
</style>
"""


def apply_theme() -> None:
    """Inject global CSS. Safe to call once per page (Streamlit dedupes)."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_html(html: str) -> None:
    """Render raw HTML through markdown with unsafe HTML enabled."""
    st.markdown(html, unsafe_allow_html=True)


def page_header(title: str, subtitle: str | None = None) -> None:
    """Consistent page header used on every non-landing page."""
    apply_theme()
    sub = (
        f'<div class="gems-muted" style="margin-top:-0.5rem;margin-bottom:0.9rem;">{subtitle}</div>'
        if subtitle
        else ""
    )
    st.markdown(
        f'<h1 style="margin-bottom:0.1rem;">{title}</h1>{sub}',
        unsafe_allow_html=True,
    )


def sidebar_user(user: str) -> None:
    """Show the signed-in user in the sidebar with consistent styling."""
    st.sidebar.markdown("### GEMS Dashboard")
    st.sidebar.markdown(
        f'<div style="font-size:0.82rem; color:#5B6B61;">Signed in as</div>'
        f'<div style="font-family:monospace; font-size:0.9rem;">{user}</div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("---")
