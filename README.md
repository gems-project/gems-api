# gems-api

GEMS tooling for **programmatic access** to gold Unity Catalog data: a **FastAPI** service that exports allowlisted tables as CSV, and **Delta Sharing** scripts (Python and R) for downloading shared tables with a `config.share` profile.

## Contents

| Folder | Purpose |
|--------|---------|
| [`API/`](API/) | **GEMS Gold Export API** — FastAPI app (`main.py`), Azure deployment notes, and CSV export over HTTPS with `X-API-Key`. Configure secrets in `API/.env` (see `API/.env.example`). Full runbook: [`API/README.md`](API/README.md). |
| [`Delta sharing/`](Delta%20sharing/) | **Delta Sharing clients** — `load_shared_table.py` and `load_shared_table.R` to list and download tables from a Databricks share using `config.share`. Sample exports may appear under `Delta sharing/shared_table_exports/`. User guide: [`Delta sharing/README.md`](Delta%20sharing/README.md). |

## Quick start

- **API (local):** `cd API`, copy `.env.example` to `.env`, install dependencies, run `uvicorn` as described in [`API/README.md`](API/README.md#5-phase-a--run-and-test-locally).
- **Delta Sharing:** place your `config.share` next to the scripts in `Delta sharing/`, then run `python load_shared_table.py` or `Rscript load_shared_table.R` as in [`Delta sharing/README.md`](Delta%20sharing/README.md#3-how-to-run-after-the-config-is-in-this-folder).

## Security

Do not commit secrets: `API/.env`, Databricks tokens, API keys, or `config.share` / `config.json`. Use private channels for credentials; keep `.env.example` and docs free of real values.

## License

See [`LICENSE`](LICENSE).
