@echo off
REM Bobbiey UCS — Fleet Node Agent launcher (Windows)
REM Turns THIS laptop into a live node in your command center.
REM
REM Usage:  start-node.cmd http://<command-host-ip>:8765 <TOKEN> "Node Name"
REM Example: start-node.cmd http://192.168.1.20:8765 ab12cd... "Studio-Laptop"

setlocal
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage: start-node.cmd ^<server-url^> ^<token^> ["node name"]
  echo Example: start-node.cmd http://192.168.1.20:8765 YOURTOKEN "Studio-Laptop"
  exit /b 1
)

set SERVER=%~1
set TOKEN=%~2
set NODENAME=%~3

REM ensure psutil is present (only dependency the agent needs)
python -c "import psutil" 2>nul
if errorlevel 1 (
  echo [node] installing psutil...
  python -m pip install psutil
)

if "%NODENAME%"=="" (
  python node_agent.py --server %SERVER% --token %TOKEN%
) else (
  python node_agent.py --server %SERVER% --token %TOKEN% --name %NODENAME%
)
endlocal
