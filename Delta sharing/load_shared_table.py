#!/usr/bin/env python3
"""
Run: python load_shared_table.py

Packages install automatically on first use. Put config.share or config.json in the same
folder as this script (or run the command from that folder).
"""

from __future__ import annotations

# Standard library only here — always available with Python (no pip).
import importlib
import re
import subprocess
import sys
from pathlib import Path

# Names/versions for `pip install` — not Python import statements.
_PIP_SPEC = [
    "pandas>=2.0",
    "pyarrow>=14.0",
    "openpyxl>=3.0",
    "delta-sharing>=1.0",
]


def _try_imports() -> bool:
    for mod in ("pandas", "pyarrow", "openpyxl", "delta_sharing"):
        try:
            importlib.import_module(mod)
        except ImportError:
            return False
    return True


def _ensure_packages() -> None:
    if _try_imports():
        return
    print(
        "Installing required Python packages (first time may take 1–2 minutes) …",
        flush=True,
    )
    try:
        subprocess.run(
            [sys.executable, "-m", "ensurepip", "--upgrade"],
            capture_output=True,
            text=True,
        )
    except OSError:
        pass
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print("Note: could not upgrade pip (often safe to ignore).", flush=True)
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", *_PIP_SPEC],
        check=False,
        text=True,
    )
    if r.returncode != 0:
        sys.exit(
            "pip install failed. Try in a terminal:\n"
            f'  "{sys.executable}" -m pip install {" ".join(_PIP_SPEC)}\n'
            "If you see permission errors, run the terminal as administrator or use a user install."
        )
    importlib.invalidate_caches()
    if not _try_imports():
        sys.exit(
            "Packages installed but import still failed. Close the terminal and run the script again."
        )


_ensure_packages()

# Third-party imports only after pip may have installed them above.
import pandas as pd
from delta_sharing import SharingClient, load_as_pandas


def script_dir() -> Path:
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()


CONFIG_NAMES = ("config.share", "config.json")


def find_config() -> Path:
    candidates: list[Path] = []
    for base in (script_dir(), Path.cwd()):
        for name in CONFIG_NAMES:
            candidates.append(base / name)
    seen: set[Path] = set()
    for p in candidates:
        p = p.resolve()
        if p in seen:
            continue
        seen.add(p)
        if p.is_file():
            return p
    raise FileNotFoundError(
        "Missing profile file. Save your activation download as config.share or config.json "
        "next to this script, or open a terminal in that folder and run the script from there."
    )


def _safe_stem(t) -> str:
    raw = f"{t.share}__{t.schema}__{t.name}"
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in raw)


# Drop lineage / ingest metadata (match case-insensitive, ignore spaces/underscores in names).
_DROP_COL_NORMS = frozenset(
    {
        "sequence",
        "workbookfile",
        "workbookpath",
        "gateruneid",
        "ingestruneid",  # ingestRunId
        "ingestid",
    }
)


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _drop_pipeline_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    drop = [c for c in df.columns if _norm_col(c) in _DROP_COL_NORMS]
    if drop:
        df = df.drop(columns=drop)
    return df


def _strip_tz_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in df.select_dtypes(include=["datetimetz"]).columns:
        df[col] = df[col].dt.tz_convert("UTC").dt.tz_localize(None)
    return df


def _write_html(path: Path, title: str, table_html: str) -> None:
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>{title}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:16px;background:#f5f5f5;}}
.wrap{{overflow:auto;max-width:100%;background:#fff;padding:12px;border-radius:8px;}}
table.data{{border-collapse:collapse;font-size:12px;}}
table.data th,table.data td{{border:1px solid #ccc;padding:6px 10px;}}
table.data th{{background:#222;color:#fff;position:sticky;top:0;}}
</style></head><body><p><b>{title}</b></p><div class="wrap">{table_html}</div></body></html>"""
    path.write_text(doc, encoding="utf-8")


def main() -> None:
    config = find_config()
    profile = str(config)
    print("Using profile:", profile, flush=True)
    client = SharingClient(profile)
    tables = list(client.list_all_tables())
    if not tables:
        sys.exit("No shared tables found.")

    out = script_dir() / "shared_table_exports"
    out.mkdir(parents=True, exist_ok=True)

    for t in tables:
        url = f"{profile}#{t.share}.{t.schema}.{t.name}"
        stem = _safe_stem(t)
        print(f"Loading {t.share}.{t.schema}.{t.name} ...", flush=True)
        df = load_as_pandas(url)
        df = _drop_pipeline_columns(df)
        df = _strip_tz_for_excel(df)

        xlsx = out / f"{stem}.xlsx"
        html_path = out / f"{stem}.html"
        df.to_excel(xlsx, index=False, engine="openpyxl")
        inner = df.to_html(
            index=False, escape=True, border=0, classes="data", justify="left"
        )
        _write_html(html_path, f"{stem} — {len(df)} rows × {len(df.columns)} cols", inner)
        print(f"  {xlsx.name}", flush=True)
        print(f"  {html_path.name}", flush=True)

    print(f"Done. Output folder: {out}", flush=True)


if __name__ == "__main__":
    main()
