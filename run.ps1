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

# Read JARVIS_HOST / JARVIS_PORT from .env so editing .env controls the bind
# address (set JARVIS_HOST=0.0.0.0 there to let fleet nodes on the LAN connect).
# An explicit environment variable still wins over the .env value.
$envHost = $null; $envPort = $null
if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^\s*JARVIS_HOST\s*=\s*(.+?)\s*$') { $envHost = $matches[1].Trim() }
        if ($_ -match '^\s*JARVIS_PORT\s*=\s*(.+?)\s*$') { $envPort = $matches[1].Trim() }
    }
}
$port = if ($env:JARVIS_PORT) { $env:JARVIS_PORT } elseif ($envPort) { $envPort } else { "8765" }
$host_ = if ($env:JARVIS_HOST) { $env:JARVIS_HOST } elseif ($envHost) { $envHost } else { "127.0.0.1" }

if ($host_ -eq "0.0.0.0") {
    $lan = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceAlias -notmatch "Loopback|vEthernet|WSL" -and $_.IPAddress -notmatch "^169\." } |
        Select-Object -ExpandProperty IPAddress) -join ", "
    Write-Host "[jarvis] Booting on 0.0.0.0:$port (LAN-reachable). Nodes/browsers use: $lan" -ForegroundColor Cyan
} else {
    Write-Host "[jarvis] Booting on http://${host_}:$port" -ForegroundColor Cyan
}
& .\.venv\Scripts\python.exe -m uvicorn main:app --host $host_ --port $port
