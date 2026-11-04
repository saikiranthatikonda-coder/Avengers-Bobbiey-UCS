"""Product Evolution engine — the website roadmap, made live, from CAPABILITY PROBES.

Mirrors the phases published on the Bobbiey UCS site. Every feature is a capability
probe against the RUNNING system, never a static claim:

  loaded        the subsystem object / route / module actually exists in this process
  configured    optional setup done (Google token, Ollama reachable, voice on…) or None
  tests         last real unit-test result for the modules behind it (from AUTONOMOUS
                EVOLUTION's scheduled test runs on the owner's machine): pass | fail | unknown
  last_success  the subsystem's own freshness timestamp, where it keeps one

A feature is "done" when it is loaded and its tests are not failing. /api/roadmap keeps
its original shape (phases → features {name, done}); /api/evolution/capabilities adds
the full matrix.

Version scheme: v<complete-phases>.<features-done-in-current-phase>
"""

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _probe(fn, default=False):
    try:
        return bool(fn())
    except Exception:
        return default


def _value(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _routes() -> set[str]:
    m = sys.modules.get("main")
    app = getattr(m, "app", None)
    try:
        return {getattr(r, "path", "") for r in app.routes} if app is not None else set()
    except Exception:
        return set()


class Roadmap:
    def __init__(self, state: dict) -> None:
        self.state = state   # the app's live service registry

    # feature: (name, loaded, configured|None, [test modules], last_success|None)
    def _phases(self) -> list[dict]:
        s = self.state
        routes = _routes()

        def has(key):
            return s.get(key) is not None

        def route(path):
            return lambda: path in routes if routes else None

        def mod(name):
            return lambda: name in (s.get("feature_modules") or {})

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
        aud = s.get("audit")
        rbac = s.get("rbac")
        flt = s.get("fleet")
        dec = s.get("decisions")
        bill = s.get("billing")
        w3 = s.get("web3")
        evo = s.get("evolution")
        ok_ts = lambda o, a="last_ok": (lambda: getattr(o, a, None) or None)  # noqa: E731

        phase1 = [
            ("Live system telemetry", lambda: bool(s["sysmon"].latest), None, ["test_smoke"], None),
            ("8-agent AI roster", lambda: len(s.get("team") or {}) == 8, None, ["test_smoke"], None),
            ("Voice engine (wake-word + TTS)", lambda: has("tts") or has("voice"),
             lambda: bool(getattr(s.get("tts"), "enabled", False) or has("voice")), ["test_voice", "test_micmode"], None),
            ("AI insights engine", lambda: insights is not None, None, [],
             lambda: (getattr(insights, "last_digest", None) or {}).get("ts")),
            ("Cinematic HUD dashboard", lambda: (HERE / "static" / "index.html").is_file() and
             (not routes or "/" in routes), None, ["test_smoke"], None),
            ("Google Calendar integration", lambda: gcal is not None,
             lambda: bool(getattr(gcal, "token_present", lambda: False)()), ["test_google"], ok_ts(gcal, "last_sync")),
            ("Gmail bridge", lambda: gcal is not None,
             lambda: bool(getattr(gcal, "token_present", lambda: False)()), ["test_google"], ok_ts(gcal, "last_sync")),
            ("Local AI models (Ollama)", lambda: llm is not None, None, [], None),
            ("Cloud AI brain (Claude CLI)", lambda: brain is not None,
             lambda: getattr(brain, "mode", "") not in ("local", "offline", ""), ["test_brain"], None),
            ("Vision awareness (webcam AI)", lambda: brain is not None and (route("/api/vision/analyze")() is not False),
             None, [], None),
            ("Operator memory (persistent)", lambda: mem is not None, None, [], None),
            ("Threat intelligence engine", lambda: threats is not None, None, [], None),
            ("Emergency alert system", lambda: route("/api/alert")() is not False and has("hub"), None, [], None),
            ("Notifications + voice reminders", lambda: agenda is not None, None, [], None),
        ]
        phase2 = [
            ("Orchestration engine (Jarvis-led)", lambda: orch is not None, None, ["test_orchestrator"], None),
            ("Task delegation to specialists", lambda: orch is not None, None, ["test_orchestrator"], None),
            ("Shared context blackboard", lambda: orch is not None, None, ["test_orchestrator"], None),
            ("Autonomous priority resolution", lambda: orch is not None, None, ["test_orchestrator"], None),
            ("Command recommendations feed", lambda: insights is not None, None, [], None),
            ("Knowledge hub (searchable memory)", lambda: knw is not None, None, [], None),
            ("Operator productivity intelligence", lambda: prod is not None, None, [], None),
            ("Agent-to-agent collaboration", lambda: orch is not None and orch.team_memory is not None, None,
             ["test_orchestrator"], None),
            ("Cross-agent shared memory", lambda: s.get("team_memory") is not None or
             (orch is not None and getattr(orch, "team_memory", None) is not None), None, [], None),
        ]
        phase3 = [
            ("Identity-free presence awareness", lambda: mem is not None, None, [], None),
            ("Description-based recognition", lambda: mem is not None and brain is not None, None, [], None),
            ("Activity analytics timeline", lambda: mem is not None, None, [], None),
            ("Multi-camera situational awareness", lambda: route("/api/vision/analyze")() is not False, None, [], None),
            ("Zone monitoring (3×3 sector watch)", lambda: route("/api/vision/analyze")() is not False, None, [], None),
        ]
        phase4 = [
            ("Audit trail (append-only)", lambda: aud is not None, None, [], None),
            ("Role-based command (commander/observer)", lambda: bool(rbac), None, ["test_security"], None),
            ("Multi-operator profiles", lambda: bool(rbac), None, [], None),
            ("Compliance data export", lambda: aud is not None and (route("/api/export")() is not False), None, [], None),
            ("Multi-site fleet (node agents)", lambda: flt is not None, None, [], None),
            ("Authenticated remote access (login + API tokens)", lambda: s.get("auth") is not None,
             lambda: bool(getattr(s.get("auth"), "password_set", False)), ["test_security"], None),
            ("On-prem AI clusters (distributed inference)", lambda: flt is not None, None, [], None),
        ]
        phase5 = [
            ("Decision proposals under human authority", lambda: dec is not None, None, [], None),
            ("Impact simulation (predicted outcomes)", lambda: dec is not None, None, [], None),
            ("Supervised autonomous execution", lambda: dec is not None, None, [], None),
            ("Fleet-wide autonomous operations", lambda: dec is not None and dec.fleet is not None, None, [], None),
            ("Self-healing watchdog", lambda: has("selfheal"), None, ["test_selfheal"],
             lambda: (s["selfheal"].snapshot() or {}).get("checked_at")),
            ("Engineering mode (diagnose → approve → apply)", lambda: has("dev"),
             lambda: bool(getattr(s.get("licensing"), "owner", False)), ["test_devagent"], None),
            ("Autonomous evolution loop (approval-gated)", lambda: evo is not None, None, ["test_evolution"],
             lambda: (getattr(evo, "heartbeat", None) or {}).get("last")),
        ]
        phase6 = [
            ("Subscription editions (Community→Enterprise)", lambda: bill is not None, None, [], None),
            ("AI-credit metering", lambda: bill is not None, None, [], None),
            ("Feature entitlements", lambda: has("licensing"), None, ["test_licensing_providers"], None),
            ("Web3 Command Center (optional module)", lambda: w3 is not None, None, [], None),
            ("Wallet integration (MetaMask / injected)", lambda: w3 is not None, None, [], None),
            ("Token + treasury dashboard", lambda: w3 is not None, None, [], None),
            ("Governance UI", lambda: w3 is not None, None, [], None),
            ("Plugin marketplace (UI)", lambda: w3 is not None, None, [], None),
            ("Live token market data", lambda: False, None, [], None),   # future — no token yet
            ("On-chain staking (rewards)", lambda: False, None, [], None),
        ]
        return [
            {"n": 1, "name": "AI Command Dashboard", "features": phase1},
            {"n": 2, "name": "Multi-Agent Intelligence", "features": phase2},
            {"n": 3, "name": "Computer Vision Operations", "features": phase3},
            {"n": 4, "name": "Enterprise Command Platform", "features": phase4},
            {"n": 5, "name": "Autonomous Decision Support", "features": phase5},
            {"n": 6, "name": "Ecosystem & Monetization", "features": phase6},
        ]

    def _tests(self, modules: list[str]) -> str:
        evo = self.state.get("evolution")
        if not modules or evo is None or not hasattr(evo, "test_status"):
            return "unknown"
        return _value(lambda: evo.test_status(modules), "unknown") or "unknown"

    def capabilities(self) -> list[dict]:
        out = []
        for ph in self._phases():
            for name, loaded, configured, tests, last in ph["features"]:
                ld = _probe(loaded)
                cf = None if configured is None else _probe(configured)
                ts = self._tests(tests)
                ls = _value(last) if last else None
                out.append({"phase": ph["n"], "name": name, "loaded": ld, "configured": cf, "tests": ts,
                            "test_modules": tests, "last_success": ls if isinstance(ls, (int, float)) else None,
                            "done": ld and ts != "fail"})
        return out

    def snapshot(self) -> dict:
        caps = self.capabilities()
        phases_out = []
        done_total = feat_total = complete_phases = 0
        counting_complete = True
        for ph in self._phases():
            feats = [{"name": c["name"], "done": c["done"], "loaded": c["loaded"], "configured": c["configured"],
                      "tests": c["tests"], "last_success": c["last_success"]}
                     for c in caps if c["phase"] == ph["n"]]
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
            else:
                status = "planned"
                counting_complete = False
            phases_out.append({"n": ph["n"], "name": ph["name"], "status": status, "done": done, "total": total,
                               "pct": round(done * 100 / max(1, total)), "features": feats})
        active = next((p for p in phases_out if p["status"] == "active"), None)
        minor = active["done"] if active else 0
        in_dev = [f["name"] for p in phases_out if p["status"] == "active" for f in p["features"] if not f["done"]][:6]
        upcoming = [p["name"] for p in phases_out if p["status"] == "planned"][:3]
        return {
            "version": f"v{complete_phases}.{minor}",
            "progress_pct": round(done_total * 100 / max(1, feat_total)),
            "completed_features": done_total,
            "total_features": feat_total,
            "current_phase": (active or phases_out[-1])["name"],
            "current_phase_n": (active or phases_out[-1])["n"],
            "phases": phases_out,
            "in_development": in_dev,
            "upcoming": upcoming,
            "probed_at": time.time(),
            "failing_tests": [c["name"] for c in caps if c["tests"] == "fail"],
        }
