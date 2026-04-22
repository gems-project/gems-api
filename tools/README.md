# tools/

One-off utility scripts that support the GEMS-API and Dashboard but are not
part of either runtime.

## deploy_dashboard.ps1

After the dashboard Web App has been created in the Portal and its
Environment variables + Authentication are configured (see
[`../dashboard/README.md`](../dashboard/README.md)), this script rebuilds
`../gems-dashboard.zip` from `../dashboard/`, sets the startup command, deploys
via `az webapp deploy`, and restarts the site.

```powershell
.\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard
```

Override the startup command (avoids CRLF issues on Windows) with:

```powershell
.\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard `
  -StartupCommand "streamlit run app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false"
```

Requires `az` on PATH and `az login` + `az account set --subscription <id>`
already done. See [`../API/README.md`](../API/README.md) section 14 for details.

## smoke_test_api.py

End-to-end smoke test against a deployed GEMS-API. Hits `/health`, `/tables`,
`/schema`, `/preview`, `/export` (header row only), and `/query`. Use it after
redeploying the API to confirm the new endpoints are live.

```powershell
python tools/smoke_test_api.py `
  --base https://gems-api-xxxx.azurewebsites.net `
  --key  YOUR_GEMS_API_KEY
```

## check_watermark_columns.py

Inspects every table in `ALLOWED_TABLES` and reports which ones carry a usable
**watermark column** (timestamp, date, int, or bigint) for the dashboard's
incremental-download feature. For tables that don't, it prints the exact
`ALTER TABLE ... SET TBLPROPERTIES (delta.enableChangeDataFeed = true)`
statements you can paste into a Databricks SQL editor so `table_changes(...)`
becomes available instead.

### Run it

From the repo root, with `API/.env` already filled in:

```powershell
# One-time: make sure dependencies are installed in whatever venv you use.
pip install databricks-sql-connector python-dotenv

python tools/check_watermark_columns.py
```

Override the env file location if needed:

```powershell
python tools/check_watermark_columns.py --env-file path/to/other.env
```

Or skip the .env loader and use exported env vars:

```powershell
python tools/check_watermark_columns.py --no-env-file
```

### What it reads

| Variable | Purpose |
|---------|---------|
| `DATABRICKS_HOST` | Warehouse hostname (no `https://`) |
| `DATABRICKS_HTTP_PATH` | Warehouse HTTP path |
| `DATABRICKS_TOKEN` | PAT with `SELECT` on the gold tables |
| `GEMS_CATALOG` | Default `gems_catalog` |
| `GEMS_SCHEMA` | Default `gold_v1` |
| `ALLOWED_TABLES` | Comma-separated list of table names |

### Example output (abridged)

```
# Watermark-column report for `gems_catalog.gold_v1`

Inspecting 22 allowlisted tables...

| Table | # cols | Watermark candidates | Verdict |
|-------|-------:|----------------------|---------|
| `goldbodyweight` | 14 | `ingestion_time` (timestamp), `measurement_date` (date) | OK |
| `goldcontributor` | 9 | (none) | MISSING |
...

## Enable Delta Change Data Feed on tables without a watermark

ALTER TABLE `gems_catalog`.`gold_v1`.`goldcontributor`
  SET TBLPROPERTIES (delta.enableChangeDataFeed = true);
```
