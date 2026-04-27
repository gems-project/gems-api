# GEMS Dashboard Build and Operations Note

## Purpose

This note captures the full journey of building and stabilizing the `dashboard/` app: local setup, Azure deployment, major failures, root causes, fixes, and the final operating model (authentication + data access).

It is intentionally detailed and meant as an internal project memory.

## Project Scope and Initial Goal

The dashboard was built as a standalone Streamlit app for:

- browsing allowlisted Databricks gold tables,
- plotting and interpreting data,
- fitting statistical models,
- chatting over data with guardrailed SQL,
- downloading full or incremental CSVs.

Design requirement: dashboard should not depend on the API web app runtime. It connects directly to Databricks SQL.

## Timeline of What Happened

### 1) Initial app build and local validation

The base app structure was created under `dashboard/` with:

- one home entry file (`app.py`),
- helper modules (`gems_*.py`),
- page scripts (`page_*.py`),
- assets, requirements, and startup script.

Local testing worked with `.env` values and `streamlit run app.py`.

### 2) First Azure deployment problems (imports/pages)

#### Symptoms

- Deploy command reported success, but runtime threw `ModuleNotFoundError`.
- Page discovery was inconsistent (sidebar pages missing).
- HTML sections occasionally rendered as raw source text.

#### Root causes

- Azure Linux Oryx zip extraction/runtime layout did not always preserve nested module assumptions (earlier package-style layout like `lib/` or `gemslib/`).
- Streamlit automatic multipage behavior tied to `pages/` was brittle in this deployment pattern.
- Indented HTML in markdown can be interpreted as code block.

#### Fixes that worked

- Flattened to root-level modules: `gems_auth.py`, `gems_data.py`, `gems_ui.py`, etc.
- Switched to explicit navigation in `app.py` with root-level page scripts (`page_explore.py`, etc.).
- Used dedented or controlled HTML rendering patterns to avoid markdown code-block behavior.

### 3) Packaging and deployment hardening

#### What was done

- Added/updated `tools/deploy_dashboard.ps1` to:
  - build `gems-dashboard.zip`,
  - set startup command,
  - deploy via `az webapp deploy`,
  - restart app.
- Added `-AsyncDeploy` mode for cases where sync deploy appears stuck on "Starting the site...".
- Ensured zip includes all required root files and assets.

#### Operational clarification

Azure "deploy success" can still coexist with app runtime error; deploy success confirms upload/build/start workflow, not app logic correctness.

### 4) Data-page behavior fixes

#### Explore and Modeling join issues

Symptoms:

- dtype mismatch (`object` vs `float64`) on join keys,
- need for left/inner/outer options and multiple join keys.

Fixes:

- join-key normalization before merge,
- multi-key support,
- join-type controls,
- aligned logic between Explore and Modeling.

#### Explore chart + AI issue

Symptoms:

- chart disappeared after interpretation action.

Fix:

- persisted chart figure in `st.session_state` and redrew after AI interpretation.

### 5) Home hero / mission / logos stabilization

This was the longest visual stabilization cycle.

#### Symptoms experienced

- logos not showing in Azure though text rendered,
- mission area looked messy/raw at times,
- large blank space below banner,
- logo color/contrast mismatches.

#### Root causes discovered

- `data:` image behavior differs between render contexts (iframe/CSP/sandbox behavior can differ from top-level doc rendering).
- filesystem-based asset lookup can fail under Oryx layout assumptions.
- one source logo had non-transparent white background, making CSS white-silhouette filtering render as a white block.

#### Final working approach

- Embedded logo bytes in `dashboard/gems_logo_data.py` so logo availability no longer depends on runtime filesystem.
- Added `tools/embed_logos.py` generator to regenerate embedded data from `dashboard/assets/`.
- For `gems_logo.png`, converted white background to transparent during embedding.
- Rendered logo group and mission box in the hero layout with tuned spacing/contrast.

Result: stable logos + mission appearance in production.

### 6) Authentication and access-control model

Decision made:

- Keep **App Service Authentication (Easy Auth)** enabled at the Azure edge so Streamlit does not implement OAuth/OIDC itself.
- Use **Auth0** as the **OpenID Connect identity provider** behind Easy Auth (social logins such as Google/GitHub/Apple are configured in Auth0).
- Restrict data pages in-app via allowlist (`ALLOWED_USERS` / `ALLOWED_DOMAINS`).

Implementation:

- `gems_auth.py` now supports:
  - `ALLOWED_USERS` (exact UPN/email list),
  - `ALLOWED_DOMAINS` (domain list),
  - `require_authorized_user()` gate.
- Home page remains visible; data pages (Explore/Modeling/Chat/Download) enforce authorization.

This matches the requirement: users may enter app shell, but only allowlisted users access data features.

Historical note (superseded): an earlier iteration used the built-in **Microsoft Entra ID** Easy Auth provider for Cornell tenant sign-in. The current production model uses **Auth0 OIDC + Easy Auth** so collaborators can authenticate with common social providers without changing Cornell central Entra settings.

## Major Issues and What Worked

### Issue: Import/module failures after deploy

- Worked: root-level module layout and explicit file packaging.
- Did not work reliably: dependency on deeper package structure in deployed runtime.

### Issue: Missing pages on server

- Worked: explicit `st.navigation` + root-level `page_*.py`.
- Less reliable in this environment: assuming default `pages/` discovery.

### Issue: Logos missing intermittently

- Worked: embedded logo data module + transparency processing for GEMS logo.
- Less reliable: runtime filesystem lookups and some earlier render contexts.

### Issue: Merge/join failures due to key dtype mismatch

- Worked: normalize join keys before merge, support multi-key joins.

### Issue: Git commit terminal spam (`Deletion of directory '.git/objects/*' failed`)

- Root cause: OneDrive lock contention with Git object cleanup.
- Worked: continue with `n`/exit, verify commit via `git log`; commit itself was successful.

## Final Operating Model (Current)

### Access model

- **Easy Auth** handles the web-app authentication edge (redirects, session cookies, logout endpoint `/.auth/logout`).
- **Auth0** is the OIDC identity provider configured inside Easy Auth (social logins live in Auth0).
- **In-app gate** controls data access (`ALLOWED_USERS` / `ALLOWED_DOMAINS`) after Easy Auth has established identity headers for Streamlit.

### Deployment model

- Local edits only.
- Deploy via `tools/deploy_dashboard.ps1`.
- `-AsyncDeploy` if sync polling is slow.

### Recommended environment variables for access

- `ALLOWED_USERS` for exact user control.
- `ALLOWED_DOMAINS` optional broad domain rule.
- Leave both empty only if unrestricted data access is intentionally desired.

## File Inventory: What Each Python File Does

### Dashboard app files (`dashboard/*.py`)

- `app.py`  
  Home page, global navigation registration, hero/banner layout, top-level app wiring.

- `page_explore.py`  
  Table exploration, joins, charting, AI chart interpretation workflow.

- `page_modeling.py`  
  Modeling UI and workflows (OLS/MixedLM), model metrics and interpretation.

- `page_chat.py`  
  Chat interface over data using guarded SQL execution path.

- `page_download.py`  
  Full/incremental CSV download UI and watermark-aware export flow.

- `gems_data.py`  
  Databricks SQL connectivity and data-access helpers (schemas, previews, exports, allowlist-aware queries).

- `gems_stats.py`  
  Statistical model helper functions and result summarization utilities.

- `gems_ai.py`  
  AI prompt helpers for plot/model interpretation.

- `gems_chat.py`  
  Chat orchestration and SQL safety/validation logic.

- `gems_auth.py`  
  Current-user extraction from Easy Auth headers and allowlist gate (`require_authorized_user`).

- `gems_ui.py`  
  Shared Streamlit styling/theme/render helpers and sidebar user display helpers.

- `gems_watermarks.py`  
  Azure Table Storage watermark persistence for incremental download.

- `gems_logo_data.py`  
  Embedded base64 logo payloads used for stable rendering in production.

### Tooling scripts (`tools/*.py` used for dashboard/API operations)

- `tools/embed_logos.py`  
  Regenerates `dashboard/gems_logo_data.py` from `dashboard/assets/*.png`; applies GEMS transparency processing.

- `tools/debug_env.py`  
  Environment diagnostics helper.

- `tools/check_watermark_columns.py`  
  Utility for watermark column validation checks.

- `tools/list_gold_tables.py`  
  Utility to list available gold tables.

- `tools/smoke_test_api.py`  
  API smoke test helper.

### Deployment tool (`tools/*.ps1`)

- `tools/deploy_dashboard.ps1`  
  Official dashboard deploy script: package, configure startup, deploy, restart.

## Azure Build and Deployment Procedure (Detailed)

This section is the full "how we built it in Azure" procedure, including what
was created, why each resource exists, and how authentication/authorization are
enforced.

### A) Azure resources created and purpose

#### 1) Web App: `gems-dashboard`

- **Resource type:** Azure App Service (Linux Web App)
- **Purpose:** Host and run the Streamlit dashboard runtime (`app.py` + pages).
- **Why this resource:** Provides managed Python runtime, **App Service Authentication (Easy Auth)** integration, app settings, deployment slots/logs, and URL hosting.

Key configuration choices used:

- Publish model: Code
- Runtime: Python 3.12 (3.11 also valid)
- OS: Linux
- App name: `gems-dashboard`
- Resource group: `GEMS`

#### 2) Storage account + Table: `gemsDownloadWatermarks`

- **Resource type:** Azure Storage Account (Table service)
- **Table name:** `gemsDownloadWatermarks`
- **Purpose:** Persist per-user watermark checkpoints for incremental CSV
  downloads.
- **Why this resource:** Lets Download page return only rows newer than a
  user's last export position, instead of forcing full table download each time.

How it is used by the app:

- `dashboard/gems_watermarks.py` reads/writes watermark records by user/table.
- `dashboard/page_download.py` offers full vs incremental export behavior.

#### 3) Auth0 application + Easy Auth identity provider wiring

With **Auth0 as the OIDC provider** behind Easy Auth, the important objects are:

- **Auth0 Tenant** (lab tenant): owns user directories/connections and the Auth0 Application used by Azure.
- **Auth0 Application (Regular Web)**: holds callback/logout URL allowlists and client credentials used by Azure’s OIDC provider configuration.
- **Azure App Service Authentication provider entry** (custom OpenID Connect): stores metadata URL, client ID, and a reference to the client secret in app settings.

Purpose:

- Handle sign-in (Easy Auth) before requests hit Streamlit.
- Expose identity headers to app (`X-MS-CLIENT-PRINCIPAL-NAME`).

### B) Authentication and Authorization model (final state)

This project ended with a two-layer access design:

#### Layer 1: Sign-in authentication (Azure Easy Auth + Auth0 OIDC)

- Enforced at App Service edge (before Python code).
- If not signed in, Easy Auth redirects the browser to **Auth0** (not Cornell central Entra).
- Auth0 completes social/provider login and returns through Easy Auth’s callback route.
- Easy Auth establishes the **App Service session** (browser cookies) and passes trusted identity headers to Streamlit.

Operational notes:

- Use the Web App **Default domain** everywhere (Azure may assign a non-obvious hostname). Mismatched hostnames break DNS and break Auth0 callback/logout URL allowlists.
- In Authentication settings, set **Redirect to** the Auth0 custom provider (not Microsoft) if Microsoft is still registered but unused.
- Auth0 **Allowed Callback URLs** must include:
  - `https://<default-domain>/.auth/login/<provider-name>/callback`
- Auth0 **Allowed Logout URLs** should include the dashboard origin and common Easy Auth logout completion paths (comma-separated, each URL intact on one line), for example:
  - `https://<default-domain>/`
  - `https://<default-domain>/.auth/logout`
  - `https://<default-domain>/.auth/logout/complete`

#### Layer 2: In-app data authorization (dashboard code)

- Enforced inside dashboard pages through `require_authorized_user()`.
- Home page remains visible to signed-in users.
- Data pages (Explore/Modeling/Chat/Download) require allowlist match.

Control variables:

- `ALLOWED_USERS` (exact email/UPN list)
- `ALLOWED_DOMAINS` (domain list)

Policy:

- User is authorized if in `ALLOWED_USERS` OR domain in `ALLOWED_DOMAINS`.
- If both vars are empty, access is open to any signed-in user.

### C) Azure Portal setup procedure (step-by-step)

#### Step 1: Create the web app (`gems-dashboard`)

1. Azure Portal -> Create Resource -> Web App.
2. Select subscription/resource group (`GEMS`).
3. Name app `gems-dashboard`.
4. Runtime Python 3.12, Linux.
5. Create.

#### Step 2: Enable App Service Authentication

1. Open `gems-dashboard` (Web App resource).
2. Settings -> Authentication.
3. Turn **App Service authentication** to **Enabled**.
4. Set **Restrict access** / unauthenticated behavior to **Require authentication** (302 redirect is typical for websites).
5. Add identity provider -> **OpenID Connect** (Auth0).
6. Set **Redirect to** the Auth0 provider (custom OIDC), not Microsoft, if both providers exist.
7. Remove/disable the **Microsoft** provider once Auth0 is verified, to avoid accidental Microsoft-first redirects.

Outcome:

- Users authenticate via **Auth0**; Easy Auth injects identity headers for Streamlit.

#### Step 3: Create storage table for incremental downloads

1. Create Storage Account (if needed).
2. Storage account -> Data storage -> Tables -> + Table.
3. Create table `gemsDownloadWatermarks`.
4. Copy storage connection string (Access keys) for app settings.

#### Step 4: Set Web App environment variables

Web App -> Settings -> Environment variables -> App settings:

- Databricks:
  - `DATABRICKS_HOST`
  - `DATABRICKS_HTTP_PATH`
  - `DATABRICKS_TOKEN`
  - `GEMS_CATALOG`
  - `GEMS_SCHEMA`
  - `ALLOWED_TABLES`
- OpenAI:
  - `OPENAI_API_KEY`
  - `OPENAI_MODEL`
  - `OPENAI_CHAT_MODEL`
- Watermarks:
  - `AZURE_TABLES_CONNECTION_STRING`
  - `AZURE_TABLES_NAME=gemsDownloadWatermarks`
- App-level authorization:
  - `ALLOWED_USERS` (comma-separated)
  - optional `ALLOWED_DOMAINS`

Apply changes (App Service restarts automatically).

### D) Deployment procedure used (code to running app)

#### Preferred deployment command

From repository root:

`.\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard -AsyncDeploy`

What this script does:

1. Builds `gems-dashboard.zip` from selected `dashboard/` files.
2. Sets startup command:
   `python -m streamlit run app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false`
3. Deploys zip via Azure CLI.
4. Restarts web app.
5. Prints default URL.

#### Why `-AsyncDeploy` was important

- Sync deployment occasionally appears stuck in "Starting the site..." polling.
- Async avoids long blocking wait and still uploads/deploys correctly.
- Verification done via portal logs/default URL health.

### E) Verification checklist after deployment

1. Open default URL for `gems-dashboard`.
2. Confirm **Auth0** sign-in redirect occurs (social buttons as configured in Auth0).
3. Confirm Home page renders correctly (hero/logos, no raw HTML).
4. Confirm data page authorization behavior:
   - allowlisted user: data pages open.
   - non-allowlisted signed-in user: blocked on data pages with clear message.
5. Confirm Databricks connection loads table lists.
6. Confirm incremental download mode works when watermark settings are present.

### F) Collaborator onboarding procedure (current model)

Because sign-in is handled by **Auth0**, onboarding is primarily:

1. Enable the desired connection(s) in Auth0 (Google/GitHub/Apple/etc.) for the dashboard Auth0 Application.
2. Ask the collaborator to sign in once and confirm which **email** Auth0 presents to the app (this should match what appears in the dashboard sidebar).
3. Add that email to `ALLOWED_USERS` (or add their domain to `ALLOWED_DOMAINS` if appropriate) in Web App app settings.
4. Ask the collaborator to refresh and verify data-page access.

Note:

- Cornell central Entra directory changes are **not required** for this model.
- If you need stricter “only these people may even reach the app shell”, that is an additional Auth0/Easy Auth policy beyond the current allowlist approach.

### G) Future hardening note: tightening who may authenticate

If the project later needs to restrict authentication itself (not just data pages), options include:

- Auth0 **Rules/Actions** or organization policies to block unknown emails/domains before issuing tokens.
- Easy Auth additional restrictions (depending on provider capabilities and operational needs).

Rollout guidance remains the same: tighten gradually, keep a break-glass admin path, and test with one internal + one external collaborator before broad changes.

## Practical Runbooks

### Local run

1. `cd dashboard`
2. `copy .env.example .env`
3. Fill required env vars.
4. `python -m venv .venv`
5. `.venv\Scripts\activate`
6. `pip install -r requirements.txt`
7. `streamlit run app.py`

### Azure deploy

From repo root:

`.\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard -AsyncDeploy`

### Set data authorization

Portal -> Web App -> Environment variables:

- `ALLOWED_USERS=user1@cornell.edu,user2@cornell.edu`
- optional `ALLOWED_DOMAINS=cornell.edu`

Apply to restart app.

## Lessons Learned

- Keep deployment layout simple and explicit for App Service Linux.
- Prefer deterministic packaging over implicit discovery.
- Separate "can sign in" from "can access data" to match collaboration workflows.
- For brand assets in cloud environments, eliminate runtime path assumptions when possible.
- In OneDrive-backed repos, Git housekeeping warnings may be noisy but non-fatal; verify commit state explicitly.

## Change Log (date-by-date)

### 2026-04-27

- **Authentication model updated (Auth0 + Easy Auth)**
  - Replaced the operational documentation sections that assumed **Microsoft Entra ID** as the Easy Auth provider with the current model: **Azure App Service Authentication (Easy Auth) + Auth0 OpenID Connect**.
  - Documented practical setup notes: use the Web App **Default domain** for Auth0 callback/logout allowlists, set Easy Auth **Redirect to** Auth0 when multiple providers exist, and validate logout URLs as comma-separated single-line entries in Auth0.
  - Updated collaborator onboarding to match Auth0-based identities (no Cornell tenant guest-invite dependency for basic access).

- **Home-only sign-out affordance**
  - Added a Home page sidebar **Sign out** link to `/.auth/logout` in `dashboard/app.py` (Easy Auth logout entrypoint).

### 2026-04-22

- **Dashboard visual stabilization completed**
  - Fixed hero/mission formatting and layout spacing.
  - Resolved missing logos in Azure by switching to embedded logo data (`gems_logo_data.py`) and generator tooling (`tools/embed_logos.py`).
  - Updated hero design: logos inside banner, improved contrast/spacing, and mission box alignment.
  - Files touched (major): `dashboard/app.py`, `dashboard/gems_logo_data.py`, `tools/embed_logos.py`, `tools/deploy_dashboard.ps1`.

- **Data-page behavior hardening**
  - Implemented/standardized multi-key joins and join type handling in Explore/Modeling.
  - Added join-key normalization to prevent dtype merge errors.
  - Kept chart visible after AI interpretation via session-state persistence.
  - Files touched (major): `dashboard/page_explore.py`, `dashboard/page_modeling.py`.

- **Access control model implemented**
  - Added in-app allowlist logic (`ALLOWED_USERS` / `ALLOWED_DOMAINS`) in `gems_auth.py`.
  - Applied gate to data pages while keeping home page accessible for signed-in users.
  - Added user-facing unauthorized messaging with home/sign-out options.
  - Updated env documentation in `.env.example`.
  - Files touched (major): `dashboard/gems_auth.py`, `dashboard/app.py`, `dashboard/page_explore.py`, `dashboard/page_modeling.py`, `dashboard/page_chat.py`, `dashboard/page_download.py`, `dashboard/.env.example`.

- **Documentation update**
  - Added detailed historical note (`dashboard/note.md`) and concise operational README (`dashboard/README.md`).
  - Added `dashboard/note` pointer file.
  - Files touched: `dashboard/note.md`, `dashboard/README.md`, `dashboard/note`.

