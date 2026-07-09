#!/usr/bin/env bash
# Bobbiey UCS — Fleet Node Agent launcher (macOS / Linux)
# Turns THIS machine into a live node in your command center.
#
# Usage:  ./start-node.sh http://<command-host-ip>:8765 <TOKEN> "Node Name"
# Example: ./start-node.sh http://192.168.1.20:8765 ab12cd... "Studio-Mac"

set -e
cd "$(dirname "$0")"

if [ -z "$1" ]; then
  echo "Usage: ./start-node.sh <server-url> <token> [\"node name\"]"
  echo "Example: ./start-node.sh http://192.168.1.20:8765 YOURTOKEN \"Studio-Mac\""
  exit 1
fi

SERVER="$1"
TOKEN="$2"
NODENAME="${3:-}"

# ensure psutil is present (only dependency the agent needs)
python3 -c "import psutil" 2>/dev/null || {
  echo "[node] installing psutil..."
  python3 -m pip install psutil
}

if [ -z "$NODENAME" ]; then
  python3 node_agent.py --server "$SERVER" --token "$TOKEN"
else
  python3 node_agent.py --server "$SERVER" --token "$TOKEN" --name "$NODENAME"
fi
