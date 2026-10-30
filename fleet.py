"""Fleet registry — Roadmap Phase 4 (multi-site).

Central store of every node reporting into the command server. Each node
(this host + every laptop running node_agent.py) pushes a telemetry sample;
the registry keeps the latest sample per node and derives an online/stale/
offline status from how recently it reported.

Broadcasts {type: "fleet"} on each ingest so the dashboard updates live.
"""

import time

ONLINE_SEC = 20      # reported within 20s → online
STALE_SEC = 90       # within 90s → stale (amber); older → offline
PURGE_SEC = 86400    # forget a node unseen for 24h



# ── report sanitising: a node report crosses machines, so every field is
#    rebuilt from a strict schema (numbers are numbers, strings are short) ──
def _num(v, lo=None, hi=None, default=0.0, allow_none=False):
    try:
        x = float(v)
        if x != x or x in (float("inf"), float("-inf")):
            raise ValueError
    except (TypeError, ValueError):
        return None if allow_none else default
    if lo is not None: x = max(lo, x)
    if hi is not None: x = min(hi, x)
    return round(x, 2)


def _txt(v, n=60):
    return str(v)[:n] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def sanitize_report(r) -> dict:
    if not isinstance(r, dict):
        return {}
    p = r.get("power") if isinstance(r.get("power"), dict) else {}
    o = r.get("ollama") if isinstance(r.get("ollama"), dict) else {}
    lst = lambda k, n: [x for x in (r.get(k) or [])[:n] if isinstance(x, dict)] if isinstance(r.get(k), list) else []  # noqa: E731
    port = int(_num(o.get("port"), 1, 65535, 11434))
    return {
        "node_id": _txt(r.get("node_id"), 64), "name": _txt(r.get("name"), 48) or "node",
        "platform": _txt(r.get("platform"), 60), "ts": _num(r.get("ts")),
        "cpu": _num(r.get("cpu"), 0, 100), "cpu_cores": int(_num(r.get("cpu_cores"), 0, 4096)),
        "mem": _num(r.get("mem"), 0, 100), "mem_total_gb": _num(r.get("mem_total_gb"), 0, 1e5),
        "disk": _num(r.get("disk"), 0, 100), "net_up_kbps": _num(r.get("net_up_kbps"), 0, 1e8),
        "net_down_kbps": _num(r.get("net_down_kbps"), 0, 1e8), "gpu": _num(r.get("gpu"), 0, 100, allow_none=True),
        "uptime_min": _num(r.get("uptime_min"), 0, 1e8, allow_none=True),
        "power": {"present": bool(p.get("present")), "percent": _num(p.get("percent"), 0, 100),
                  "plugged": bool(p.get("plugged")), "ac": bool(p.get("ac"))},
        "volumes": [{"name": _txt(v.get("name"), 40), "mount": _txt(v.get("mount"), 40), "fstype": _txt(v.get("fstype"), 16),
                     "total_gb": _num(v.get("total_gb"), 0, 1e6), "used_pct": _num(v.get("used_pct"), 0, 100),
                     "removable": bool(v.get("removable"))} for v in lst("volumes", 16)],
        "net": [{"name": _txt(n.get("name"), 40), "speed_mbps": _num(n.get("speed_mbps"), 0, 1e6),
                 "addr": _txt(n.get("addr"), 45)} for n in lst("net", 16)],
        "peripherals": [{"name": _txt(d.get("name"), 80), "cls": _txt(d.get("cls"), 30), "ok": bool(d.get("ok"))}
                        for d in lst("peripherals", 60)],
        "ollama": {"available": bool(o.get("available")), "port": port,
                   "models": [_txt(m, 60) for m in (o.get("models") or [])[:12]] if isinstance(o.get("models"), list) else [],
                   "count": int(_num(o.get("count"), 0, 1000))},
        "ip": _txt(r.get("ip"), 45),
    }

class Fleet:
    def __init__(self, hub=None, local_id: str | None = None) -> None:
        self.hub = hub
        self.local_id = local_id
        self.nodes: dict[str, dict] = {}
        self.reports_total = 0

    def ingest(self, report: dict) -> dict:
        report = sanitize_report(report)       # untrusted input from another machine
        nid = report.get("node_id")
        if not nid:
            return {"ok": False, "error": "missing node_id"}
        report["last_report"] = time.time()
        report["is_local"] = (nid == self.local_id)
        self.nodes[nid] = report
        self.reports_total += 1
        return {"ok": True, "node_id": nid}

    def _status(self, node: dict) -> str:
        age = time.time() - node.get("last_report", 0)
        if age < ONLINE_SEC:
            return "online"
        if age < STALE_SEC:
            return "stale"
        return "offline"

    def snapshot(self) -> dict:
        now = time.time()
        # purge long-dead nodes (except the local host, which always stays)
        for nid in list(self.nodes):
            n = self.nodes[nid]
            if not n.get("is_local") and now - n.get("last_report", 0) > PURGE_SEC:
                del self.nodes[nid]
        out = []
        for n in self.nodes.values():
            status = self._status(n)
            out.append({**n, "status": status,
                        "last_seen_sec": round(now - n.get("last_report", now))})
        # local first, then online, then by name
        out.sort(key=lambda x: (not x.get("is_local"),
                                x["status"] != "online", x.get("name", "")))
        online = sum(1 for n in out if n["status"] == "online")
        # fleet-wide aggregate (online nodes only)
        live = [n for n in out if n["status"] == "online"]
        agg = {
            "nodes": len(out),
            "online": online,
            "cpu_cores": sum(n.get("cpu_cores", 0) for n in live),
            "mem_total_gb": round(sum(n.get("mem_total_gb", 0) for n in live), 1),
            "avg_cpu": round(sum(n.get("cpu", 0) for n in live) / len(live)) if live else 0,
        }
        return {"nodes": out, "aggregate": agg, "reports_total": self.reports_total}

    async def ingest_and_notify(self, report: dict) -> dict:
        res = self.ingest(report)
        if res.get("ok") and self.hub is not None:
            await self.hub.broadcast({
                "type": "fleet", "node_id": res["node_id"],
                "name": report.get("name"), "status": "online",
            })
        return res
