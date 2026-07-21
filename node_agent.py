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
from __future__ import annotations   # runs on Python 3.7+ (lazy annotations)

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

# where this node caches the token it obtained by pairing (so it pairs once)
TOKEN_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "node_token.txt")


def _pair(server: str, code: str) -> str:
    """Exchange the short access code for the fleet token."""
    url = server.rstrip("/") + "/api/fleet/pair"
    body = json.dumps({"code": code}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=12) as r:
        d = json.loads(r.read())
    if not d.get("ok"):
        raise SystemExit(f"[node-agent] pairing failed: {d.get('error', 'unknown')}")
    print(f"[node-agent] paired with command host '{d.get('server', '?')}'")
    return d["token"]


def _resolve_token(args) -> str:
    """Token from --token, else the cached token, else pair with the code."""
    if args.token:
        return args.token
    if not args.pair:
        try:
            with open(TOKEN_CACHE, encoding="utf-8") as f:
                cached = f.read().strip()
            if cached:
                return cached
        except Exception:
            pass
    code = args.code or input("Enter the access code shown on the commander dashboard: ").strip()
    token = _pair(args.server, code)
    try:
        with open(TOKEN_CACHE, "w", encoding="utf-8") as f:
            f.write(token)
    except Exception:
        pass
    return token


def main() -> int:
    ap = argparse.ArgumentParser(description="Bobbiey UCS fleet node agent")
    ap.add_argument("--server", default=os.getenv("JARVIS_SERVER", "http://127.0.0.1:8765"),
                    help="command server base URL, e.g. http://192.168.1.20:8765")
    ap.add_argument("--token", default=os.getenv("JARVIS_FLEET_TOKEN", ""),
                    help="fleet token (advanced; prefer --pair with the access code)")
    ap.add_argument("--pair", action="store_true",
                    help="pair using the short access code from the commander dashboard")
    ap.add_argument("--code", default=os.getenv("JARVIS_PAIR_CODE", ""),
                    help="access code (with --pair; prompts if omitted)")
    ap.add_argument("--name", default=os.getenv("JARVIS_NODE_NAME"),
                    help="friendly node name (defaults to hostname)")
    ap.add_argument("--interval", type=float,
                    default=float(os.getenv("JARVIS_NODE_INTERVAL", "5")),
                    help="seconds between reports")
    args = ap.parse_args()

    token = _resolve_token(args)
    url = args.server.rstrip("/") + "/api/fleet/report"
    probe = NodeProbe(name=args.name)
    print(f"[node-agent] {probe.name} ({probe.platform}) -> {url}")
    print(f"[node-agent] node id: {probe.node_id} - interval {args.interval}s")

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
