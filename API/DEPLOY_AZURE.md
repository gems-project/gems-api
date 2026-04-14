# Azure deployment — quick reference

The **full story** (architecture, Mermaid workflows, local setup, portal steps, troubleshooting, collaborator handoff) is in **`README.md`** in this folder. Use that document first.

This file keeps **copy-paste** commands and tables for operators who already know the flow.

---

## Application settings (same names as `API/.env`)

| Name | Value | Notes |
|------|--------|-------|
| `DATABRICKS_HOST` | `adb-....azuredatabricks.net` | No `https://` |
| `DATABRICKS_HTTP_PATH` | `/sql/1.0/warehouses/...` | From warehouse connection details |
| `DATABRICKS_TOKEN` | `dapi-...` | PAT |
| `GEMS_CATALOG` | `gems_catalog` | Adjust if needed |
| `GEMS_SCHEMA` | `gems_schema` | Adjust if needed |
| `ALLOWED_TABLES` | `table1,table2` | Comma-separated |
| `GEMS_API_KEY` | long random secret | Sent as header `X-API-Key` |
| `MAX_EXPORT_ROWS` | `100000` | Optional |

**Portal:** Web App → **Environment variables** → **App settings** → **+ Add** → **Save**.

---

## Startup command

**Option A**

```bash
bash startup.sh
```

**Option B** (if bash/CRLF causes issues)

```bash
gunicorn main:app --workers 2 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

**Azure CLI** (replace resource group and app name)

```bash
az webapp config set --resource-group GEMS --name GEMS-API --startup-file "gunicorn main:app --workers 2 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000"
az webapp config show --resource-group GEMS --name GEMS-API --query appCommandLine -o tsv
az webapp restart --resource-group GEMS --name GEMS-API
```

---

## Zip deploy (PowerShell)

From the **`API`** folder (do **not** include `.env`, `.venv`, `__pycache__`).

**Minimal (first deploy):**

```powershell
cd API
Compress-Archive -Force -Path main.py,requirements.txt,startup.sh,.deployment,.env.example -DestinationPath ..\gems-api.zip
```

**With optional files:**

```powershell
cd "...\data-entry-template\API"
$files = @('main.py','requirements.txt','startup.sh','.deployment','.env.example')
if (Test-Path '.gitattributes') { $files += '.gitattributes' }
Compress-Archive -Force -Path $files -DestinationPath ..\gems-api.zip
```

From the parent folder (where `gems-api.zip` lives):

```powershell
az webapp deploy --resource-group YOUR_RG --name YOUR_APP --src-path .\gems-api.zip --type zip
```

**Without CLI:** Portal → Web App → **Advanced Tools** → **Go** → **`https://<app>.scm.azurewebsites.net/ZipDeploy`** or **File Manager** → `site/wwwroot` (zip drag-and-drop). Or **Azure App Service** extension in VS Code/Cursor. Details: **`README.md` §8 D.2–D.3**.

---

## Smoke tests after deploy

1. `https://<default-domain-from-overview>/docs`
2. `GET /health`
3. `GET /tables` with header **`X-API-Key`**
4. `GET /export/<table>.csv` with same header

---

## Networking

Outbound from App Service to `*.azuredatabricks.net` usually works on the public internet. If the workspace uses **IP allow lists** or **Private Link**, allow the Web App’s **outbound IPs** (or integration subnet) on the Databricks side.

---

## Optional hardening

- Key Vault references for `DATABRICKS_TOKEN` and `GEMS_API_KEY`
- Custom domain + managed TLS
- Separate PAT per environment and a rotation policy
