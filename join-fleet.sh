#!/usr/bin/env bash
# ═══ Bobbiey UCS — One-command fleet join (macOS / Linux) ═══
# Makes THIS machine a node in your command center. It auto-finds a working
# Python, ensures psutil, then pairs — so you don't have to fight python/pip.
#
#   ./join-fleet.sh                         # asks for host URL + access code
#   ./join-fleet.sh http://10.50.74.67:8765 # asks only for the access code
#   ./join-fleet.sh http://10.50.74.67:8765 "My-Mac"
#
# The access code is shown on the commander dashboard's ADD NODE panel.

cd "$(dirname "$0")"

SERVER="$1"
NAME="${2:-$(hostname | cut -d. -f1)}"

if [ -z "$SERVER" ]; then
  printf "Command host URL (e.g. http://10.50.74.67:8765): "
  read SERVER
fi
# strip stray angle brackets / spaces (a common copy-paste mistake)
SERVER="$(echo "$SERVER" | tr -d '<> ')"

# ── pick a Python: prefer one that already has psutil (zero setup), ───
#    else one that has pip so we can install it.
PY=""
for c in "$CONDA_PREFIX/bin/python" python3 python; do
  [ -z "$c" ] && continue
  command -v "$c" >/dev/null 2>&1 || [ -x "$c" ] || continue
  if "$c" -c "import psutil" >/dev/null 2>&1; then PY="$c"; HAVE_PSUTIL=1; break; fi
done
if [ -z "$PY" ]; then
  for c in "$CONDA_PREFIX/bin/python" python3 python; do
    [ -z "$c" ] && continue
    command -v "$c" >/dev/null 2>&1 || [ -x "$c" ] || continue
    if "$c" -m pip --version >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "[join] No usable Python 3 found. Install Python 3 (python.org or 'brew install python'), then re-run."
  exit 1
fi
echo "[join] using Python: $PY"

if [ -z "$HAVE_PSUTIL" ]; then
  echo "[join] installing psutil into that Python..."
  "$PY" -m pip install psutil || "$PY" -m pip install --user psutil || {
    echo "[join] psutil install failed. Try:  $PY -m pip install psutil"; exit 1; }
fi

echo "[join] pairing this machine as node '$NAME' -> $SERVER"
exec "$PY" node_agent.py --server "$SERVER" --pair --name "$NAME"
