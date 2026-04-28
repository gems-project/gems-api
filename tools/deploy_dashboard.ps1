<#
.SYNOPSIS
  Build gems-dashboard.zip from the dashboard/ folder and deploy it to an
  existing Azure Linux Web App.

.DESCRIPTION
  Run this AFTER the dashboard Web App has been created in the Portal and its
  Environment variables + Authentication are set up (see dashboard/README.md).

  The script:
    1. Packages dashboard/ into ..\gems-dashboard.zip (root-level files only,
       excluding .venv / __pycache__ / .env).
    2. Sets the startup command (default: inline `python -m streamlit ...`, same
       idea as GEMS-API's inline `gunicorn ... --bind 0.0.0.0:8000`).
    3. Runs `az webapp deploy --type zip` (optional `-AsyncDeploy` to skip the
       long sync "Starting the site..." poll).
    4. Restarts the Web App.

.PARAMETER ResourceGroup
  Resource group containing the dashboard Web App.

.PARAMETER AppName
  Dashboard Web App name (e.g. gems-dashboard).

.PARAMETER StartupCommand
  Optional. Override the default inline Streamlit command (relative `app.py`,
  port 8000 — Oryx runs from the app folder after zip extract).

.PARAMETER AsyncDeploy
  If set, passes `--async true` to `az webapp deploy` so the CLI returns after
  upload instead of waiting up to ~10 minutes for the site to become healthy.

.EXAMPLE
  .\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard

.EXAMPLE
  .\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard -AsyncDeploy

.EXAMPLE
  .\tools\deploy_dashboard.ps1 -ResourceGroup GEMS -AppName gems-dashboard `
    -StartupCommand "sh startup.sh"
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$ResourceGroup,

  [Parameter(Mandatory = $true)]
  [string]$AppName,

  [string]$StartupCommand = "python -m streamlit run app.py --server.port 8000 --server.address 0.0.0.0 --server.headless true --browser.gatherUsageStats false",

  [switch]$AsyncDeploy
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$dashboardDir = Join-Path $repoRoot "dashboard"
$zipPath = Join-Path $repoRoot "gems-dashboard.zip"

if (-not (Test-Path $dashboardDir)) {
  Write-Error "dashboard/ not found at: $dashboardDir"
}

Write-Host "[1/4] Checking Azure CLI..." -ForegroundColor Cyan
$null = az version 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Error "Azure CLI (`az`) not on PATH. See API/README.md section 14.1."
}

Write-Host "[2/4] Building $zipPath ..." -ForegroundColor Cyan
Push-Location $dashboardDir
try {
  Get-ChildItem -Path . -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

  $files = @(
    "app.py",
    "gems_auth.py",
    "gems_data.py",
    "gems_ui.py",
    "gems_stats.py",
    "gems_ai.py",
    "gems_chat.py",
    "gems_logo_data.py",
    "gems_api_keys.py",
    "page_explore.py",
    "page_modeling.py",
    "page_chat.py",
    "page_api_access.py",
    "assets",
    ".streamlit",
    "requirements.txt",
    "startup.sh",
    ".deployment",
    ".env.example",
    ".gitattributes",
    "README.md"
  ) | Where-Object { Test-Path $_ }

  if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
  }
  Compress-Archive -Path $files -DestinationPath $zipPath -Force
  Write-Host "    -> $(Get-Item $zipPath | Select-Object -ExpandProperty Length) bytes" -ForegroundColor DarkGray
}
finally {
  Pop-Location
}

Write-Host "[3/4] Setting startup command..." -ForegroundColor Cyan
az webapp config set `
  --resource-group $ResourceGroup `
  --name $AppName `
  --startup-file $StartupCommand | Out-Null

Write-Host "[4/4] Deploying zip to $AppName ..." -ForegroundColor Cyan
if ($AsyncDeploy) {
  az webapp deploy `
    --resource-group $ResourceGroup `
    --name $AppName `
    --src-path $zipPath `
    --type zip `
    --async true
}
else {
  az webapp deploy `
    --resource-group $ResourceGroup `
    --name $AppName `
    --src-path $zipPath `
    --type zip
}
if ($LASTEXITCODE -ne 0) {
  Write-Error "az webapp deploy failed (exit code $LASTEXITCODE). If sync deploy hangs on 'Starting the site...', re-run with -AsyncDeploy, then check Log stream in the Portal."
}

Write-Host "    Restarting..." -ForegroundColor DarkGray
az webapp restart --resource-group $ResourceGroup --name $AppName | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Error "az webapp restart failed (exit code $LASTEXITCODE)."
}

$domain = az webapp show `
  --resource-group $ResourceGroup `
  --name $AppName `
  --query defaultHostName -o tsv

Write-Host ""
if ($AsyncDeploy) {
  Write-Host "Zip upload accepted (async). Site may take a few minutes to start; verify in Portal Log stream if needed." -ForegroundColor Green
}
else {
  Write-Host "Deployed." -ForegroundColor Green
}
Write-Host "Open: https://$domain/" -ForegroundColor Green
Write-Host "You will be redirected to Auth0 sign-in if Authentication is enabled."
