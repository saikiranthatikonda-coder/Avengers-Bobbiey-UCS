@echo off
setlocal
title AVENGERS . JARVIS Command Center
cd /d "%~dp0"

echo ============================================================
echo  AVENGERS . JARVIS Command Center
echo  Stark Industries x Bobbiey
echo ============================================================
echo.

REM ---- Kill any stale process listening on port 8765 ----
echo [boot] Checking for stale processes on port 8765...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765 " ^| findstr "LISTENING"') do (
  echo [boot] Killing stale PID %%a holding port 8765
  taskkill /F /T /PID %%a >nul 2>&1
)

REM ---- Kill stale OAuth listeners on 8766-8768 ----
for %%P in (8766 8767 8768) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING"') do (
    echo [boot] Killing stale PID %%a holding port %%P
    taskkill /F /T /PID %%a >nul 2>&1
  )
)

REM ---- Also kill any orphaned python from this folder's venv ----
for /f "tokens=2 delims==" %%P in ('wmic process where "ExecutablePath like '%%jarvis%%\\.venv%%'" get ProcessId /value 2^>nul ^| findstr "="') do (
  echo [boot] Killing orphaned jarvis python PID %%P
  taskkill /F /T /PID %%P >nul 2>&1
)

timeout /t 1 /nobreak >nul

REM ---- Auto-open the dashboard once the server responds ----
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 60;$i++){try{Invoke-WebRequest -Uri 'http://127.0.0.1:8765/api/status' -UseBasicParsing -TimeoutSec 2|Out-Null;Start-Process 'http://127.0.0.1:8765';break}catch{Start-Sleep -Seconds 2}}"

echo [boot] Launching server... (dashboard opens automatically when ready)
echo [boot] To stop: press Ctrl+C in this window, or just close it.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"

echo.
echo ============================================================
echo  Server stopped. Press any key to close this window.
echo ============================================================
pause >nul
endlocal
