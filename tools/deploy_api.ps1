<#
.SYNOPSIS
  Build gems-api.zip from the API/ folder and deploy to the GEMS-API Web App.

.EXAMPLE
  .\tools\deploy_api.ps1 -ResourceGroup GEMS -AppName GEMS-API -AsyncDeploy
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$ResourceGroup,

  [Parameter(Mandatory = $true)]
  [string]$AppName,

  [string]$StartupCommand = "gunicorn main:app --workers 2 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000",

  [switch]$AsyncDeploy
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$apiDir = Join-Path $repoRoot "API"
$zipPath = Join-Path $repoRoot "gems-api.zip"

if (-not (Test-Path $apiDir)) {
  Write-Error "API/ not found at: $apiDir"
}

Write-Host "[1/4] Checking Azure CLI..." -ForegroundColor Cyan
$null = az version 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Error "Azure CLI (az) not on PATH."
}

Write-Host "[2/4] Building $zipPath ..." -ForegroundColor Cyan
Push-Location $apiDir
try {
  Get-ChildItem -Path . -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

  $files = @(
    "main.py",
    "requirements.txt",
    "startup.sh",
    ".deployment",
    ".env.example",
    ".gitattributes",
    "README.md",
    "DEPLOY_AZURE.md"
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
  Write-Error "az webapp deploy failed (exit code $LASTEXITCODE)."
}

Write-Host "    Restarting..." -ForegroundColor DarkGray
az webapp restart --resource-group $ResourceGroup --name $AppName | Out-Null

$domain = az webapp show `
  --resource-group $ResourceGroup `
  --name $AppName `
  --query defaultHostName -o tsv

Write-Host ""
Write-Host "Deployed." -ForegroundColor Green
Write-Host "Open: https://$domain/docs" -ForegroundColor Green
Write-Host "Confirm AUTH0_DOMAIN, AUTH0_AUDIENCE, AUTH0_SWAGGER_CLIENT_ID are set on GEMS-API." -ForegroundColor Yellow
Write-Host "Auth0 callback for Swagger: https://$domain/docs/oauth2-redirect" -ForegroundColor Yellow
Write-Host "Auth0 callback for Python script: http://127.0.0.1:8765/callback" -ForegroundColor Yellow
