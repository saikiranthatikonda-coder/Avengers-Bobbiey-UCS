"""Autonomous Decision Support — Roadmap Phase 5 (first release).

The platform PROPOSES operational decisions, SIMULATES their impact from
live numbers, and EXECUTES them only under human authority:

  PROPOSE   candidates derived from real conditions (never invented)
  SIMULATE  each proposal carries a predicted outcome computed from the
            actual telemetry it would affect
  EXECUTE   · manual: operator clicks APPROVE (commander role required)
            · supervised autonomy: an opt-in toggle lets the platform
              auto-execute a small whitelist of LOW-risk actions; every
              execution is audit-logged either way

Nothing destructive is ever auto-executed. Executions are real platform
actions (start a focus session, switch the brain, acknowledge incidents,
deliver the briefing, delegate a directive) — not simulations of actions.
"""

import json
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

FILE = Path(__file__).parent / "decisions.json"

# only these action keys may run WITHOUT a human click, and only when the
# operator has switched supervised autonomy ON
AUTONOMY_WHITELIST = {"ack_resolved", "switch_claude"}


class DecisionEngine:
    def __init__(self, hub=None, brain=None, local_llm=None, threats=None,
                 agenda=None, insights=None, productivity=None,
                 orchestrator=None, audit=None, tts=None, fleet=None) -> None:
        self.hub = hub
        self.brain = brain
        self.local_llm = local_llm
        self.threats = threats
        self.agenda = agenda
        self.insights = insights
        self.productivity = productivity
        self.orchestrator = orchestrator
        self.audit = audit
        self.tts = tts
        self.fleet = fleet          # Phase 5: fleet-wide autonomous operations
        self.autonomy = False
        self.proposals: dict[str, dict] = {}     # key → proposal
        self.executed: deque = deque(maxlen=10)
        self.executed_total = 0
        self.auto_executed_total = 0
        self._dismissed: dict[str, float] = {}   # key → ts (2h cooldown)
        try:
            saved = json.loads(FILE.read_text(encoding="utf-8"))
            self.autonomy = bool(saved.get("autonomy"))
        except Exception:
            pass

    def _save(self) -> None:
        try:
            FILE.write_text(json.dumps({"autonomy": self.autonomy}),
                            encoding="utf-8")
        except Exception:
            pass

    # ── candidate generation: real conditions → proposals ─────────
    def _candidates(self) -> list[dict]:
        out: list[dict] = []
        now = datetime.now()

        # focus session: a real free block is open and unused
        try:
            intel = self.agenda.intelligence() if self.agenda else {}
            p = self.productivity.snapshot() if self.productivity else {}
            blk = intel.get("largest_free_block_min") or 0
            pres = (self.insights.presence.get("state")
                    if self.insights else "offline")
            if (blk >= 45 and not p.get("focus_active")
                    and p.get("focus_min_today", 0) < 30
                    and pres in ("active", "idle") and 8 <= now.hour < 20):
                out.append({
                    "key": "focus_start", "risk": "low",
                    "title": f"Start a deep-work session ({blk}-min window open)",
                    "rationale": "Calendar shows a protected gap and no focus time logged today.",
                    "impact": f"SIM: protects up to {blk} min; orchestrator guards interruptions; "
                              f"productivity score +{min(45, round(blk / 120 * 45))} pts potential",
                })
        except Exception:
            pass

        # acknowledge incidents that already reached the resolved stage
        try:
            snap = self.threats.snapshot() if self.threats else {}
            stale = [e for e in snap.get("response_queue", [])
                     if e.get("stage") == "resolved"]
            if stale:
                out.append({
                    "key": "ack_resolved", "risk": "low",
                    "title": f"Acknowledge {len(stale)} resolved incident(s)",
                    "rationale": "These incidents completed the response pipeline but still hold the queue.",
                    "impact": f"SIM: response queue {len(snap.get('response_queue', []))} → "
                              f"{len(snap.get('response_queue', [])) - len(stale)}; board hygiene restored",
                    "_ids": [e["id"] for e in stale],
                })
        except Exception:
            pass

        # brain routing: forced-local but the endpoint is down → Claude keeps you alive
        try:
            if (self.brain is not None and getattr(self.brain, "force_local", False)
                    and self.local_llm is not None and not self.local_llm.available):
                out.append({
                    "key": "switch_claude", "risk": "low",
                    "title": "Reroute brain to Claude — local endpoint offline",
                    "rationale": "Replies are silently falling back anyway; make the routing explicit.",
                    "impact": "SIM: reply reliability restored immediately; uses Claude quota until Ollama returns",
                })
            elif (self.brain is not None and not getattr(self.brain, "force_local", False)
                    and self.local_llm is not None and self.local_llm.available
                    and (self.local_llm.last_latency_ms or 99999) < 4000):
                out.append({
                    "key": "switch_local", "risk": "medium",
                    "title": f"Route brain to local {self.local_llm.model}",
                    "rationale": f"Local model proven fast here ({self.local_llm.last_latency_ms}ms) — "
                                 "zero Claude quota, fully private.",
                    "impact": "SIM: Claude usage → 0 for replies; latency "
                              f"≈{self.local_llm.last_latency_ms}ms/reply; vision stays on Claude",
                })
        except Exception:
            pass

        # morning briefing not yet delivered
        try:
            if 7 <= now.hour < 11 and self.insights is not None:
                delivered = any(i.get("source") == "briefing"
                                and time.time() - i.get("ts", 0) < 6 * 3600
                                for i in self.insights.recent)
                if not delivered:
                    out.append({
                        "key": "briefing", "risk": "low",
                        "title": "Deliver the morning executive briefing",
                        "rationale": "No briefing issued yet today.",
                        "impact": "SIM: spoken + logged summary of calendar, inbox, risk and system health",
                    })
        except Exception:
            pass

        # disk pressure → delegate a cleanup investigation (never auto-delete)
        try:
            m = (self.insights.sysmon.latest if self.insights and self.insights.sysmon else {}) or {}
            if m.get("disk", 0) > 88:
                out.append({
                    "key": "disk_directive", "risk": "medium",
                    "title": f"Task STARK with a storage triage — disk {m['disk']:.0f}%",
                    "rationale": "Sustained fill risks failed saves and updates.",
                    "impact": "SIM: directive raised with reclaim candidates report; no files touched without you",
                })
        except Exception:
            pass

        # ── FLEET-WIDE autonomous operations (Phase 5) ────────────
        try:
            if self.fleet is not None:
                snap = self.fleet.snapshot()
                nodes = snap.get("nodes", [])
                for n in nodes:
                    if n.get("is_local") or n.get("status") != "online":
                        continue
                    if (n.get("disk") or 0) >= 90:
                        out.append({
                            "key": f"fleet_disk_{n['node_id']}", "risk": "medium",
                            "title": f"Node {n['name']}: disk critical {n['disk']}%",
                            "rationale": "A fleet node is nearly full; saves/updates may fail there.",
                            "impact": f"SIM: raises a flagged alert for {n['name']} — no remote writes performed",
                        })
                    if (n.get("mem") or 0) >= 92:
                        out.append({
                            "key": f"fleet_mem_{n['node_id']}", "risk": "low",
                            "title": f"Node {n['name']}: memory pressure {n['mem']}%",
                            "rationale": "A fleet node is under heavy memory load.",
                            "impact": f"SIM: notes {n['name']} as degraded in the fleet log",
                        })
                # offload inference: an idle-GPU node is available but the brain
                # is running locally without a GPU
                local_gpu = next((x.get("gpu") for x in nodes if x.get("is_local")), None)
                idle_gpu_node = next(
                    (x for x in nodes if not x.get("is_local") and x.get("status") == "online"
                     and (x.get("ollama") or {}).get("available") and (x.get("gpu") or 0) < 50), None)
                if idle_gpu_node and (local_gpu is None or local_gpu == 0) \
                        and not getattr(self.local_llm, "remote_node", None):
                    out.append({
                        "key": "fleet_offload", "risk": "medium",
                        "title": f"Offload inference to {idle_gpu_node['name']} (idle GPU)",
                        "rationale": "This host has no free GPU; a fleet node offers Ollama with spare GPU.",
                        "impact": f"SIM: routes the brain to {idle_gpu_node['name']} — faster local-model replies, zero Claude quota",
                    })
        except Exception:
            pass
        return out

    # ── lifecycle ─────────────────────────────────────────────────
    async def tick(self) -> None:
        now = time.time()
        cands = {c["key"]: c for c in self._candidates()}
        # drop proposals whose condition cleared
        for key in list(self.proposals.keys()):
            if key not in cands:
                del self.proposals[key]
        # add fresh ones (respect a 2h dismissal cooldown)
        for key, c in cands.items():
            if key in self.proposals:
                self.proposals[key].update(
                    {k: c[k] for k in ("title", "rationale", "impact") if k in c})
                if "_ids" in c:
                    self.proposals[key]["_ids"] = c["_ids"]
                continue
            if now - self._dismissed.get(key, 0) < 7200:
                continue
            self.proposals[key] = {
                "id": uuid.uuid4().hex[:8], "ts": now, **c,
            }
            if self.hub:
                await self.hub.broadcast({
                    "type": "log", "level": "info",
                    "msg": f"decision-support: proposal — {c['title'][:80]}",
                })
        # supervised autonomy: auto-execute whitelisted low-risk proposals
        if self.autonomy:
            for key in list(self.proposals.keys()):
                p = self.proposals.get(key)
                if p and p["risk"] == "low" and key in AUTONOMY_WHITELIST:
                    await self.execute(p["id"], approved_by="autonomy")

    async def execute(self, pid: str, approved_by: str = "operator") -> dict:
        p = next((x for x in self.proposals.values() if x["id"] == pid), None)
        if p is None:
            return {"ok": False, "error": "proposal expired or unknown"}
        done, note = False, ""
        try:
            if p["key"] == "focus_start" and self.productivity is not None:
                await self.productivity.focus_start()
                done, note = True, "deep-work session started"
            elif p["key"] == "ack_resolved" and self.threats is not None:
                n = 0
                for eid in p.get("_ids", []):
                    if self.threats.acknowledge(eid):
                        n += 1
                done, note = True, f"{n} incident(s) acknowledged"
            elif p["key"] == "switch_claude" and self.brain is not None:
                self.brain.force_local = False
                if self.local_llm is not None:
                    self.local_llm.save_pref(force_local=False)
                done, note = True, "brain routed to Claude"
            elif p["key"] == "switch_local" and self.brain is not None:
                self.brain.force_local = True
                if self.local_llm is not None:
                    self.local_llm.save_pref(force_local=True)
                done, note = True, f"brain routed to {self.local_llm.model}"
            elif p["key"] == "briefing" and self.insights is not None:
                await self.insights.briefing(speak=True)
                done, note = True, "executive briefing delivered"
            elif p["key"] == "disk_directive" and self.orchestrator is not None:
                await self.orchestrator._assign(
                    {"key": f"decision-{pid}", "priority": 2, "agent": "stark",
                     "title": "Storage triage — identify reclaim candidates",
                     "detail": "raised by decision support"}, time.time())
                done, note = True, "directive delegated to STARK"
            elif p["key"].startswith("fleet_disk_") or p["key"].startswith("fleet_mem_"):
                # flag the node — raise an alert, do NOT touch the remote machine
                if self.hub:
                    await self.hub.broadcast({
                        "type": "alert", "severity": "warning",
                        "title": p["title"], "detail": p["rationale"],
                        "source": "decision-support · fleet",
                        "action": "Check the node in the Command Fleet panel.",
                    })
                done, note = True, "fleet node flagged"
            elif p["key"] == "fleet_offload" and self.local_llm is not None:
                # route the brain to the idle-GPU node's Ollama (safe, reversible)
                import re as _re
                m = _re.search(r"to (.+?) \(idle", p["title"])
                target = m.group(1) if m else None
                node = None
                if self.fleet is not None and target:
                    node = next((n for n in self.fleet.snapshot()["nodes"]
                                 if n.get("name") == target and n.get("ip")), None)
                if node:
                    oll = node.get("ollama") or {}
                    self.local_llm.base_url = f"http://{node['ip']}:{oll.get('port', 11434)}/v1"
                    self.local_llm.remote_node = node.get("name")
                    if self.brain is not None:
                        self.brain.force_local = True
                    await self.local_llm.probe()
                    done, note = True, f"inference offloaded to {node.get('name')}"
                else:
                    done, note = False, ""
        except Exception as e:
            return {"ok": False, "error": str(e)[:120]}
        if not done:
            return {"ok": False, "error": "no executor for this proposal"}
        self.proposals.pop(p["key"], None)
        record = {**p, "executed_at": time.time(), "by": approved_by, "note": note}
        self.executed.appendleft(record)
        self.executed_total += 1
        if approved_by == "autonomy":
            self.auto_executed_total += 1
        if self.audit is not None:
            await self.audit.log("decision.execute",
                                 f"{p['title'][:80]} → {note} (by {approved_by})")
        if self.hub:
            await self.hub.broadcast({
                "type": "log",
                "level": "warn" if approved_by == "autonomy" else "info",
                "msg": f"decision {'AUTO-' if approved_by == 'autonomy' else ''}EXECUTED: "
                       f"{p['title'][:60]} — {note}",
            })
        return {"ok": True, "note": note}

    async def dismiss(self, pid: str) -> dict:
        p = next((x for x in self.proposals.values() if x["id"] == pid), None)
        if p is None:
            return {"ok": False, "error": "unknown proposal"}
        self.proposals.pop(p["key"], None)
        self._dismissed[p["key"]] = time.time()
        if self.audit is not None:
            await self.audit.log("decision.dismiss", p["title"][:80])
        return {"ok": True}

    async def set_autonomy(self, on: bool) -> dict:
        self.autonomy = bool(on)
        self._save()
        if self.audit is not None:
            await self.audit.log("decision.autonomy",
                                 f"supervised autonomy {'ENABLED' if on else 'DISABLED'}")
        if self.hub:
            await self.hub.broadcast({
                "type": "log", "level": "warn",
                "msg": f"supervised autonomy {'ENABLED — low-risk whitelist may auto-execute' if on else 'disabled'}",
            })
        return {"ok": True, "autonomy": self.autonomy}

    def snapshot(self) -> dict:
        return {
            "autonomy": self.autonomy,
            "whitelist": sorted(AUTONOMY_WHITELIST),
            "proposals": [{k: v for k, v in p.items() if not k.startswith("_")}
                          for p in sorted(self.proposals.values(),
                                          key=lambda x: x["ts"], reverse=True)],
            "executed": list(self.executed),
            "executed_total": self.executed_total,
            "auto_executed_total": self.auto_executed_total,
        }
