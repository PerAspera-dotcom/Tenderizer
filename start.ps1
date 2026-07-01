# Switch everything on: Postgres (docker) + API (uvicorn) + web (vite).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Starting Postgres..." -ForegroundColor Cyan
docker compose up -d postgres

Write-Host "Starting API + web (Ctrl+C to stop)..." -ForegroundColor Cyan
npm run dev
