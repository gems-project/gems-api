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

