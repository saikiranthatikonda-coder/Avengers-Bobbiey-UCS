# JARVIS launcher
# First-time setup is automatic: creates .venv, installs deps, copies .env.example.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path .venv)) {
    Write-Host "[jarvis] Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "[jarvis] .env created from template. Add your NEWSAPI_KEY then re-run." -ForegroundColor Yellow
    exit 1
}

$port = if ($env:JARVIS_PORT) { $env:JARVIS_PORT } else { "8765" }
$host_ = if ($env:JARVIS_HOST) { $env:JARVIS_HOST } else { "127.0.0.1" }

Write-Host "[jarvis] Booting on http://$host_`:$port" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -m uvicorn main:app --host $host_ --port $port
