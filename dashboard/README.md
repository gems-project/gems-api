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

Optional: set `LOCAL_DEV_USER` to an email in `ALLOWED_USERS` to exercise data pages
locally. If `LOCAL_DEV_USER` is unset, Home behaves as a public visitor and data
pages show the Sign in gate.

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
   - API access: `AZURE_TABLES_CONNECTION_STRING`, `AZURE_API_KEYS_TABLE`, `API_KEY_PEPPER`, `GEMS_API_BASE_URL`, `DASHBOARD_API_AUTHZ_SECRET`
   - Dashboard data access gate: `ALLOWED_USERS`
   - Optional Auth0 verification resend: `AUTH0_DOMAIN` plus either `AUTH0_MANAGEMENT_API_TOKEN` or both `AUTH0_MANAGEMENT_CLIENT_ID` and `AUTH0_MANAGEMENT_CLIENT_SECRET`. The Management API application needs permission to create verification email jobs (`create:user_tickets` / verification email job access in Auth0 Management API). If these are omitted, the dashboard shows a friendly message and asks users to use the original Auth0 verification email or contact an administrator.

## Access Control Model (Current)

Two layers:

1. **Azure Easy Auth + Auth0** — establishes browser identity (optional for Home).
2. **`ALLOWED_USERS`** — after sign-in, gates Explore / Modeling / Chat. API Access also needs `GEMS-API.ALLOWED_API_USERS`.

### Public Home (intended production setting)

- **Home** is visible without signing in.
- In Azure Portal → Web App `gems-dashboard` → **Authentication**:
  - Keep Auth0 / Easy Auth **Enabled**.
  - Set unauthenticated requests to **Allow anonymous** (not “Require authentication”).
  - Keep Auth0 as the identity provider for Sign in (`/.auth/login/auth0`).
- Deploy the dashboard code that shows Sign in on Home and blocks data pages until Auth0 + allowlist.

Until that Easy Auth setting is changed, Azure still forces Auth0 before any page (including Home).

### After sign-in

- Anyone may complete Auth0 signup/login and email verification.
- `gems-dashboard.ALLOWED_USERS` controls Explore / Modeling / Chat access.
- `GEMS-API.ALLOWED_API_USERS` controls API Access page visibility, API-key generation, and whether the API service accepts the user.
- Existing allowlisted users keep access; they do not need new emails or re-verification if already verified.
- Explore/Modeling/Chat call `require_authorized_user()` (Sign in required if anonymous; allowlist if signed in).

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
- **Unauthorized on data pages:** check `ALLOWED_USERS`.
- **Dashboard user cannot access API page:** check `ALLOWED_API_USERS` on `gems-api` and confirm `DASHBOARD_API_AUTHZ_SECRET` matches in both apps.
- **Git object cleanup prompts on Windows/OneDrive:** usually non-fatal; verify commit with `git log -1` and `git status`.
- **Home page feels slow:** first load runs several cached Databricks stat queries (1 h TTL). The LLM is **not** pinged on Home anymore; Chat/Explore AI run the endpoint only when you use them. Do not set `GEMS_CHECK_LLM_ON_STARTUP=1` in production unless debugging.
- **Chat feels slow:** Chat uses `DATABRICKS_LLM_ENDPOINT` (Opus) with multiple tool rounds per question; larger models are slower, not faster.

## Update — Public Home (2026-08-17)

**Goal:** Anyone who opens https://gems.bovi-analytics.org can see the **Home** page without Auth0. Data pages stay restricted.

**Azure Easy Auth (Portal → gems-dashboard → Authentication → Edit):**

- App Service authentication: **Enabled** (unchanged)
- Restrict access: **Allow unauthenticated access** (was Require authentication)
- Keep **auth0** as the Sign in provider
- Do **not** remove Auth0 or change `ALLOWED_USERS` for this update

**App behavior after deploy + that setting:**

| Visitor state | Home | Explore / Modeling / Chat / API Access |
|---------------|------|----------------------------------------|
| Not signed in | Public overview + Sign in CTA | Sign in required |
| Signed in, email not in `ALLOWED_USERS` | Warning: need admin approval | Blocked |
| Signed in, verified email in `ALLOWED_USERS` | Full-access caption | Allowed (API Access also needs `ALLOWED_API_USERS`) |

**Messaging:** Home explains that users may sign in, but data pages still need administrator approval and a verified email.

**Existing allowlisted users:** No email changes and no re-verification if already verified. They Sign in with the same Auth0 account when the session expires.

**Code touched for this update:** `dashboard/gems_auth.py`, `dashboard/app.py` (Home sidebar), plus notes in this README / `.env.example`.

**Smoke test:**

1. Incognito → Home loads without Auth0
2. Open Explore → Sign in prompt
3. Non-allowlisted login → Home warning; data pages blocked
4. Allowlisted login → data pages work as before
