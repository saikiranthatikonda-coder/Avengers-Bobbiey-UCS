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


class Fleet:
    def __init__(self, hub=None, local_id: str | None = None) -> None:
        self.hub = hub
        self.local_id = local_id
        self.nodes: dict[str, dict] = {}
        self.reports_total = 0

    def ingest(self, report: dict) -> dict:
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
