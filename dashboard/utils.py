from __future__ import annotations

import re

_GREENFEED_TOKEN = "GREENFEEDTOKEN"
_KNOWN_WORDS = (
    "characteristics",
    "experimental",
    "visitation",
    "identifier",
    "greenfeed",
    "emission",
    "methane",
    "animal",
    "intake",
    "weight",
    "design",
    "raw",
    "per",
    "day",
    "data",
    "body",
)


def _split_known_lowercase(token: str) -> list[str]:
    remaining = token
    words: list[str] = []
    while remaining:
        match = next((word for word in _KNOWN_WORDS if remaining.startswith(word)), None)
        if not match:
            words.append(remaining)
            break
        words.append(match)
        remaining = remaining[len(match) :]
    return words


def prettify_table_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return raw

    value = re.sub(r"^(bronze|gold)", "", raw, flags=re.IGNORECASE)
    value = re.sub(r"greenfeed", f" {_GREENFEED_TOKEN} ", value, flags=re.IGNORECASE)
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", value)
    value = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", value)
    value = re.sub(r"\s+", " ", value).strip()

    tokens: list[str] = []
    for word in value.split():
        if word == _GREENFEED_TOKEN:
            tokens.append("GreenFeed")
        elif word.islower():
            tokens.extend(part.capitalize() for part in _split_known_lowercase(word))
        else:
            tokens.append(word.capitalize())
    words = ["GreenFeed" if word.lower() == "greenfeed" else word for word in tokens]
    return " ".join(words)