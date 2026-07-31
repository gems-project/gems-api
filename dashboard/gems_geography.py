"""Affiliation parsing and geocoding helpers for the home-page map."""

from __future__ import annotations

import re
import unicodedata

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

# institution_key -> preferred geocode search string
_KNOWN_INSTITUTION_QUERIES: dict[str, str] = {
    "cornelluniversity": "Cornell University, Ithaca, New York, United States",
    "universityofcaliforniadavis": "University of California, Davis, California, United States",
    "universityofguelph": "University of Guelph, Ontario, Canada",
    "universityofnewengland": "University of New England, Armidale, New South Wales, Australia",
    "agricultureandagrifoodcanada": "Agriculture and Agri-Food Canada, Ottawa, Canada",
    "agricultureagrifoodcanada": "Agriculture and Agri-Food Canada, Ottawa, Canada",
    "universitacattolicadelsacrocuore": "Università Cattolica del Sacro Cuore, Piacenza, Italy",
    "universitcattolicadelsacrocuore": "Università Cattolica del Sacro Cuore, Piacenza, Italy",
    "researchinstituteforfarmanimalbiologyfbn": (
        "Research Institute for Farm Animal Biology (FBN), Dummerstorf, Germany"
    ),
    "researchinstituteforfarmanimalbiology": (
        "Research Institute for Farm Animal Biology (FBN), Dummerstorf, Germany"
    ),
    "ethzurich": "ETH Zurich, Zurich, Switzerland",
    # Keep "&" — Nominatim fails on "Wageningen University and Research".
    "wageningenuniversityandresearch": (
        "Wageningen University & Research, Wageningen, Netherlands"
    ),
    # Zodiac / De Elst 1 on Wageningen Campus (WLR headquarters).
    "wageningenlivestockresearch": "De Elst 1, 6708 WD Wageningen, Netherlands",
}

# Nominatim local country names that should match English preferred_country values.
_COUNTRY_ALIASES: dict[str, set[str]] = {
    "netherlands": {"netherlands", "nederland", "the netherlands"},
    "italy": {"italy", "italia"},
    "germany": {"germany", "deutschland"},
    "spain": {"spain", "españa", "espana"},
    "sweden": {"sweden", "sverige"},
    "norway": {"norway", "norge"},
    "denmark": {"denmark", "danmark"},
    "belgium": {"belgium", "belgië", "belgie", "belgique"},
    "switzerland": {"switzerland", "schweiz", "suisse", "svizzera"},
    "france": {"france"},
    "united states": {"united states", "united states of america", "usa"},
    "united kingdom": {"united kingdom", "uk", "great britain"},
    "canada": {"canada"},
    "australia": {"australia"},
    "new zealand": {"new zealand", "aotearoa"},
    "ireland": {"ireland", "éire", "eire"},
    "austria": {"austria", "österreich", "osterreich"},
}

# Word-boundary fallbacks (longer keys first).
_LOCATION_FALLBACKS: list[tuple[str, dict]] = [
    ("farmanimal biology", {"lat": 53.85, "lon": 12.23, "country": "Germany"}),
    ("fbn", {"lat": 53.85, "lon": 12.23, "country": "Germany"}),
    ("cattolica del sacro cuore", {"lat": 45.05, "lon": 9.70, "country": "Italy"}),
    ("sacro cuore", {"lat": 45.05, "lon": 9.70, "country": "Italy"}),
    (
        "wageningen livestock research",
        {"lat": 51.983631, "lon": 5.6589866, "country": "Netherlands"},
    ),
    (
        "wageningen university",
        {"lat": 51.985445, "lon": 5.663212, "country": "Netherlands"},
    ),
    ("wageningen", {"lat": 51.985445, "lon": 5.663212, "country": "Netherlands"}),
    ("lethbridge", {"lat": 49.6940, "lon": -112.8328, "country": "Canada"}),
    ("agri-food canada", {"lat": 45.4215, "lon": -75.6972, "country": "Canada"}),
    ("agriculture and agri-food", {"lat": 45.4215, "lon": -75.6972, "country": "Canada"}),
    ("university of guelph", {"lat": 43.5448, "lon": -80.2482, "country": "Canada"}),
    ("guelph", {"lat": 43.5448, "lon": -80.2482, "country": "Canada"}),
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
    ("italy", {"lat": 41.8719, "lon": 12.5674, "country": "Italy"}),
    ("germany", {"lat": 51.1657, "lon": 10.4515, "country": "Germany"}),
    ("netherlands", {"lat": 52.1326, "lon": 5.2913, "country": "Netherlands"}),
    ("united states", {"lat": 39.8283, "lon": -98.5795, "country": "United States"}),
]

# Split ASCII/latin camelCase without breaking accented letters (e.g. Università).
_CAMEL_BOUNDARY = re.compile(
    r"(?<=[a-z\xe0-\xff])(?=[A-Z\xc0-\xd6\xd8-\xde])|"
    r"(?<=[A-Z\xc0-\xd6\xd8-\xde])(?=[A-Z\xc0-\xd6\xd8-\xde][a-z\xe0-\xff])|"
    r"(?<=[a-zA-Z\xe0-\xff])(?=[0-9])|(?<=[0-9])(?=[a-zA-Z\xe0-\xff])"
)
_SMALL_WORDS = {
    "of",
    "and",
    "for",
    "the",
    "del",
    "della",
    "de",
    "di",
    "da",
    "van",
    "von",
    "du",
}


def institution_key(label: str) -> str:
    """Stable dedupe key for partner lists (ignores case/punctuation)."""
    text = unicodedata.normalize("NFKD", label or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def split_camel_case(text: str) -> str:
    """Turn camelCase/PascalCase affiliation tokens into spaced words."""
    raw = (text or "").strip()
    if not raw:
        return ""
    raw = raw.replace("&", " and ")
    raw = re.sub(r"\s*\(\s*", " (", raw)
    raw = re.sub(r"\s+", " ", raw)
    if " " in raw and not _CAMEL_BOUNDARY.search(raw):
        return _titleize_words(raw)
    spaced = _CAMEL_BOUNDARY.sub(" ", raw)
    return _titleize_words(spaced)


def _titleize_words(text: str) -> str:
    parts = []
    for word in text.split():
        low = word.lower()
        if low in _SMALL_WORDS:
            parts.append(low)
        elif word.isupper() and len(word) <= 4:
            parts.append(word)
        else:
            parts.append(word[:1].upper() + word[1:])
    return " ".join(parts)


def humanize_affiliation(affiliation: str) -> str:
    """Readable label for map popups and institution lists."""
    return split_camel_case(affiliation)


def dedupe_institution_labels(labels: list[str]) -> list[str]:
    """Keep one display name per institution (e.g. one Cornell University)."""
    best: dict[str, str] = {}
    for label in labels:
        key = institution_key(label)
        if not key:
            continue
        prev = best.get(key)
        if prev is None or len(label) > len(prev):
            best[key] = label
    return sorted(best.values(), key=lambda s: s.lower())


def preferred_country(affiliation: str) -> str | None:
    """Prefer US, Canada, Australia, or European country when present in text."""
    lowered = humanize_affiliation(affiliation).lower()
    for needle, country in _COUNTRY_HINTS:
        if re.search(rf"\b{re.escape(needle)}\b", lowered):
            return country
    if "università" in lowered or "cattolica" in lowered or "sacro cuore" in lowered:
        return "Italy"
    if "fbn" in lowered or "farmanimal biology" in lowered or "farm animal biology" in lowered:
        return "Germany"
    if "wageningen" in lowered:
        return "Netherlands"
    return None


def countries_match(want: str | None, got: str | None) -> bool:
    """True when Nominatim country matches preferred English country (incl. aliases)."""
    if not want or not got:
        return True
    want_n = unicodedata.normalize("NFKD", want).encode("ascii", "ignore").decode("ascii")
    got_n = unicodedata.normalize("NFKD", got).encode("ascii", "ignore").decode("ascii")
    want_l = want_n.strip().lower()
    got_l = got_n.strip().lower()
    if want_l == got_l:
        return True
    aliases = _COUNTRY_ALIASES.get(want_l)
    return bool(aliases and got_l in aliases)


def geocode_query(affiliation: str) -> str:
    """Build a Nominatim-friendly query from affiliation text."""
    key = institution_key(humanize_affiliation(affiliation))
    if key in _KNOWN_INSTITUTION_QUERIES:
        return _KNOWN_INSTITUTION_QUERIES[key]
    text = humanize_affiliation(affiliation)
    if text.lower() == "cornell":
        return "Cornell University, Ithaca, New York, United States"
    country = preferred_country(affiliation)
    if country and country.lower() not in text.lower():
        return f"{text}, {country}"
    return text


def fallback_coordinates(affiliation: str) -> dict | None:
    """Keyword fallbacks with word boundaries."""
    key = institution_key(humanize_affiliation(affiliation))
    # Hard pins for institutions whose Nominatim names are unreliable.
    if key == "wageningenlivestockresearch":
        return {"lat": 51.983631, "lon": 5.6589866, "country": "Netherlands"}
    if key == "wageningenuniversityandresearch":
        return {"lat": 51.985445, "lon": 5.663212, "country": "Netherlands"}
    if key in _KNOWN_INSTITUTION_QUERIES:
        query = _KNOWN_INSTITUTION_QUERIES[key].lower()
        for phrase, coords in _LOCATION_FALLBACKS:
            if phrase in query:
                return dict(coords)
    normalized = humanize_affiliation(affiliation).lower()
    country = preferred_country(affiliation)
    for phrase, coords in _LOCATION_FALLBACKS:
        if re.search(rf"\b{re.escape(phrase)}\b", normalized):
            if country and coords.get("country") and coords["country"] != country:
                continue
            return dict(coords)
    return None
