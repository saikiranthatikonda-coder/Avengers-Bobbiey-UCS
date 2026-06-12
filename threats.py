"""Threat Intelligence Engine.

Aggregates real signals into a classified incident feed:
  SYSTEM        — sustained CPU / memory / disk pressure crossings
  NETWORK       — connectivity degradation, wifi changes
  AI            — brain/LLM failures surfacing in the hub
  OPERATIONS    — calendar conflicts, meeting collisions
  EXTERNAL      — security-relevant headlines from the news feed
  PRODUCTIVITY  — extended operator absence during work hours

Maintains a rolling incident timeline, per-category counts, severity levels
and an operational risk score (0-100). Broadcasts:
  {type: "threat-event", ...}   on each new incident
  {type: "risk", score, level}  whenever the score changes materially
"""

import time
import uuid
from collections import deque
from datetime import datetime

SEVERITY_WEIGHT = {"low": 3, "medium": 8, "high": 18, "critical": 30}
SECURITY_WORDS = ("breach", "hack", "cyber", "malware", "ransomware",
                  "attack", "vulnerability", "exploit", "phishing",
                  "data leak", "zero-day", "ddos")


AGENT_FOR_CATEGORY = {
    "system": "stark", "network": "hulk", "external": "widow",
    "operations": "captain", "ai": "vision", "productivity": "hawkeye",
}


class ThreatEngine:
    def __init__(self, hub, sysmon=None, news=None, agenda=None,
                 insights=None, team=None) -> None:
        self.hub = hub
        self.sysmon = sysmon
        self.news = news
        self.agenda = agenda
        self.insights = insights
        self.team = team
        self.events: deque = deque(maxlen=60)
        self.risk_score = 5
        self._seen_news: set[str] = set()
        self._fired: dict[str, float] = {}   # dedupe key → last fired ts
        self._last_broadcast_score = -1
        self._prev_level = "secure"
        self._prev_matrix: dict[str, int] = {}
        # SOC counters
        self.stats = {"analyzed": 0, "alerts": 0, "resolved": 0}

    # ── event plumbing ────────────────────────────────────────────
    async def _add(self, category: str, severity: str, title: str,
                   detail: str = "", dedupe_key: str | None = None,
                   cooldown: float = 600.0) -> None:
        key = dedupe_key or f"{category}:{title}"
        now = time.time()
        if now - self._fired.get(key, 0) < cooldown:
            return
        self._fired[key] = now
        ev = {
            "id": uuid.uuid4().hex[:8], "ts": now,
            "category": category, "severity": severity,
            "title": title[:120], "detail": detail[:240],
            "agent": AGENT_FOR_CATEGORY.get(category, "jarvis"),
        }
        self.events.appendleft(ev)
        self.stats["alerts"] += 1
        await self.hub.broadcast({"type": "threat-event", **ev})
        # Alert bridge: only CRITICAL incidents raise popup cards — high/medium
        # stay on the feed and ticker so recurring pressure events don't spam.
        if severity == "critical":
            await self.hub.broadcast({
                "type": "alert",
                "severity": "critical",
                "title": title[:120],
                "detail": detail[:200],
                "source": f"threat-engine · {category}",
                "action": "Open Threat Intelligence and triage.",
            })

    def _recompute_risk(self) -> int:
        now = time.time()
        score = 5
        for ev in self.events:
            age = now - ev["ts"]
            if age > 1800:           # incidents decay after 30 min
                continue
            decay = 1.0 - (age / 1800)
            score += SEVERITY_WEIGHT.get(ev["severity"], 3) * decay
        self.risk_score = min(100, round(score))
        return self.risk_score

    @staticmethod
    def level_for(score: int) -> str:
        return "secure" if score < 20 else "elevated" if score < 50 else "high"

    # ── periodic scan ─────────────────────────────────────────────
    async def tick(self) -> None:
        m = (self.sysmon.latest if self.sysmon else {}) or {}

        # SYSTEM
        if m.get("cpu", 0) > 92:
            await self._add("system", "high",
                            f"CPU saturation — {m['cpu']:.0f}%",
                            "Sustained load above 92%. Thermal throttling likely.")
        elif m.get("cpu", 0) > 85:
            await self._add("system", "medium",
                            f"CPU pressure — {m['cpu']:.0f}%")
        if m.get("mem", 0) > 93:
            await self._add("system", "high",
                            f"Memory critical — {m['mem']:.0f}%",
                            "Paging imminent; responsiveness will degrade.")
        if m.get("disk", 0) > 95:
            await self._add("system", "critical",
                            f"Disk nearly full — {m['disk']:.0f}%",
                            "Updates and saves may begin to fail.")

        # OPERATIONS — calendar conflicts
        if self.agenda:
            try:
                snap = self.agenda.snapshot()
                evs = snap.get("events") or []
                for i in range(len(evs) - 1):
                    a, b = evs[i], evs[i + 1]
                    a_end = a["start_ts"] + a["duration_min"] * 60
                    if b["start_ts"] < a_end:
                        await self._add(
                            "operations", "medium",
                            f"Schedule conflict: \"{a['title']}\" overlaps \"{b['title']}\"",
                            dedupe_key=f"conflict:{a['title']}:{b['title']}",
                            cooldown=3600)
            except Exception:
                pass

        # EXTERNAL — security headlines
        if self.news:
            for art in (self.news.recent or [])[:12]:
                title = (art.get("title") or "")
                low = title.lower()
                if title not in self._seen_news and any(w in low for w in SECURITY_WORDS):
                    self._seen_news.add(title)
                    await self._add("external", "low",
                                    f"Security headline: {title}",
                                    f"via {art.get('source', 'feed')}",
                                    cooldown=0)

        # PRODUCTIVITY — long operator absence during work hours
        if self.insights:
            p = self.insights.presence
            hour = datetime.now().hour
            if (p.get("state") == "away" and 9 <= hour < 19
                    and time.time() - p.get("since", time.time()) > 1800):
                await self._add("productivity", "low",
                                "Operator away for 30+ minutes during work hours",
                                cooldown=1800)

        self.stats["analyzed"] += 6   # signals evaluated this tick

        # risk broadcast on material change + emergency transition
        score = self._recompute_risk()
        level = self.level_for(score)
        if abs(score - self._last_broadcast_score) >= 3:
            self._last_broadcast_score = score
            await self.hub.broadcast({"type": "risk", "score": score,
                                      "level": level})
        if level == "high" and self._prev_level != "high":
            await self.hub.broadcast({
                "type": "alert", "severity": "emergency",
                "title": f"OPERATIONAL RISK CRITICAL — {score}/100",
                "detail": "Multiple high-severity incidents active. Command attention required.",
                "source": "threat-engine · escalation",
                "action": "Review the incident board and acknowledge.",
            })
        self._prev_level = level

    # ── threat matrix: six risk domains 0-100 ─────────────────────
    def matrix(self) -> dict:
        now = time.time()
        m = (self.sysmon.latest if self.sysmon else {}) or {}

        def recent(cat: str, weightit=True) -> float:
            s = 0.0
            for ev in self.events:
                if ev["category"] != cat or now - ev["ts"] > 1800:
                    continue
                s += SEVERITY_WEIGHT.get(ev["severity"], 3) * (1 - (now - ev["ts"]) / 1800)
            return s

        cpu, mem, disk = m.get("cpu", 0), m.get("mem", 0), m.get("disk", 0)
        intel = {}
        if self.agenda:
            try:
                intel = self.agenda.intelligence()
            except Exception:
                pass
        avg_conf = 92
        if self.team:
            confs = [a.confidence for a in self.team.values()]
            avg_conf = sum(confs) / max(1, len(confs))

        domains = {
            "operational": min(100, round(10 + recent("operations") * 2
                                          + intel.get("conflicts", 0) * 15
                                          + (20 if intel.get("density") == "heavy" else 0))),
            "infrastructure": min(100, round(max(0, disk - 70) * 2.2
                                             + recent("system") * 1.5)),
            "network": min(100, round(8 + recent("network") * 3)),
            "ai": min(100, round(max(0, 100 - avg_conf) * 2)),
            "resource": min(100, round(max(0, cpu - 60) * 0.9
                                       + max(0, mem - 70) * 1.1)),
            "security": min(100, round(6 + recent("external") * 2.5)),
        }
        out = {}
        for k, v in domains.items():
            prev = self._prev_matrix.get(k, v)
            out[k] = {"score": v,
                      "trend": "up" if v > prev + 2 else "down" if v < prev - 2 else "flat",
                      "confidence": max(60, 96 - abs(v - prev))}
        self._prev_matrix = {k: v["score"] for k, v in out.items()}
        return out

    # ── incident lifecycle stage (for the response timeline) ──────
    @staticmethod
    def _stage(ev: dict) -> str:
        age = time.time() - ev["ts"]
        if age < 60:    return "detected"
        if age < 180:   return "classification"
        if age < 600:   return "analysis"
        if age < 1500 and ev["severity"] in ("high", "critical"):
            return "escalation"
        return "resolved"

    # ── API snapshot ──────────────────────────────────────────────
    def snapshot(self) -> dict:
        score = self._recompute_risk()
        by_cat: dict[str, int] = {}
        by_sev: dict[str, int] = {}
        resolved = 0
        events = []
        for ev in self.events:
            by_cat[ev["category"]] = by_cat.get(ev["category"], 0) + 1
            by_sev[ev["severity"]] = by_sev.get(ev["severity"], 0) + 1
            stage = self._stage(ev)
            if stage == "resolved":
                resolved += 1
            events.append({**ev, "stage": stage})
        self.stats["resolved"] = resolved
        investigations = sum(1 for e in events
                             if e["stage"] in ("analysis", "escalation"))
        return {
            "risk_score": score,
            "level": self.level_for(score),
            "events": events[:20],
            "by_category": by_cat,
            "by_severity": by_sev,
            "matrix": self.matrix(),
            "soc": {**self.stats, "investigations": investigations},
        }

    def summary_text(self) -> str:
        s = self.snapshot()
        if not s["events"]:
            return "No active threats. Perimeter clean."
        top = s["events"][0]
        return (f"Risk {s['risk_score']}/100 ({s['level']}). "
                f"{len(s['events'])} incidents on the board; latest: {top['title']}.")
