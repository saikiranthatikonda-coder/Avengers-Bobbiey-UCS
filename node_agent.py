"""Bobbiey UCS — Fleet Node Agent (Roadmap Phase 4).

Run this on ANY laptop you own to make it a live node in your command center.
It reports that machine's real telemetry (CPU/RAM/disk/network/hardware/
peripherals) to your command server every few seconds.

  Dependencies: just `pip install psutil`  (everything else is stdlib)

  Usage:
    python node_agent.py --server http://192.168.1.20:8765 --token <TOKEN> [--name "Studio-Mac"] [--interval 5]

  Get <TOKEN> from the command host: open http://127.0.0.1:8765/api/fleet/token
  on that machine (it only reveals the token to localhost), or read fleet_token.txt
  in the project folder. The same value can be pinned via JARVIS_FLEET_TOKEN.

  The agent auto-reconnects; leave it running (or install it as a service /
  launch item). Ctrl-C to stop.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Windows consoles default to cp1252 and choke on non-ASCII — force UTF-8 so
# node names / paths with accents never crash the agent.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from node_probe import NodeProbe


def main() -> int:
    ap = argparse.ArgumentParser(description="Bobbiey UCS fleet node agent")
    ap.add_argument("--server", default=os.getenv("JARVIS_SERVER", "http://127.0.0.1:8765"),
                    help="command server base URL, e.g. http://192.168.1.20:8765")
    ap.add_argument("--token", default=os.getenv("JARVIS_FLEET_TOKEN", ""),
                    help="fleet token (from /api/fleet/token on the host)")
    ap.add_argument("--name", default=os.getenv("JARVIS_NODE_NAME"),
                    help="friendly node name (defaults to hostname)")
    ap.add_argument("--interval", type=float,
                    default=float(os.getenv("JARVIS_NODE_INTERVAL", "5")),
                    help="seconds between reports")
    args = ap.parse_args()

    url = args.server.rstrip("/") + "/api/fleet/report"
    probe = NodeProbe(name=args.name)
    print(f"[node-agent] {probe.name} ({probe.platform}) -> {url}")
    print(f"[node-agent] node id: {probe.node_id} - interval {args.interval}s")
    if not args.token:
        print("[node-agent] WARNING: no --token given; the host will reject reports "
              "unless it was started with an empty JARVIS_FLEET_TOKEN.")

    ok_streak, fail_streak = 0, 0
    while True:
        try:
            payload = json.dumps(probe.sample()).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json",
                         "X-Fleet-Token": args.token})
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            ok_streak += 1
            fail_streak = 0
            if ok_streak == 1 or ok_streak % 12 == 0:
                print(f"[node-agent] reporting OK ({ok_streak} sent)")
        except urllib.error.HTTPError as e:
            fail_streak += 1
            ok_streak = 0
            if e.code in (401, 403):
                print(f"[node-agent] AUTH REJECTED ({e.code}) — check --token", file=sys.stderr)
            else:
                print(f"[node-agent] HTTP {e.code}", file=sys.stderr)
        except Exception as e:
            fail_streak += 1
            ok_streak = 0
            if fail_streak == 1 or fail_streak % 12 == 0:
                print(f"[node-agent] cannot reach {args.server}: {e}", file=sys.stderr)
        # brief backoff when the server is unreachable, else steady cadence
        time.sleep(min(30, args.interval * (3 if fail_streak else 1)))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[node-agent] stopped.")
