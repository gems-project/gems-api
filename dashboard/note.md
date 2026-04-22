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

- Keep app sign-in broadly accessible to valid tenant/guest identities under current Easy Auth setup.
- Restrict data pages in-app via allowlist.

Implementation:

- `gems_auth.py` now supports:
  - `ALLOWED_USERS` (exact UPN/email list),
  - `ALLOWED_DOMAINS` (domain list),
  - `require_authorized_user()` gate.
- Home page remains visible; data pages (Explore/Modeling/Chat/Download) enforce authorization.

This matches the requirement: users may enter app shell, but only allowlisted users access data features.

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

- Easy Auth handles sign-in.
- `Assignment required = No` means sign-in is not restricted by Enterprise App assignments.
- In-app gate controls data access (`ALLOWED_USERS` / `ALLOWED_DOMAINS`).

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
- **Why this resource:** Provides managed Python runtime, Entra integration
  (Easy Auth), app settings, deployment slots/logs, and URL hosting.

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

#### 3) Microsoft Entra app objects (created by Authentication setup)

Enabling App Service Authentication with Microsoft creates/uses:

- **App Registration** (application definition),
- **Enterprise Application** (service principal instance in tenant).

Purpose:

- Handle sign-in (Easy Auth) before requests hit Streamlit.
- Expose identity headers to app (`X-MS-CLIENT-PRINCIPAL-NAME`).

### B) Authentication and Authorization model (final state)

This project ended with a two-layer access design:

#### Layer 1: Sign-in authentication (Azure Easy Auth / Entra)

- Enforced at App Service edge (before Python code).
- If not signed in, user is redirected to Microsoft login.

Current decisions:

- App registration supported account type: **My organization only** (single tenant).
- Enterprise application `Assignment required`: **No**.

Meaning:

- Cornell tenant users can sign in.
- Invited guest users in Cornell tenant can sign in.
- No pre-assignment required for sign-in itself.

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
3. Add identity provider -> Microsoft.
4. Require authentication (redirect unauthenticated users to Microsoft login).

Outcome:

- App Registration + Enterprise Application objects are available in Entra.

#### Step 3: Confirm Enterprise App policy

1. Microsoft Entra ID -> Enterprise applications -> `gems-dashboard`.
2. Properties -> `Assignment required?`
3. Set to **No** for current model (sign-in open to valid tenant/guest users).

#### Step 4: Create storage table for incremental downloads

1. Create Storage Account (if needed).
2. Storage account -> Data storage -> Tables -> + Table.
3. Create table `gemsDownloadWatermarks`.
4. Copy storage connection string (Access keys) for app settings.

#### Step 5: Set Web App environment variables

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
2. Confirm Microsoft sign-in redirect occurs.
3. Confirm Home page renders correctly (hero/logos, no raw HTML).
4. Confirm data page authorization behavior:
   - allowlisted user: data pages open.
   - non-allowlisted signed-in user: blocked on data pages with clear message.
5. Confirm Databricks connection loads table lists.
6. Confirm incremental download mode works when watermark settings are present.

### F) Guest user onboarding procedure (current model)

If user is external to Cornell tenant:

1. Microsoft Entra ID -> Users -> New user -> Invite external user.
2. User accepts invitation email.
3. Add their sign-in identity to `ALLOWED_USERS` in Web App app settings.
4. Ask user to sign in and verify data-page access.

Note:

- Large guest list in tenant-wide Users view is normal and not app-specific.
- Enterprise app Users/Groups list is assignment metadata; with
  `Assignment required = No`, it is not the primary data-access control.

### G) Future hardening note: switching to "specific identities"

If we later switch Web App Authentication -> Microsoft provider ->
`Identity requirement` from "Allow requests from any identity" to
"Allow requests from specific identities", use this safe rollout:

1. First have target users sign in at least once while permissive mode is active
   (or otherwise confirm their Entra identity objects exist).
2. Add those exact identities to the specific-identities list.
3. Keep at least one break-glass admin account in the list.
4. Then switch to specific identities and test with one internal + one guest user.

Important:

- This mode depends on valid Entra identity objects/tokens, not on invitation
  email delivery itself.
- If invitation emails are delayed/filtered, existing confirmed identity objects
  can still be allowlisted directly.

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

