# GEMS Dashboard

Streamlit dashboard for GEMS data exploration, modeling, chat, and API access.
The app connects directly to Databricks SQL for interactive pages and manages
per-user API keys for the separate FastAPI web app.

For a detailed project history and issue log, see `dashboard/note.md`.

## Quick Architecture

- **Frontend/runtime:** Streamlit (`app.py` + `page_*.py`)
- **Auth:** Auth0 through Azure App Service Easy Auth
- **Data:** Databricks SQL warehouse (`gems_catalog.gold_v1`)
- **AI:** Databricks-hosted LLM endpoint (plot/model interpretation + chat)
- **API keys:** Azure Table Storage hashed per-user key records

## Main Files

- `app.py`: home page + navigation + hero UI.
- `page_explore.py`: data browsing, joins, charts, chart interpretation.
- `page_modeling.py`: OLS/MixedLM workflows and interpretation.
- `page_chat.py`: chat over data with SQL safety checks.
- `resources/data_dictionary.json`: editable table/column dictionary used by chat.
- `page_api_access.py`: API-key creation/revocation and Python/R examples.
- `gems_api_keys.py`: API-key generation, hashing, and Azure Table Storage records.
- `gems_data.py`: Databricks query/access layer.
- `gems_auth.py`: user identity + allowlist gate.
- `gems_logo_data.py`: embedded logo bytes for stable rendering in Azure.

## Local Development

From repo root:

```powershell
cd dashboard
copy .env.example .env
```

Edit `.env` with real values (minimum: `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`,
`DATABRICKS_TOKEN`, `DATABRICKS_LLM_ENDPOINT`, `ALLOWED_TABLES`).

Then run:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Open `http://localhost:8501`.

## Azure Setup (One-Time)

1. Create Linux App Service Web App (`gems-dashboard`).
2. Enable Authentication with Auth0 as an OpenID Connect provider through Easy Auth.
3. Configure App Settings (Environment variables):
   - Databricks: `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN`
   - Dataset scope: `GEMS_CATALOG`, `GEMS_SCHEMA`, `ALLOWED_TABLES`
   - AI: `DATABRICKS_LLM_ENDPOINT`
   - API access: `AZURE_TABLES_CONNECTION_STRING`, `AZURE_API_KEYS_TABLE`, `API_KEY_PEPPER`, `GEMS_API_BASE_URL`
   - Data access gate: `ALLOWED_USERS` and/or `ALLOWED_DOMAINS`
   - Optional Auth0 verification resend: `AUTH0_DOMAIN` plus either `AUTH0_MANAGEMENT_API_TOKEN` or both `AUTH0_MANAGEMENT_CLIENT_ID` and `AUTH0_MANAGEMENT_CLIENT_SECRET`. The Management API application needs permission to create verification email jobs (`create:user_tickets` / verification email job access in Auth0 Management API). If these are omitted, the dashboard shows a friendly message and asks users to use the original Auth0 verification email or contact an administrator.

## Access Control Model (Current)

- Easy Auth controls who can sign in.
- `gems-dashboard.ALLOWED_USERS` controls dashboard data-page access.
- `GEMS-API.ALLOWED_USERS` controls API-tier data access and must be a subset of `gems-dashboard.ALLOWED_USERS`.
- Home page remains visible to signed-in users.
- Anyone added to `GEMS-API.ALLOWED_USERS` must also be added to `gems-dashboard.ALLOWED_USERS`.
- Explore/Modeling/Chat call `require_authorized_user()`; API Access also checks API-tier access through `GEMS-API /authz/me`.

`ALLOWED_USERS` example:

```text
puchun.niu@cornell.edu,collaborator@cornell.edu
```

Only verified emails in `ALLOWED_USERS` receive dashboard data access.

## Deploy to Azure

From repo root:

```powershell
.\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard -AsyncDeploy
```

What the script does:

1. Builds `gems-dashboard.zip` from `dashboard/`
2. Sets startup command
3. Deploys zip with Azure CLI
4. Restarts app and prints the app URL

## Update Workflow

1. Edit files locally in `dashboard/`
2. Test with local Streamlit
3. Deploy with `deploy_dashboard.ps1`
4. Verify in browser and Azure Log stream

## Troubleshooting (Common)

- **Deploy says success but app errors:** check runtime logs; upload/build success is separate from app logic success.
- **No tables shown:** verify `ALLOWED_TABLES` and Databricks env vars.
- **Unauthorized on data pages:** check `ALLOWED_USERS`/`ALLOWED_DOMAINS`.
- **Git object cleanup prompts on Windows/OneDrive:** usually non-fatal; verify commit with `git log -1` and `git status`.
