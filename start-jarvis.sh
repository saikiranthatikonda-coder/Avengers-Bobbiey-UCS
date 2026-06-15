#!/usr/bin/env bash
# Bobbiey UCS launcher — macOS / Linux
# First run sets everything up automatically (venv, deps, .env).
set -e
cd "$(dirname "$0")"

echo "============================================================"
echo "  BOBBIEY UCS — Unified Command System"
echo "  Stark Industries x Bobbiey"
echo "============================================================"

# --- pick a python ---
PY=""
for c in python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[boot] Python 3.10+ not found. Install it from https://python.org and re-run."
  exit 1
fi

# --- venv + deps (first run only) ---
if [ ! -d ".venv" ]; then
  echo "[boot] Creating virtual environment..."
  "$PY" -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -r requirements.txt
fi

# --- .env ---
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "[boot] .env created from template. Add your NEWSAPI_KEY (optional) then re-run."
fi

# --- free a stale port 8765 ---
if command -v lsof >/dev/null 2>&1; then
  PID=$(lsof -ti tcp:8765 2>/dev/null || true)
  if [ -n "$PID" ]; then echo "[boot] Freeing port 8765 (pid $PID)"; kill -9 $PID 2>/dev/null || true; fi
fi

HOST="${JARVIS_HOST:-127.0.0.1}"
PORT="${JARVIS_PORT:-8765}"

# --- open the dashboard once it is reachable ---
( for i in $(seq 1 60); do
    if curl -s "http://$HOST:$PORT/api/status" >/dev/null 2>&1; then
      if command -v open >/dev/null 2>&1; then open "http://$HOST:$PORT"
      elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://$HOST:$PORT"; fi
      break
    fi
    sleep 2
  done ) &

echo "[boot] Booting on http://$HOST:$PORT  (dashboard opens automatically)"
echo "[boot] Stop with Ctrl+C."
exec ./.venv/bin/python -m uvicorn main:app --host "$HOST" --port "$PORT"
