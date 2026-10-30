# BUCS launcher
# First-time setup is automatic: checks Python, creates .venv, installs deps,
# copies .env.example, adds a desktop shortcut. Then BUCS starts.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path .venv)) {
    # Python 3.11+ must be installed (the Microsoft Store "python" stub doesn't count)
    $pyOk = $false
    try { $pyOk = ((& python -c "import sys; print(sys.version_info >= (3, 11))" 2>$null) -eq "True") } catch {}
    if (-not $pyOk) {
        Write-Host ""
        Write-Host "[BUCS] Python 3.11 or newer is required." -ForegroundColor Yellow
        Write-Host "[BUCS] Install it from python.org (tick 'Add python.exe to PATH'), then run Start BUCS again." -ForegroundColor Yellow
        Start-Process "https://www.python.org/downloads/"
        exit 1
    }
    Write-Host "[BUCS] First launch: setting up (about 3-5 minutes)..." -ForegroundColor Cyan
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue   # retry cleanly next launch
        Write-Host "[BUCS] Installing dependencies failed. Check your internet connection and run Start BUCS again." -ForegroundColor Red
        exit 1
    }
    # desktop shortcut "BUCS" -> Start BUCS.cmd in this folder
    try {
        $lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "BUCS.lnk"
        if (-not (Test-Path $lnk)) {
            $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
            $sc.TargetPath = Join-Path $PSScriptRoot "Start BUCS.cmd"
            $sc.WorkingDirectory = $PSScriptRoot
            $sc.Save()
            Write-Host "[BUCS] Desktop shortcut created: BUCS" -ForegroundColor Cyan
        }
    } catch {}
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env          # defaults work out of the box (news needs no key)
    Write-Host "[BUCS] .env created from the template." -ForegroundColor Cyan
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
# Server loop. The dashboard can ask the launcher (via .update\*.flag) to:
#   restart  - relaunch after an in-app update (updater.py)
#   shutdown - stop and close the window (exit code 99, see start-jarvis.cmd)
# If an update was applied but the new version exits before it marks itself
# healthy (.updateerify.json still present), the previous files are restored.
$upd = Join-Path $PSScriptRoot ".update"
$env:BUCS_LAUNCHER = "1"     # self-healing may request a restart only when this loop will relaunch
while ($true) {
    & .\.venv\Scripts\python.exe -m uvicorn main:app --host $host_ --port $port
    $restartFlag  = Join-Path $upd "restart.flag"
    $shutdownFlag = Join-Path $upd "shutdown.flag"
    $verify       = Join-Path $upd "verify.json"
    if (Test-Path $restartFlag) {
        Remove-Item $restartFlag -Force
        Write-Host "[BUCS] Restarting into the updated version..." -ForegroundColor Cyan
        $req = $null
        if (Test-Path $verify) { $req = (Get-Content $verify -Raw | ConvertFrom-Json).requirements_changed }
        if ($req) { & .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt }
        continue
    }
    if (Test-Path $shutdownFlag) {
        Remove-Item $shutdownFlag -Force
        Write-Host "[BUCS] Shut down from the dashboard." -ForegroundColor Cyan
        exit 99
    }
    if (Test-Path $verify) {
        $v = Get-Content $verify -Raw | ConvertFrom-Json
        Write-Host "[BUCS] Version $($v.to) failed to start - rolling back to $($v.from)..." -ForegroundColor Yellow
        Copy-Item -Path (Join-Path $v.backup "*") -Destination $PSScriptRoot -Recurse -Force
        foreach ($f in $v.created) { Remove-Item (Join-Path $PSScriptRoot $f) -Force -ErrorAction SilentlyContinue }
        Remove-Item $verify -Force
        continue
    }
    break
}
