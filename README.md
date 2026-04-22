# gems-api (GEMS data stack)

This repository now contains three operational pieces:

1. **API service** for programmatic access (`API/`)
2. **Dashboard app** for interactive access (`dashboard/`)
3. **Delta Sharing clients** for shared-table export (`Delta sharing/`)

## Repository layout

| Folder | Purpose | Main doc |
|---|---|---|
| [`API/`](API/) | FastAPI service for allowlisted Databricks gold-table CSV export over HTTPS (`X-API-Key`). | [`API/README.md`](API/README.md) |
| [`dashboard/`](dashboard/) | Streamlit dashboard (Home, Explore, Modeling, Chat, Download) with Entra sign-in and in-app data allowlist controls. | [`dashboard/README.md`](dashboard/README.md) |
| [`Delta sharing/`](Delta%20sharing/) | Python + R scripts to list/download tables from a Databricks share via `config.share`. | [`Delta sharing/README.md`](Delta%20sharing/README.md) |
| [`tools/`](tools/) | Operational scripts (dashboard deploy, env/debug helpers, table checks, logo embedding utility). | Inline script docs |

## Quick start by component

### API (local)

- `cd API`
- copy `.env.example` -> `.env`
- install dependencies and run `uvicorn` (see [`API/README.md`](API/README.md))

### Dashboard (local)

- `cd dashboard`
- copy `.env.example` -> `.env`
- `python -m venv .venv`
- `.venv\Scripts\activate`
- `pip install -r requirements.txt`
- `streamlit run app.py`

Full setup/deploy guide: [`dashboard/README.md`](dashboard/README.md)

### Dashboard (Azure deploy)

From repo root:

```powershell
.\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard -AsyncDeploy
```

### Delta Sharing clients

Place `config.share` in `Delta sharing/`, then run:

- `python load_shared_table.py`
- or `Rscript load_shared_table.R`

Details: [`Delta sharing/README.md`](Delta%20sharing/README.md)

## Access model summary (dashboard)

- **Sign-in:** handled by Azure App Service Easy Auth (Entra ID).
- **Data access:** controlled in-app via `ALLOWED_USERS` / `ALLOWED_DOMAINS`.
- Home can remain visible while data pages are restricted.

## Security

Do not commit secrets, including:

- `API/.env`
- `dashboard/.env`
- Databricks tokens
- OpenAI keys
- Azure storage connection strings
- `config.share` / `config.json`

Use `.env.example` files for structure only.

## License

See [`LICENSE`](LICENSE).
