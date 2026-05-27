# GEMS Dashboard/API v3 Update Notes (2026-05-27)

**Date:** 2026-05-27  
**Branch merged to `main`:** `dashboard-v2-improvements`  
**Tip commit on `main`:** `c539012` — *update dashboard chat agent*  
**Production URL:** https://gems.bovi-analytics.org  

Related notes:

- `note_api_dashboard.md` — original handoff  
- `note_api_dashboard_v2.md` — full v2 architecture and feature baseline  

This document records **what changed on 2026-05-27** after the custom-domain work on `main` (`5a84abc`). It is a delta on top of v2, not a replacement.

---

## 1. Executive Summary

On 2026-05-27 the team:

1. Finished **Home page live data** (bronze partners, gold studies, affiliation-based map).  
2. Tuned the **Chat agent** (Opus 4.7, tool status UI, removed extra verification LLM pass).  
3. Improved **Home load time** by removing an Opus health ping on every page load.  
4. Documented **Auth0 logout** URLs for the custom domain (`/.auth/logout/complete`).  
5. **Pushed** `dashboard-v2-improvements` and **merged into `main`**.  
6. **Deployed** `gems-dashboard` twice; final deploy reported `RuntimeSuccessful` at https://gems.bovi-analytics.org.

Commits on the feature branch (fast-forward onto `main`):

| Commit | Message |
|--------|---------|
| `b800822` | Improve dashboard map, chat agent, and Opus LLM |
| `5965c37` | update dahsboard with map the live data |
| `c539012` | update dashboard chat agent |

---

## 2. Git and Release State

### 2.1 Remote sync (end of day)

```text
Branch: main (and dashboard-v2-improvements at same tip)
HEAD:   c539012
Remote: origin/main, origin/dashboard-v2-improvements — up to date
Working tree: clean
```

### 2.2 Merge to `main`

`dashboard-v2-improvements` was **3 commits ahead** of `main` at `5a84abc` with no diverging commits on `main`. Merge is a **fast-forward**.

**GitHub (recommended):**

```text
https://github.com/gems-project/gems-api/compare/main...dashboard-v2-improvements
```

**Local:**

```powershell
git checkout main
git pull origin main
git merge dashboard-v2-improvements
git push origin main
```

### 2.3 OneDrive / `.git/objects` warnings

Commits on a OneDrive-synced repo may prompt:

```text
Deletion of directory '.git/objects/XX' failed. Should I try again? (y/n)
```

Answering **`n`** or **`Ctrl+C`** is fine if `git log -1` already shows the new commit. The commit succeeded; only object cleanup failed.

---

## 3. Home Page — Live Data (refined)

**Main file:** `dashboard/app.py`  
**New module:** `dashboard/gems_geography.py`  
**Geocache:** `dashboard/resources/site_geocache.json`  
**Build script:** `dashboard/tools_build_site_geocache.py`  
**Tests:** `tests/test_gems_geography.py`

### 3.1 Stat cards (four tiles)

| Card | Source | Notes |
|------|--------|--------|
| **Partner institutions** | `gems_schema.bronzecontributor` | Distinct `Affiliation`; deduped labels; popover lists names. Bronze = raw ingested contributor records (signed agreements / data-entry links). |
| **Gold studies** | `gold_v1.goldcontributor` | `COUNT(DISTINCT studyId)`; popover lists institutions among gold studies. |
| **Animals** | `bronzeanimalcharacteristics` / `goldanimalcharacteristics` | Distinct study + animal pairs. |
| **Date range** | `goldexperimentaldesign` / `bronzeexperimentaldesign` | Min/max measurement dates. |

All live queries use `@st.cache_data(ttl=3600)` (1 hour).

Environment:

```text
GEMS_CATALOG=gems_catalog
GEMS_SCHEMA=gold_v1
GEMS_BRONZE_SCHEMA=gems_schema
```

### 3.2 Map — gold studies by institution

- **Not** `ExperimentalLocation` for grouping.  
- Uses **`goldcontributor.Affiliation`** with **distinct `studyId` per institution**.  
- Humanized labels via `humanize_affiliation()` (camelCase → readable text).  
- Geocoding via `site_geocache.json` + live Nominatim with **fallback presets** in `gems_geography.py` (e.g. FBN → Dummerstorf, Germany; Università Cattolica → Italy).  
- Caption shows mapped study count vs total gold studies when geocoding is incomplete.

Rebuild geocache after affiliation changes:

```powershell
python dashboard/tools_build_site_geocache.py
```

Then redeploy dashboard (zip must include `gems_geography.py` — listed in `tools/deploy_dashboard.ps1`).

### 3.3 Consortium Members

Unchanged from v2: static logo strip / cards under `dashboard/assets/logos/`.

---

## 4. Home Page — Performance

**Change:** Removed `check_llm_endpoint()` from `dashboard/app.py` startup.

**Before:** Every Home (and every Streamlit) load sent a **Claude Opus** “ping” to Databricks serving endpoints.  
**After:** No LLM call on Home; Chat and Explore/Modeling AI still call the LLM when the user uses those features.

Optional debug restore:

```text
GEMS_CHECK_LLM_ON_STARTUP=1
```

and call `maybe_check_llm_on_startup()` from `app.py` (not enabled by default). See `dashboard/llm_client.py`.

---

## 5. Chat Agent — Updates (2026-05-27)

**Files:** `dashboard/gems_chat.py`, `dashboard/page_chat.py`, `dashboard/llm_client.py`

### 5.1 Model

- Chat uses **`DATABRICKS_LLM_ENDPOINT`** → **`databricks-claude-opus-4-7`** (via `get_llm_model()`).  
- `get_chat_llm_model()` exists for an optional override (`DATABRICKS_CHAT_LLM_ENDPOINT`); **unset in production** so Chat stays on Opus.  
- Databricks Opus endpoints **reject `temperature`**; `chat_completion()` strips it (see `gems_ai.py` / `gems_chat.py`).

### 5.2 Removed: second verification LLM pass

The **`_verify_answer()`** post-check (extra Opus call after the draft answer) was **removed** per product decision. Answers rely on:

- SQL validator (`chat_sql.py`),  
- tool-only data paths,  
- system prompt rules.

### 5.3 Kept from v2 (not slimmed down)

- Full **`data_dictionary.json`** in system prompt (up to ~60k chars).  
- Full **`describe_table`** (row count + null % per column).  
- **`max_iters = 15`** tool rounds.  
- Aggregate-only / allowlisted SQL guardrails (see v2 §11).

### 5.4 New: step status in Chat UI

`page_chat.py` uses `st.status()` with labels driven by `run_agent(..., on_status=...)`:

| Phase | Status text |
|-------|-------------|
| LLM turn | Calling assistant… |
| `list_tables` | Listing tables… |
| `describe_table` | Loading table schema… |
| `run_aggregate_query` | Running SQL on Databricks… |
| `plot` / `render_chart` | Building chart… |
| Final reply | Writing answer… |
| Complete | Done |

No status line for `summarize_last_result` (minor tool).

### 5.5 How tools limit exposure to the LLM (unchanged rule, clarified)

The LLM does **not** connect to Databricks. The server runs tools and returns JSON:

| Tool | Rows to LLM? |
|------|----------------|
| `list_tables` | No — metadata only |
| `describe_table` | No — schema, null %, row count |
| `run_aggregate_query` | Yes — **validated** SQL only; max **50** rows; redacted columns stripped |
| `plot` / `render_chart` | No — chart spec only |

See v2 §11.1 and `dashboard/chat_sql.py` for validator rules.

---

## 6. Auth0 — Sign-Out Fix (custom domain)

**Symptom:** Sign out → Auth0 “Oops!, something went wrong” with  
`post_logout_redirect_uri=https://gems.bovi-analytics.org/.auth/logout/complete`

**Fix:** Add to Auth0 app **Allowed Logout URLs** (client `MHYamdXz76XbRnzxr3ZVLbvL48Sboej2`):

```text
https://gems.bovi-analytics.org/.auth/logout/complete
https://gems-dashboard-aed0h2fufpd8byf6.eastus-01.azurewebsites.net/.auth/logout/complete
```

(Full comma-separated list in `tools/auth0_dashboard_domains.txt` and `docs/auth.md`.)

---

## 7. Deployment (2026-05-27)

### 7.1 Command

```powershell
.\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard -AsyncDeploy
```

### 7.2 Observed results

| Time (UTC) | Zip size | Result |
|------------|----------|--------|
| ~14:07 | ~286077 B | Build successful, site started, **RuntimeSuccessful** |
| ~15:27 | ~285838 B | Build successful, site started, **RuntimeSuccessful** |

App URL after deploy: https://gems.bovi-analytics.org

Post-merge production deploy from `main`:

```powershell
git checkout main
git pull origin main
.\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard -AsyncDeploy
```

---

## 8. Collaborator Communication (draft email)

Short team update sent / to send:

- **URL:** https://gems.bovi-analytics.org  
- Sign in with the **same email** that received the message; verify email.  
- **Home:** live partners (bronze), gold studies, live map.  
- **AI:** Explore/Modeling interpretation; **Chat** on **Opus 4.7** — try data questions.  
- **API:** only users with **API Access** (key generation) in the dashboard can query the API.

Plain-language alternative to “read-only warehouse queries”:

*Chat can search and summarize GEMS data; it cannot modify the database.*

---

## 9. Files Changed (5a84abc → c539012)

```text
dashboard/app.py
dashboard/gems_chat.py
dashboard/gems_geography.py          (new)
dashboard/gems_ai.py
dashboard/llm_client.py
dashboard/page_chat.py
dashboard/tools_build_site_geocache.py
dashboard/resources/site_geocache.json
dashboard/.env.example
dashboard/README.md
docs/auth.md
tools/auth0_dashboard_domains.txt
tools/deploy_dashboard.ps1
tests/test_gems_geography.py         (new)
gems-dashboard.zip
```

---

## 10. Validation Checklist (post–2026-05-27)

1. https://gems.bovi-analytics.org loads; Home stats populate (may lag first load).  
2. Partner popover (bronze) and gold-study popover work.  
3. Map shows gold institutions; FBN/Cattolica pins sensible.  
4. **Sign out** returns to site (no Auth0 error) after logout URLs updated.  
5. **Chat:** status steps update; Opus answers; charts render for visual questions.  
6. No multi-second Opus delay on **Home-only** refresh (no startup ping).  
7. `git log -1 main` → `c539012` after merge.

---

## 11. Operational Mental Model (v3 addendum)

```text
Bronze contributor affiliations  -> Partner institutions card (ingested, agreements)
Gold contributor affiliations    -> Map + gold study institutions (QC'd analysis set)
Opus on Chat only when user asks -> not on every Home page load
Auth0 logout/complete URL        -> required for custom-domain sign-out
main @ c539012                   -> dashboard v2 improvements + 2026-05-27 chat/home polish
```

For full architecture, env vars, API keys, and smoke tests, continue to use **`note_api_dashboard_v2.md`**.
