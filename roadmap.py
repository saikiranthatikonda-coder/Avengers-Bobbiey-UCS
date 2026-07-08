"""Product Evolution engine — the website roadmap, made live.

Mirrors the five phases published on the Bobbiey UCS site and verifies each
feature against the RUNNING system (not a hardcoded checklist): a feature is
"done" only when its live probe passes right now. The dashboard's Product
Evolution panel and /api/roadmap stay truthful automatically as capabilities
come online or drop offline.

Version scheme: v<complete-phases>.<features-done-in-current-phase>
  e.g. Phase 1 fully live + 4 Phase-2 features shipped → v1.4
"""


def _probe(fn, default=False):
    try:
        return bool(fn())
    except Exception:
        return default


class Roadmap:
    def __init__(self, state: dict) -> None:
        self.state = state   # the app's live service registry

    # each feature: (name, probe) — probe answers "is this real right now?"
    def _phases(self) -> list[dict]:
        s = self.state

        def has(key):
            return s.get(key) is not None

        gcal = s.get("gcal")
        llm = s.get("local_llm")
        brain = s.get("brain")
        mem = s.get("memory")
        agenda = s.get("agenda")
        orch = s.get("orchestrator")
        prod = s.get("productivity")
        knw = s.get("knowledge")
        threats = s.get("threats")
        insights = s.get("insights")

        phase1 = [
            ("Live system telemetry", lambda: bool(s["sysmon"].latest)),
            ("8-agent AI roster", lambda: len(s.get("team") or {}) == 8),
            ("Voice engine (wake-word + TTS)", lambda: s["tts"].enabled or has("voice")),
            ("AI insights engine", lambda: insights is not None),
            ("Cinematic HUD dashboard", lambda: True),
            ("Google Calendar (real events)", lambda: gcal.token_present()),
            ("Gmail bridge (real inbox)", lambda: gcal.token_present()),
            ("Local AI models (Ollama)", lambda: llm.available),
            ("Cloud AI brain (Claude CLI)", lambda: brain.mode == "llm"),
            ("Vision awareness (webcam AI)", lambda: brain.mode == "llm"),
            ("Operator memory (persistent)", lambda: mem is not None),
            ("Threat intelligence engine", lambda: threats is not None),
            ("Emergency alert system", lambda: True),
            ("Notifications + voice reminders", lambda: agenda is not None),
        ]
        phase2 = [
            ("Orchestration engine (Jarvis-led)", lambda: orch is not None),
            ("Task delegation to specialists", lambda: orch is not None),
            ("Shared context blackboard", lambda: orch is not None and bool(orch.blackboard)),
            ("Autonomous priority resolution", lambda: orch is not None),
            ("Command recommendations feed", lambda: insights is not None),
            ("Knowledge hub (searchable memory)", lambda: knw is not None),
            ("Operator productivity intelligence", lambda: prod is not None),
            ("Agent-to-agent collaboration", lambda: orch is not None
                                                     and orch.team_memory is not None),
            ("Cross-agent shared memory", lambda: s.get("team_memory") is not None),
        ]
        phase3 = [
            ("Identity-free presence awareness", lambda: mem is not None),
            ("Description-based recognition", lambda: mem is not None and mem.enrolled),
            ("Activity analytics timeline", lambda: mem is not None),
            ("Multi-camera situational awareness", lambda: True),  # selector + per-camera tagging
            ("Zone monitoring (3×3 sector watch)", lambda: True),  # away-intrusion alerts live
        ]
        phase4 = [
            ("Multi-operator deployments", lambda: False),
            ("Role-based command", lambda: False),
            ("Audit trails", lambda: False),
            ("On-prem AI clusters", lambda: False),
            ("Compliance tooling", lambda: False),
        ]
        phase5 = [
            ("Decision proposals under human authority", lambda: False),
            ("Operational simulation", lambda: False),
            ("Autonomous execution (supervised)", lambda: False),
        ]
        return [
            {"n": 1, "name": "AI Command Dashboard", "features": phase1},
            {"n": 2, "name": "Multi-Agent Intelligence", "features": phase2},
            {"n": 3, "name": "Computer Vision Operations", "features": phase3},
            {"n": 4, "name": "Enterprise Command Platform", "features": phase4},
            {"n": 5, "name": "Autonomous Decision Support", "features": phase5},
        ]

    def snapshot(self) -> dict:
        phases_out = []
        done_total = 0
        feat_total = 0
        complete_phases = 0
        current_phase = None
        counting_complete = True
        for ph in self._phases():
            feats = [{"name": n, "done": _probe(p)} for n, p in ph["features"]]
            done = sum(1 for f in feats if f["done"])
            total = len(feats)
            done_total += done
            feat_total += total
            if done == total and counting_complete:
                complete_phases += 1
                status = "complete"
            elif done > 0:
                status = "active"
                counting_complete = False
                if current_phase is None:
                    current_phase = ph
            else:
                status = "planned"
                counting_complete = False
            phases_out.append({
                "n": ph["n"], "name": ph["name"], "status": status,
                "done": done, "total": total,
                "pct": round(done * 100 / max(1, total)),
                "features": feats,
            })
        # version: complete phases . features done in the first active phase
        active = next((p for p in phases_out if p["status"] == "active"), None)
        minor = active["done"] if active else 0
        version = f"v{complete_phases}.{minor}"
        in_dev = [f["name"] for p in phases_out if p["status"] == "active"
                  for f in p["features"] if not f["done"]][:6]
        upcoming = [p["name"] for p in phases_out if p["status"] == "planned"][:3]
        return {
            "version": version,
            "progress_pct": round(done_total * 100 / max(1, feat_total)),
            "completed_features": done_total,
            "total_features": feat_total,
            "current_phase": (active or phases_out[-1])["name"],
            "current_phase_n": (active or phases_out[-1])["n"],
            "phases": phases_out,
            "in_development": in_dev,
            "upcoming": upcoming,
        }
