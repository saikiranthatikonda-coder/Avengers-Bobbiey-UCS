#!/usr/bin/env bash
# BUCS launcher for macOS / Linux (zip / source installs).
# macOS users: double-click "Start BUCS.command" (it runs this script), or install the
# BUCS desktop app (.dmg), which bundles its own Python and needs no setup at all.
# First run sets everything up automatically (Python 3.10+ check, .venv, deps, .env).
set -u
cd "$(dirname "$0")"

echo "============================================================"
echo "  BUCS - Bobbiey Unified Command System"
echo "============================================================"

# --- pick a Python 3.10+ (macOS ships 3.9 as /usr/bin/python3, which is too old) ---
py_ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }
PY=""
for c in python3.13 python3.12 python3.11 python3.10 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 \
         python3 python; do
  if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then PY="$(command -v "$c")"; break; fi
done

# an existing .venv made by an older Python can't run BUCS: rebuild it
if [ -x ".venv/bin/python" ] && ! py_ok ".venv/bin/python"; then
  echo "[boot] The existing .venv uses Python older than 3.10: rebuilding it."
  rm -rf .venv
fi

if [ ! -x ".venv/bin/python" ]; then
  if [ -z "$PY" ]; then
    echo ""
    echo "[boot] BUCS needs Python 3.10 or newer, and none was found."
    if [ "$(uname)" = "Darwin" ]; then
      echo "       (macOS's built-in python3 is 3.9, which is too old.)"
      echo ""
      echo "  Easiest: install the BUCS app for Mac instead (no Python needed):"
      echo "     https://saikiranthatikonda-coder.github.io/Avengers-Bobbiey-UCS/"
      echo "  Or install Python 3.11 from https://www.python.org/downloads/macos/"
      echo "     (or: brew install python@3.11), then double-click Start BUCS.command again."
      open "https://www.python.org/downloads/macos/" >/dev/null 2>&1 || true
    else
      echo "  Install Python 3.11 (e.g. sudo apt install python3.11 python3.11-venv) and re-run."
    fi
    exit 1
  fi
  echo "[boot] First launch: setting up with $("$PY" --version 2>&1) (about 3-5 minutes)..."
  if ! "$PY" -m venv .venv; then
    echo "[boot] Could not create the virtual environment (Linux: install the python3-venv package)."
    rm -rf .venv; exit 1
  fi
  ./.venv/bin/python -m pip install -q --upgrade pip
  if ! ./.venv/bin/python -m pip install -r requirements.txt; then
    rm -rf .venv     # retry cleanly next launch
    echo "[boot] Installing dependencies failed. Check your internet connection and try again."
    exit 1
  fi
fi

# --- .env ---
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "[boot] .env created from the template."
fi

# --- host/port: environment first, then .env, then defaults ---
envval() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\(.*\)[[:space:]]*$/\1/p" .env 2>/dev/null | tail -1 | tr -d '\r"'"'"; }
HOST="${JARVIS_HOST:-$(envval JARVIS_HOST)}"; HOST="${HOST:-127.0.0.1}"
PORT="${JARVIS_PORT:-$(envval JARVIS_PORT)}"; PORT="${PORT:-8765}"

# --- a BUCS already running on this port? just open it ---
if curl -s -m 2 "http://127.0.0.1:$PORT/api/license" >/dev/null 2>&1; then
  echo "[boot] BUCS is already running on port $PORT: opening it."
  (open "http://127.0.0.1:$PORT" || xdg-open "http://127.0.0.1:$PORT") >/dev/null 2>&1
  exit 0
fi
if command -v lsof >/dev/null 2>&1 && lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[boot] Port $PORT is used by another program. Set JARVIS_PORT in .env to another port."
  exit 1
fi

# --- open the dashboard once it answers ---
if [ "${BUCS_NO_BROWSER:-0}" != "1" ]; then
  ( for _ in $(seq 1 225); do
      if curl -s -m 2 "http://127.0.0.1:$PORT/api/license" >/dev/null 2>&1; then
        (open "http://127.0.0.1:$PORT" || xdg-open "http://127.0.0.1:$PORT") >/dev/null 2>&1
        break
      fi
      sleep 2
    done ) &
fi

echo "[boot] Booting on http://$HOST:$PORT  (the dashboard opens automatically)"
echo "[boot] Stop with Ctrl+C or the dashboard's shutdown button."

# --- server loop: same contract as run.ps1 (in-app updates, shutdown, auto-rollback) ---
export BUCS_LAUNCHER=1
UPD=".update"
while true; do
  ./.venv/bin/python -m uvicorn main:app --host "$HOST" --port "$PORT"
  RC=$?
  if [ -f "$UPD/restart.flag" ]; then
    rm -f "$UPD/restart.flag" "$UPD/shutdown.flag"
    echo "[boot] Restarting into the updated version..."
    if [ -f "$UPD/verify.json" ] && ./.venv/bin/python -c 'import json,sys; sys.exit(0 if json.load(open(".update/verify.json")).get("requirements_changed") else 1)'; then
      ./.venv/bin/python -m pip install -q -r requirements.txt
    fi
    continue
  fi
  if [ -f "$UPD/shutdown.flag" ]; then
    rm -f "$UPD/shutdown.flag"
    echo "[boot] Shut down from the dashboard."
    exit 0
  fi
  if [ -f "$UPD/verify.json" ]; then
    echo "[boot] The updated version failed to start: rolling back..."
    ./.venv/bin/python - <<'PYCODE'
import json, shutil
from pathlib import Path
v = json.loads(Path(".update/verify.json").read_text())
b = Path(v["backup"])
for src in b.rglob("*"):
    if src.is_file():
        dst = Path(".") / src.relative_to(b)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
for rel in v.get("created", []):
    Path(rel).unlink(missing_ok=True)
Path(".update/verify.json").unlink()
print(f"[boot] restored {v.get('from')}")
PYCODE
    continue
  fi
  exit $RC
done
