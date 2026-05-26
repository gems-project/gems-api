"""Affiliation parsing and geocoding helpers for the home-page map."""

from __future__ import annotations

import re

# Country/region hints extracted from affiliation text (order = preference when ambiguous).
_COUNTRY_HINTS: list[tuple[str, str]] = [
    ("united states", "United States"),
    ("usa", "United States"),
    ("u.s.a", "United States"),
    ("canada", "Canada"),
    ("australia", "Australia"),
    ("switzerland", "Switzerland"),
    ("germany", "Germany"),
    ("france", "France"),
    ("netherlands", "Netherlands"),
    ("united kingdom", "United Kingdom"),
    ("uk", "United Kingdom"),
    ("new zealand", "New Zealand"),
    ("ireland", "Ireland"),
    ("italy", "Italy"),
    ("spain", "Spain"),
    ("denmark", "Denmark"),
    ("sweden", "Sweden"),
    ("norway", "Norway"),
    ("belgium", "Belgium"),
    ("austria", "Austria"),
]

# Word-boundary fallbacks (longer keys first). Avoid bare "eth" matching inside unrelated words.
_LOCATION_FALLBACKS: list[tuple[str, dict]] = [
    ("lethbridge", {"lat": 49.6940, "lon": -112.8328, "country": "Canada"}),
    ("agri-food canada", {"lat": 45.4215, "lon": -75.6972, "country": "Canada"}),
    ("agriculture and agri-food", {"lat": 45.4215, "lon": -75.6972, "country": "Canada"}),
    ("university of guelph", {"lat": 43.5448, "lon": -80.2482, "country": "Canada"}),
    ("guelph", {"lat": 43.5448, "lon": -80.2482, "country": "Canada"}),
    ("ottawa", {"lat": 45.4215, "lon": -75.6972, "country": "Canada"}),
    ("cornell", {"lat": 42.4534, "lon": -76.4735, "country": "United States"}),
    ("ithaca", {"lat": 42.4430, "lon": -76.5019, "country": "United States"}),
    ("university of california", {"lat": 38.5449, "lon": -121.7405, "country": "United States"}),
    ("davis", {"lat": 38.5449, "lon": -121.7405, "country": "United States"}),
    ("university of new england", {"lat": -30.5142, "lon": 151.6690, "country": "Australia"}),
    ("armidale", {"lat": -30.5142, "lon": 151.6690, "country": "Australia"}),
    ("eth zurich", {"lat": 47.3769, "lon": 8.5417, "country": "Switzerland"}),
    ("zurich", {"lat": 47.3769, "lon": 8.5417, "country": "Switzerland"}),
    ("switzerland", {"lat": 46.8182, "lon": 8.2275, "country": "Switzerland"}),
    ("australia", {"lat": -25.2744, "lon": 133.7751, "country": "Australia"}),
    ("canada", {"lat": 45.4215, "lon": -75.6972, "country": "Canada"}),
    ("united states", {"lat": 39.8283, "lon": -98.5795, "country": "United States"}),
]

_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-zA-Z])(?=[0-9])|(?<=[0-9])(?=[a-zA-Z])"
)


def split_camel_case(text: str) -> str:
    """Turn camelCase/PascalCase affiliation tokens into spaced words."""
    raw = (text or "").strip()
    if not raw:
        return ""
    if " " in raw or "," in raw:
        return " ".join(raw.replace(",", " ").split())
    spaced = _CAMEL_BOUNDARY.sub(" ", raw)
    return " ".join(spaced.split())


def humanize_affiliation(affiliation: str) -> str:
    """Readable label for map popups and institution lists."""
    return split_camel_case(affiliation)


def preferred_country(affiliation: str) -> str | None:
    """Prefer US, Canada, Australia, or European country when present in text."""
    lowered = humanize_affiliation(affiliation).lower()
    for needle, country in _COUNTRY_HINTS:
        if re.search(rf"\b{re.escape(needle)}\b", lowered):
            return country
    return None


def geocode_query(affiliation: str) -> str:
    """Build a Nominatim-friendly query from camelCase affiliation."""
    text = humanize_affiliation(affiliation)
    if text.lower() == "cornell":
        return "Cornell University, Ithaca, New York, United States"
    country = preferred_country(affiliation)
    if country and country.lower() not in text.lower():
        return f"{text}, {country}"
    return text


def fallback_coordinates(affiliation: str) -> dict | None:
    """Keyword fallbacks with word boundaries (avoids 'eth' inside unrelated strings)."""
    normalized = humanize_affiliation(affiliation).lower()
    country = preferred_country(affiliation)
    for key, coords in _LOCATION_FALLBACKS:
        if re.search(rf"\b{re.escape(key)}\b", normalized):
            if country and coords.get("country") and coords["country"] != country:
                continue
            return dict(coords)
    return None


def geocode_affiliation(
    affiliation: str,
    cache: dict,
    geocode_fn,
    *,
    write_cache,
) -> dict | None:
    """Resolve one affiliation to lat/lon using cache, live geocode, then fallback."""
    key = (affiliation or "").strip()
    if not key:
        return None
    cached = cache.get(key)
    if cached is None:
        cached = geocode_fn(geocode_query(key))
        if cached:
            cache[key] = cached
            write_cache(cache)
    if cached is None:
        cached = fallback_coordinates(key)
        if cached:
            cached = {
                "lat": float(cached["lat"]),
                "lon": float(cached["lon"]),
                "country": cached.get("country"),
            }
            cache[key] = cached
            write_cache(cache)
    return cached
