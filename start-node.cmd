@echo off
REM Bobbiey UCS — Fleet Node Agent launcher (Windows)
REM Turns THIS laptop into a live node in your command center.
REM
REM Usage:  start-node.cmd http://<command-host-ip>:8765 <TOKEN> "Node Name"
REM Example: start-node.cmd http://192.168.1.20:8765 ab12cd... "Studio-Laptop"

setlocal
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage: start-node.cmd ^<server-url^> ["node name"]
  echo Example: start-node.cmd http://192.168.1.20:8765 "Studio-Laptop"
  echo It will ask for the 6-char ACCESS CODE shown on the commander dashboard.
  exit /b 1
)

set SERVER=%~1
set NODENAME=%~2

REM ensure psutil is present (only dependency the agent needs)
python -c "import psutil" 2>nul
if errorlevel 1 (
  echo [node] installing psutil...
  python -m pip install psutil
)

if "%NODENAME%"=="" (
  python node_agent.py --server %SERVER% --pair
) else (
  python node_agent.py --server %SERVER% --pair --name %NODENAME%
)
endlocal
