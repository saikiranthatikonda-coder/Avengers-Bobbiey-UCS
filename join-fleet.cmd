@echo off
REM ═══ Bobbiey UCS — One-command fleet join (Windows) ═══
REM Makes THIS machine a node in your command center. Auto-finds Python,
REM ensures psutil, then pairs.
REM
REM   join-fleet.cmd                          - asks for host URL + access code
REM   join-fleet.cmd http://10.50.74.67:8765  - asks only for the access code
REM   join-fleet.cmd http://10.50.74.67:8765 "Work-PC"
REM
REM The access code is on the commander dashboard's ADD NODE panel.

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "SERVER=%~1"
set "NAME=%~2"
if "%NAME%"=="" set "NAME=%COMPUTERNAME%"

if "%SERVER%"=="" set /p "SERVER=Command host URL (e.g. http://10.50.74.67:8765): "
REM strip stray angle brackets
set "SERVER=%SERVER:<=%"
set "SERVER=%SERVER:>=%"

REM ── find a Python: 'python' then the 'py' launcher ──
set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if "%PY%"=="" ( py -3 -c "import sys" >nul 2>&1 && set "PY=py -3" )
if "%PY%"=="" (
  echo [join] No Python 3 found. Install from python.org ^(check "Add to PATH"^), then re-run.
  exit /b 1
)
echo [join] using Python: %PY%

%PY% -c "import psutil" >nul 2>&1
if errorlevel 1 (
  echo [join] installing psutil...
  %PY% -m pip install psutil
)

echo [join] pairing this machine as node "%NAME%" -^> %SERVER%
%PY% node_agent.py --server %SERVER% --pair --name "%NAME%"
endlocal
