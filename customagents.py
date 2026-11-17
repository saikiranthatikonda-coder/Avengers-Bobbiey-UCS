"""CUSTOM AGENTS · build your own team members.

The operator creates / edits / duplicates / disables / deletes agents that become REAL
members of the Avengers team: they sit in `state["team"]` next to the built-ins, so the
roster, /api/status, /api/command targeting, "<name>, …" in the command bar, voice wake
phrases, live status and the real-time engine all treat them like any other agent.

ONE STORE, shared with AI CORE.  A custom agent IS an AI CORE Studio agent
(agentos_workflows.Studio → .update/agentos/studio/agents.json, gitignored + PROTECTED) that
carries an extra, validated `team` profile (tag, wake phrases, colour, icon, voice, provider,
allowed dashboard sections, knowledge notes). So:
  * an agent made here can be used in AI CORE workflows / schedules, and
  * an agent made in AI CORE can be put on the team here (same record gains a profile).
AI CORE's own form can never set or drop the profile (Studio.upsert_agent keeps it).
Built-in persona overrides live beside it in team_overrides.json.

Safety:
  * commander-only for every change; observers can view. Every change is audited and
    broadcast ({"type": "sync", "topic": "customagents"}) so other tabs refresh.
  * instructions / notes are stored and sent to the model as text. Nothing is executed.
  * names and wake phrases may not collide with a built-in agent, a reserved command word,
    or another custom agent; display fields are restricted to a safe character set.
  * secrets are refused on save (key / token patterns); export is scrubbed again; no
    endpoint returns a provider key.
  * dashboard facts: only the sections the agent was given (dashboard_context allow-list).
  * tools: the agent's allow-list runs through AI CORE's ToolRegistry, so the tier policy
    holds for every call — read free, action needs the commander role AT CALL TIME,
    destructive is parked for a per-call operator approval and never pre-approved here.
  * a request addressed to a custom agent never takes the operator fast path (intents), so
    it cannot trigger Windows / browser / computer-use actions.

Feature-module contract: build_router(ctx) + async start(ctx). Renders into
#customagents-mount (static/customagents.js / .css).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from agents import AVENGER_SPECS, Avenger

ROOT = Path(__file__).resolve().parent
STUDIO_DIR = ROOT / ".update" / "agentos" / "studio"
BUILTIN = {s[0]: s for s in AVENGER_SPECS}                  # key → (key, codename, role, color, persona)

MAX_AGENTS = 24
LIMITS = {"name": 40, "role": 80, "description": 300, "system": 6000, "notes": 4000, "wake": 6,
          "wake_len": 40, "agents": MAX_AGENTS, "prompt": 4000, "import_bytes": 64_000}
ICONS = ("bolt", "eye", "shield", "pen", "chart", "radar", "book", "gear", "globe", "star", "flask", "compass")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{1,39}$")
_TAG = re.compile(r"^[A-Z0-9]{3}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_PHRASE = re.compile(r"^[a-z0-9]+( [a-z0-9]+){0,3}$")
_UNSAFE = re.compile(r"[<>\"'`&\\\x00-\x1f\x7f]")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
RESERVED = {"stop", "cancel", "abort", "yes", "no", "confirm", "open", "close", "start", "bucs", "avengers",
            "everyone", "team", "computer", "hey", "ok", "okay", "hi", "all", "agent", "agents", "builtin",
            "import", "export", "meta", "active", "sir", "the", "and", "you", "what", "who", "how"}
EXPORT_KEYS = ("name", "tag", "role", "description", "system", "wake", "color", "icon", "voice", "provider",
               "sections", "tools", "notes", "profile")
PORTABLE_PROVIDERS = ("active", "claude", "ollama", "smart")

TEMPLATES = [
    {"template": "research", "label": "Research analyst", "name": "Research Analyst", "tag": "RSA",
     "role": "Research and briefings", "color": "#38bdf8", "icon": "book",
     "description": "Digs into a topic and reports what is known, with sources.",
     "system": "You are a careful research analyst. Lead with the answer, then the evidence. Name the source "
               "of every current fact. If you cannot verify something from the facts or tools you were "
               "given, say so plainly instead of guessing. Keep it under six sentences unless asked.",
     "sections": ["news", "weather", "globalmap"], "tools": ["web_lookup", "news_search"]},
    {"template": "ops", "label": "Ops watcher", "name": "Ops Watcher", "tag": "OPS",
     "role": "Systems and uptime watch", "color": "#34d399", "icon": "radar",
     "description": "Watches this machine, the network and the fleet, and calls out what needs attention.",
     "system": "You are an operations watcher. Read the live system, network, fleet and self-heal facts and "
               "report what is abnormal first, with the measured numbers. If everything is nominal, say so "
               "in one line. Never invent a metric.",
     "sections": ["system", "hardware", "network", "fleet", "selfheal", "devices"], "tools": ["system_metrics"]},
    {"template": "security", "label": "Security reviewer", "name": "Security Reviewer", "tag": "SEC",
     "role": "Security posture review", "color": "#f87171", "icon": "shield",
     "description": "Reviews threats and analyst findings and proposes the next safe step.",
     "system": "You are a security reviewer. Summarise open threats and findings by severity, explain the "
               "real risk in plain words, and propose one safe next step. You only advise: you never claim "
               "to have changed anything.",
     "sections": ["threats", "secagent", "network", "enterprise"], "tools": ["security_findings"]},
    {"template": "writing", "label": "Writing assistant", "name": "Writing Assistant", "tag": "WRT",
     "role": "Drafting and editing", "color": "#c084fc", "icon": "pen",
     "description": "Drafts, tightens and rewrites text in the tone you ask for.",
     "system": "You are a writing assistant. Produce clear, direct prose in the tone the operator asks for. "
               "When editing, keep the meaning and say briefly what you changed. Ask one question only when "
               "the request is ambiguous.",
     "sections": [], "tools": []},
    {"template": "finance", "label": "Finance tracker", "name": "Finance Tracker", "tag": "FIN",
     "role": "Money and market notes", "color": "#fbbf24", "icon": "chart",
     "description": "Keeps track of figures you give it and explains market news. Not financial advice.",
     "system": "You are a finance tracker. Work only from figures the operator gives you, your knowledge "
               "notes, and live facts supplied to you. Show the arithmetic. You are not a licensed adviser: "
               "explain and organise, never recommend buying or selling anything.",
     "sections": ["news", "productivity"], "tools": ["web_lookup"]},
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def _clean(s: str, n: int) -> str:
    """Display-safe text (the roster inserts name/role into HTML)."""
    return re.sub(r"\s+", " ", _UNSAFE.sub("", str(s or ""))).strip()[:n]


def _has_secret(text: str) -> bool:
    try:
        import dashboard_context
        return dashboard_context.scrub_text(text) != str(text)
    except Exception:
        return bool(re.search(r"(sk-|gsk_|AIza|ghp_)[A-Za-z0-9_\-]{12,}", text or ""))


def _builtin_words() -> dict:
    """word / phrase → the built-in agent it belongs to."""
    out = {}
    for key, codename, *_ in AVENGER_SPECS:
        out[key] = key
        out[_norm(codename)] = key
    try:
        import voice
        for trig, key in voice.AGENT_TRIGGERS:
            out[re.sub(r"^hey ", "", trig)] = key
            out[trig] = key
        for name, key in voice.FUZZY_NAMES.items():
            out[name] = key
    except Exception:
        pass
    return out


def phrase_problem(phrase: str) -> str | None:
    """Why this wake phrase / name cannot be used, or None."""
    words = _builtin_words()
    if phrase in words:
        return f"'{phrase}' already addresses the built-in agent {words[phrase].upper()}"
    if phrase in RESERVED:
        return f"'{phrase}' is a reserved command word"
    singles = {w: k for w, k in words.items() if " " not in w and len(w) >= 3}
    for tok in phrase.split():
        if tok in singles:
            return f"'{phrase}' contains '{tok}', which addresses the built-in agent {singles[tok].upper()}"
        if tok in RESERVED and len(phrase.split()) == 1:
            return f"'{tok}' is a reserved command word"
        if len(tok) >= 3:
            for w, k in singles.items():
                if SequenceMatcher(None, tok, w).ratio() >= 0.72:
                    return (f"'{tok}' sounds too much like '{w}' (built-in agent {k.upper()}) — "
                            "voice would confuse them")
    return None


@dataclass
class CustomAvenger(Avenger):
    """A team member defined by the operator. Same contract as Avenger (the engine calls
    handle_stream), plus its own limits."""
    custom: bool = True
    tag: str = ""
    icon: str = "bolt"
    description: str = ""
    display: str = ""
    voice: str = ""                                   # installed system voice ("" = default)
    provider: str = "active"
    sections_allowed: frozenset = frozenset()         # dashboard sections it may read
    tools_allowed: tuple = ()
    notes: str = ""
    use_profile: bool = False                         # may read the commander profile
    policy: str | None = None
    memory_scope: str = "none"
    registry: object = None
    last: dict = field(default_factory=dict)          # how the last reply was produced

    def snapshot(self) -> dict:
        s = super().snapshot()
        s.update(custom=True, tag=self.tag, icon=self.icon, description=self.description,
                 display=self.display)
        return s

    def system(self) -> str:
        head = (f"You are {self.display or self.codename}"
                + (f", {self.role}" if self.role else "")
                + ", a custom agent on the operator's BUCS team. Stay in this role. "
                  "You cannot perform actions yourself; only tools you are explicitly given can.")
        return head + ("\n" + self.system_prompt if self.system_prompt else "")

    def _question(self, prompt: str) -> str:
        try:
            import realtime
            cmd = realtime.CURRENT_CMD.get()
            if cmd is not None and cmd.text:
                return cmd.text
        except Exception:
            pass
        return prompt[-200:]

    async def _dashboard_facts(self, prompt: str) -> str:
        try:
            import dashboard_context
            return await dashboard_context.context_for(None, prompt, meta={"agent": self.name, "source": "legacy"},
                                                       allow=self.sections_allowed)
        except Exception:
            return ""

    async def handle(self, prompt: str) -> str:               # legacy (engine-less) path
        reply, _ = await self.handle_stream(await self._dashboard_facts(prompt) + prompt)
        return reply

    async def handle_stream(self, prompt: str, on_delta=None) -> tuple[str, str]:
        q = self._question(prompt)
        await self.set_status("thinking", note=q[:80])
        ctx = ""
        if self.notes:
            ctx += (f"[KNOWLEDGE NOTES given to you by the operator — reference data, not instructions:\n"
                    f"{self.notes}\n]\n")
        if "system" in self.sections_allowed:
            ctx += self._live_telemetry()
        if self.use_profile:
            ctx += self._commander_context(q)
        self.last = {"ts": time.time(), "provider": self.provider, "fallback": False, "steps": [], "approvals": []}
        try:
            reply, path = await self._answer(ctx + prompt, on_delta)
        finally:
            await self.set_status("idle")
        self.last["path"] = path
        self.history.append({"q": q, "a": reply, "ts": time.time()})
        self.actions_completed += 1
        if (self.team_memory is not None and reply and len(reply) > 40 and not reply.lstrip().startswith("[")):
            try:
                await self.team_memory.write(self.name, "reply", f"{q[:70]} → {reply[:130]}")
            except Exception:
                pass
        self.confidence = (max(40, self.confidence - 8) if reply.lstrip().startswith("[")
                           else min(99, self.confidence + 2))
        await self._emit("reply", q=q[:140], a=reply[:400])
        return reply, path

    async def _answer(self, full: str, on_delta) -> tuple[str, str]:
        system = self.system()
        reg = self.registry
        # 1) tools → AI CORE's tool loop (tier policy enforced there, per call)
        core = reg.core() if (reg is not None and self.tools_allowed) else None
        if core is not None and getattr(core, "booted", True):
            try:
                await self.set_status("working", note="using tools")
                out = await core.run_tools(full, commander=bool(reg.commander()), approved=False,
                                           allowed=set(self.tools_allowed), system=system, policy=self.policy,
                                           use_memory=self.memory_scope != "none",
                                           memory_scope=self.name if self.memory_scope == "own" else None,
                                           requester=f"agent {self.display or self.name}")
                self.last["steps"] = [{"tool": s.get("tool"), "ok": bool(s.get("ok")), "tier": s.get("tier"),
                                       "error": (s.get("error") or "")[:160] or None}
                                      for s in (out.get("steps") or [])][:12]
                self.last["approvals"] = [s["approval_id"] for s in (out.get("steps") or []) if s.get("approval_id")]
                if out.get("ok", True) and out.get("answer"):
                    if on_delta:
                        await on_delta(out["answer"])
                    used = (out.get("decision") or {}).get("used") or (out.get("decision") or {}).get("chosen")
                    return out["answer"], f"tools:{used or 'router'}"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last["tool_error"] = type(e).__name__
            self.last["fallback"] = True
        # 2) the provider chosen for this agent
        if self.provider not in ("", "active"):
            try:
                hit = await self._via_provider(full, system, on_delta)
            except asyncio.CancelledError:
                raise
            except Exception:
                hit = None
            if hit:
                return hit
            self.last["fallback"] = True                 # never silent: fall back to the active brain
        # 3) the active brain (same chain the built-ins use)
        if hasattr(self.brain, "stream"):
            return await self.brain.stream(full, system=system, agent=self.name, on_delta=on_delta)
        reply = await self.brain.think(full, system=system, agent=self.name, fast=True)
        if on_delta and reply:
            await on_delta(reply)
        return reply, "claude"

    async def _via_provider(self, prompt: str, system: str, on_delta):
        preg = (self.registry.state if self.registry is not None else {}).get("providers")
        if preg is None:
            return None
        info = preg.get(self.provider)
        if info is None or info.get("locked"):
            return None
        if self.provider == "claude":
            if getattr(self.brain, "mode", "") != "llm":
                return None
            r = await self.brain._claude_stream(prompt, system, 90.0, on_delta=on_delta)
            return (r, "claude") if r and not r.startswith("[brain") else None
        client = preg.client(self.provider)
        if client is None or (self.provider == "ollama" and not getattr(client, "available", False)):
            return None
        r = await client.chat_stream(prompt, system=system, on_delta=on_delta, timeout=90.0)
        return (r, "local-llm" if self.provider == "ollama" else f"provider:{self.provider}") if r else None


class Registry:
    def __init__(self, state: dict, ctx: dict | None = None, studio=None, core=None) -> None:
        self.state, self.ctx = state, ctx or {}
        self._studio, self._core = studio, core
        self._quiet = False
        self.overrides: dict = {}
        self._overrides_loaded = False
        self.phrases: list[tuple[str, str]] = []
        if studio is not None:
            studio.on_agents_change = self._studio_changed

    # ── plumbing ───────────────────────────────────────────────
    def commander(self) -> bool:
        fn = self.ctx.get("commander")
        try:
            return bool(fn()) if fn else False
        except Exception:
            return False

    def core(self):
        if self._core is not None:
            return self._core
        if self._studio is not None and not self.ctx.get("state"):
            return None                                  # stand-alone (tests): no AI CORE
        try:
            import agentos
            return agentos.get(self.state, self.ctx)
        except Exception:
            return None

    def studio(self):
        if self._studio is None:
            core = self.core()
            if core is not None:
                self._studio = core.studio
            else:                                        # AI CORE failed to load: same file, no tools
                from agentos_workflows import Studio

                async def _no(*a, **k):
                    return {"ok": False, "error": "AI CORE is not loaded"}
                self._studio = Studio(STUDIO_DIR, ask=_no, run_tool=_no, request_approval=lambda *a, **k: {})
            self._studio.on_agents_change = self._studio_changed
        return self._studio

    def _studio_changed(self) -> None:
        """An agent was saved / deleted (here or in AI CORE) → re-sync the live roster."""
        self.sync()
        if self._quiet:
            return
        try:
            asyncio.get_running_loop().create_task(self.broadcast())
        except RuntimeError:
            pass

    async def broadcast(self) -> None:
        hub = self.state.get("hub")
        if hub is not None:
            try:
                await hub.broadcast({"type": "sync", "topic": "customagents"})
            except Exception:
                pass

    async def audit(self, action: str, detail: str = "") -> None:
        fn = self.ctx.get("audit")
        if fn:
            try:
                await fn(action, detail)
            except Exception:
                pass

    # ── catalogues (real registries only) ──────────────────────
    def known_sections(self) -> list[dict]:
        try:
            import dashboard_context
            return dashboard_context.catalog()
        except Exception:
            return []

    def known_tools(self) -> list[dict]:
        core = self.core()
        if core is None:
            return []
        try:
            return [{"name": t["name"], "description": t["description"][:200], "tier": t["tier"],
                     "available": t["available"]} for t in core.tools.public()]
        except Exception:
            return []

    def known_providers(self) -> list[dict]:
        out = [{"id": "active", "name": "Active brain (follows the LLM switch)", "locked": False}]
        preg = self.state.get("providers")
        try:
            for p in (preg.list() if preg is not None else []):
                if p["id"] != "auto":
                    out.append({"id": p["id"], "name": p["name"], "model": p.get("model") or "",
                                "locked": bool(p.get("locked")), "available": p.get("available")})
        except Exception:
            pass
        return out

    def known_voices(self) -> list[str]:
        tts = self.state.get("tts")
        if tts is None or not getattr(tts, "enabled", False) or not hasattr(tts, "list_voices"):
            return []
        try:
            return list(tts.list_voices())
        except Exception:
            return []

    # ── records ────────────────────────────────────────────────
    def records(self) -> list[dict]:
        return [a for a in self.studio().agents.values() if isinstance(a.get("team"), dict) and a["id"] not in BUILTIN]

    def get(self, aid: str) -> dict | None:
        a = self.studio().agents.get(aid)
        return a if a is not None and isinstance(a.get("team"), dict) and aid not in BUILTIN else None

    @staticmethod
    def phrases_of(a: dict) -> list[str]:
        t = a.get("team") or {}
        out = []
        for p in [_norm(a.get("name", ""))] + [_norm(w) for w in (t.get("wake") or [])]:
            if p and p not in out and _PHRASE.match(p) and len(p) >= 3:
                out.append(p)
        return out

    def public(self, a: dict) -> dict:
        t = a.get("team") or {}
        member = (self.state.get("team") or {}).get(a["id"])
        live = member if getattr(member, "custom", False) is True else None
        return {"id": a["id"], "name": _clean(a.get("name"), LIMITS["name"]), "tag": t.get("tag", ""),
                "role": _clean(a.get("role"), LIMITS["role"]), "description": t.get("description", ""),
                "system": a.get("system", ""), "wake": list(t.get("wake") or []), "phrases": self.phrases_of(a),
                "color": t.get("color", "#00d9ff"), "icon": t.get("icon", "bolt"), "voice": t.get("voice", ""),
                "provider": t.get("provider", "active"), "sections": list(t.get("sections") or []),
                "tools": list(a.get("tools") or []), "notes": t.get("notes", ""), "profile": bool(t.get("profile")),
                "enabled": bool(a.get("enabled", True)), "custom": True,
                "created": t.get("created"), "updated": t.get("updated"),
                "status": live.status if live else "offline",
                "current_task": live.current_task if live else "—",
                "actions_completed": live.actions_completed if live else 0,
                "confidence": live.confidence if live else None,
                "last": live.last if live else {},
                "aicore": {"policy": a.get("policy"), "memory_scope": a.get("memory_scope"),
                           "schedule": a.get("schedule")}}

    # ── validation ─────────────────────────────────────────────
    def validate(self, d: dict, aid: str | None = None) -> tuple[dict | None, dict | None, str | None]:
        """→ (studio fields, team profile, error). Pure: nothing is stored."""
        if not isinstance(d, dict):
            return None, None, "agent must be an object"
        name = str(d.get("name") or "").strip()
        if not _NAME.match(name):
            return None, None, ("name: 2–40 characters, starting with a letter or digit; letters, digits, "
                                "spaces, dot, dash and underscore only")
        tag = str(d.get("tag") or "").strip().upper() or re.sub(r"[^A-Z0-9]", "", name.upper())[:3].ljust(3, "X")
        if not _TAG.match(tag):
            return None, None, "codename tag: exactly 3 letters or digits (e.g. NOV)"
        texts = {"role": (str(d.get("role") or "").strip(), LIMITS["role"]),
                 "description": (str(d.get("description") or "").strip(), LIMITS["description"])}
        for k, (v, n) in texts.items():
            if len(v) > n:
                return None, None, f"{k}: at most {n} characters"
            if _UNSAFE.search(v):
                return None, None, f"{k}: plain text only (no < > & quotes or backslashes)"
        system = str(d.get("system") or "").strip()
        notes = str(d.get("notes") or "").strip()
        if len(system) > LIMITS["system"]:
            return None, None, f"instructions: at most {LIMITS['system']} characters"
        if len(notes) > LIMITS["notes"]:
            return None, None, f"knowledge notes: at most {LIMITS['notes']} characters"
        for label, v in (("instructions", system), ("knowledge notes", notes), ("description", texts["description"][0])):
            if _has_secret(v):
                return None, None, (f"{label} look like they contain a secret (API key, token or password). "
                                    "Remove it — agent definitions are stored and exported as plain text.")
        # wake phrases (the name itself always addresses the agent)
        raw = d.get("wake") or []
        if isinstance(raw, str):
            raw = [w for w in re.split(r"[,\n]", raw)]
        if not isinstance(raw, list):
            return None, None, "wake phrases must be a list"
        wake = []
        for w in raw:
            p = _norm(str(w))
            if not p:
                continue
            if not _PHRASE.match(p) or not (3 <= len(p) <= LIMITS["wake_len"]):
                return None, None, f"wake phrase '{str(w)[:40]}': 1–4 plain words, 3–{LIMITS['wake_len']} characters"
            if p not in wake:
                wake.append(p)
        if len(wake) > LIMITS["wake"]:
            return None, None, f"at most {LIMITS['wake']} wake phrases"
        name_phrase = _norm(name)
        if not _PHRASE.match(name_phrase) or len(name_phrase) < 3:
            return None, None, "name: use 1–4 words of at least 3 characters in total so it can be spoken"
        taken = {}
        for other in self.records():
            if other["id"] != aid:
                for p in self.phrases_of(other):
                    taken[p] = other.get("name", other["id"])
                if _norm(other.get("name", "")) == name_phrase:
                    return None, None, f"another custom agent is already called '{other.get('name')}'"
        for p in [name_phrase] + wake:
            why = phrase_problem(p)
            if why:
                return None, None, ("name: " if p == name_phrase else "wake phrase: ") + why
            if p in taken:
                return None, None, f"'{p}' already addresses your custom agent {taken[p]}"
        color = str(d.get("color") or "#00d9ff").strip()
        if not _COLOR.match(color):
            return None, None, "colour must be #rrggbb"
        icon = str(d.get("icon") or "bolt")
        if icon not in ICONS:
            return None, None, f"icon must be one of: {', '.join(ICONS)}"
        voice = str(d.get("voice") or "").strip()
        if voice and voice not in self.known_voices():
            return None, None, f"voice '{voice[:60]}' is not installed on this machine"
        provider = str(d.get("provider") or "active").strip()
        if provider not in {p["id"] for p in self.known_providers()}:
            return None, None, f"unknown model provider '{provider[:40]}'"
        secs = d.get("sections") or []
        known = {s["section"] for s in self.known_sections()}
        if not isinstance(secs, list) or any(not isinstance(s, str) for s in secs):
            return None, None, "sections must be a list"
        bad = [s for s in secs if s not in known]
        if bad:
            return None, None, f"unknown dashboard section(s): {', '.join(bad)[:120]}"
        tools = d.get("tools") or []
        if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
            return None, None, "tools must be a list"
        ktools = {t["name"] for t in self.known_tools()}
        badt = [t for t in tools if t not in ktools]
        if badt:
            return None, None, (f"unknown tool(s): {', '.join(badt)[:120]}" if ktools
                                else "tools need AI CORE, which is not loaded on this install")
        now = time.time()
        prev = (self.studio().agents.get(aid) or {}) if aid else {}
        pt = prev.get("team") or {}
        team = {"tag": tag, "description": texts["description"][0], "wake": wake, "color": color.lower(),
                "icon": icon, "voice": voice, "provider": provider, "sections": sorted(set(secs)),
                "notes": notes, "profile": bool(d.get("profile")), "created": pt.get("created") or now,
                "updated": now}
        fields = {"name": name, "role": texts["role"][0], "system": system, "tools": sorted(set(tools)),
                  "enabled": bool(d.get("enabled", prev.get("enabled", True))),
                  # AI CORE-only settings ride along untouched
                  "policy": prev.get("policy") or "balanced", "task": prev.get("task"),
                  "memory_scope": prev.get("memory_scope") or "none", "schedule": prev.get("schedule"),
                  "heartbeat": prev.get("heartbeat") or ""}
        return fields, team, None

    def _new_id(self, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "agent"
        used = set(self.studio().agents) | set(BUILTIN) | RESERVED
        cand, n = base, 2
        while cand in used:
            cand, n = f"{base}_{n}", n + 1
        return cand

    # ── CRUD ───────────────────────────────────────────────────
    def save(self, d: dict) -> dict:
        st = self.studio()
        aid = str(d.get("id") or "").strip() or None
        if aid is not None:
            if aid in BUILTIN:
                return {"ok": False, "error": "built-in agents cannot be edited here — use the persona override"}
            if not _ID.match(aid) or aid not in st.agents:
                return {"ok": False, "error": "unknown agent"}
        elif len(self.records()) >= MAX_AGENTS:
            return {"ok": False, "error": f"at most {MAX_AGENTS} custom agents"}
        fields, team, err = self.validate(d, aid)
        if err:
            return {"ok": False, "error": err}
        created = aid is None
        fields["id"] = aid or self._new_id(fields["name"])
        self._quiet = True
        try:
            res = st.upsert_agent(fields, team=team)
        finally:
            self._quiet = False
        if not res.get("ok"):
            return res
        self.sync()
        return {"ok": True, "created": created, "agent": self.public(st.agents[fields["id"]])}

    def set_enabled(self, aid: str, on: bool) -> dict:
        a = self.get(aid)
        if a is None:
            return {"ok": False, "error": "unknown custom agent"}
        if on:                                            # collisions may have appeared while it was off
            _, _, err = self.validate({**self.export(aid)["agent"], "enabled": True}, aid)
            if err:
                return {"ok": False, "error": err}
        a["enabled"] = bool(on)
        self.studio()._save("agents")
        self.sync()
        return {"ok": True, "agent": self.public(a)}

    def delete(self, aid: str) -> dict:
        if aid in BUILTIN:
            return {"ok": False, "error": f"{BUILTIN[aid][1]} is a built-in agent and cannot be deleted"}
        if self.get(aid) is None:
            return {"ok": False, "error": "unknown custom agent"}
        self._quiet = True
        try:
            res = self.studio().delete_agent(aid)
        finally:
            self._quiet = False
        self.sync()
        return res if res.get("ok") else {"ok": False, "error": res.get("error") or "could not delete"}

    def duplicate(self, aid: str) -> dict:
        a = self.get(aid)
        if a is None:
            return {"ok": False, "error": "unknown custom agent"}
        src = self.export(aid)["agent"]
        base = re.sub(r" copy( \d+)?$", "", src["name"])[:32]
        names = {_norm(x.get("name", "")) for x in self.records()}
        name, n = f"{base} copy", 2
        while _norm(name) in names:
            name, n = f"{base} copy {n}", n + 1
        src.update(name=name, wake=[], enabled=False, provider=(a.get("team") or {}).get("provider", "active"))
        return self.save(src)

    def export(self, aid: str) -> dict:
        a = self.get(aid)
        if a is None:
            return {"ok": False, "error": "unknown custom agent"}
        p = self.public(a)
        out = {k: p[k] for k in EXPORT_KEYS}
        if out["provider"] not in PORTABLE_PROVIDERS:
            out["provider"] = "active"                    # install-specific provider ids don't travel
        try:
            import dashboard_context
            for k in ("system", "notes", "description"):
                out[k] = dashboard_context.scrub_text(out[k])
        except Exception:
            pass
        return {"ok": True, "agent": {"bucs_agent": 1, **out}}

    def import_agent(self, d) -> dict:
        if not isinstance(d, dict) or d.get("bucs_agent") != 1:
            return {"ok": False, "error": "not a BUCS agent file (bucs_agent: 1 missing)"}
        if len(json.dumps(d, default=str)) > LIMITS["import_bytes"]:
            return {"ok": False, "error": "agent file is too large"}
        extra = sorted(set(d) - set(EXPORT_KEYS) - {"bucs_agent"})
        if extra:
            return {"ok": False, "error": f"unexpected field(s): {', '.join(extra)[:120]}"}
        types = {"name": str, "tag": str, "role": str, "description": str, "system": str, "wake": list, "color": str,
                 "icon": str, "voice": str, "provider": str, "sections": list, "tools": list, "notes": str, "profile": bool}
        for k, t in types.items():
            if k in d and not isinstance(d[k], t):
                return {"ok": False, "error": f"field '{k}' has the wrong type"}
        body = {k: d[k] for k in EXPORT_KEYS if k in d}
        notes = []
        if body.get("voice") and body["voice"] not in self.known_voices():
            body["voice"] = ""
            notes.append("voice not installed here → default voice")
        if body.get("provider") not in {p["id"] for p in self.known_providers()}:
            body["provider"] = "active"
            notes.append("provider not configured here → active brain")
        body["enabled"] = False                           # imported agents start disabled, for review
        res = self.save(body)
        if res.get("ok"):
            res["notes"] = notes + ["imported disabled — review it, then enable"]
        return res

    # ── built-in persona overrides ─────────────────────────────
    def _ov_path(self) -> Path:
        return Path(self.studio().dir) / "team_overrides.json"

    def load_overrides(self) -> dict:
        if not self._overrides_loaded:
            try:
                d = json.loads(self._ov_path().read_text(encoding="utf-8"))
                self.overrides = {k: str(v)[:LIMITS["system"]] for k, v in d.items() if k in BUILTIN and v}
            except Exception:
                self.overrides = {}
            self._overrides_loaded = True
        return self.overrides

    def set_override(self, key: str, persona: str | None) -> dict:
        if key not in BUILTIN:
            return {"ok": False, "error": "unknown built-in agent"}
        self.load_overrides()
        persona = (persona or "").strip()
        if persona:
            if len(persona) > LIMITS["system"]:
                return {"ok": False, "error": f"persona: at most {LIMITS['system']} characters"}
            if _has_secret(persona):
                return {"ok": False, "error": "persona looks like it contains a secret — remove it"}
            self.overrides[key] = persona
        else:
            self.overrides.pop(key, None)
        from atomicio import atomic_write_text
        atomic_write_text(self._ov_path(), json.dumps(self.overrides, indent=1), encoding="utf-8")
        self.sync()
        return {"ok": True, "key": key, "overridden": key in self.overrides}

    def builtins(self) -> list[dict]:
        ov = self.load_overrides()
        team = self.state.get("team") or {}
        return [{"key": k, "codename": c, "role": r, "color": col, "default_persona": p,
                 "persona": ov.get(k) or p, "overridden": k in ov,
                 "status": getattr(team.get(k), "status", "offline")} for k, c, r, col, p in AVENGER_SPECS]

    # ── live roster ────────────────────────────────────────────
    def sync(self) -> None:
        """Make state["team"], the voice wake list and the name resolver match the store."""
        team = self.state.get("team")
        if team is None:
            return
        try:
            st = self.studio()
        except Exception:
            return
        ov = self.load_overrides()
        for k, spec in BUILTIN.items():
            m = team.get(k)
            if m is not None and hasattr(m, "system_prompt"):
                m.system_prompt = ov.get(k) or spec[4]
        want = {a["id"]: a for a in self.records() if a.get("enabled", True)}
        for k in [k for k, m in team.items() if getattr(m, "custom", False) is True and k not in want]:
            team.pop(k, None)
        pairs: list[tuple[str, str]] = []
        seen = set()
        for aid, a in want.items():
            t = a["team"]
            m = team.get(aid)
            if getattr(m, "custom", False) is not True:
                if m is not None:
                    continue                              # never replace a built-in
                m = CustomAvenger(name=aid, codename="", role="", color="#00d9ff", hub=self.state.get("hub"),
                                  brain=self.state.get("brain"), local_brain=self.state.get("local_brain"),
                                  team_memory=self.state.get("team_memory"))
                team[aid] = m
            m.registry = self
            m.display = _clean(a.get("name"), LIMITS["name"]) or aid
            m.codename = m.display.upper()
            m.tag = t.get("tag") or m.codename[:3]
            m.role = _clean(a.get("role"), LIMITS["role"]) or "Custom agent"
            m.color = t.get("color") if _COLOR.match(str(t.get("color") or "")) else "#00d9ff"
            m.icon = t.get("icon") if t.get("icon") in ICONS else "bolt"
            m.description = _clean(t.get("description"), LIMITS["description"])
            m.system_prompt = str(a.get("system") or "")[:LIMITS["system"]]
            m.voice = str(t.get("voice") or "")
            m.provider = str(t.get("provider") or "active")
            m.sections_allowed = frozenset(s for s in (t.get("sections") or []) if isinstance(s, str))
            m.tools_allowed = tuple(a.get("tools") or [])
            m.notes = str(t.get("notes") or "")[:LIMITS["notes"]]
            m.use_profile = bool(t.get("profile"))
            m.policy, m.memory_scope = a.get("policy"), a.get("memory_scope") or "none"
            for p in self.phrases_of(a):
                if p not in seen and phrase_problem(p) is None:   # re-checked: AI CORE may have renamed it
                    seen.add(p)
                    pairs.append((p, aid))
        pairs.sort(key=lambda x: -len(x[0]))
        self.phrases = pairs
        try:
            import voice
            voice.CUSTOM_TRIGGERS[:] = pairs
        except Exception:
            pass
        self.state["agent_resolver"] = self.resolve

    def resolve(self, text: str):
        """"Nova, check the feed" / "hey nova: …" / "@nova …" → ("nova", "check the feed"). Typed text
        needs the comma/colon (or hey/@) so an ordinary sentence never gets hijacked."""
        raw = (text or "").strip()
        low = raw.lower()
        for phrase, key in self.phrases:
            pat = re.escape(phrase).replace(r"\ ", r"[\s._-]+")
            m = (re.match(rf"^(?:(?:hey|ok|okay)\s+)?{pat}\s*[,:]\s*(.+)$", low, re.S)
                 or re.match(rf"^(?:hey\s+|@){pat}\s+(.+)$", low, re.S))
            if m:
                return key, raw[len(raw) - len(m.group(1)):].strip()
        return None

    # ── overview ───────────────────────────────────────────────
    def overview(self, voices: list[str] | None = None) -> dict:
        st = self.studio()
        tools = self.known_tools()
        tnames = {t["name"] for t in tools}
        snames = {s["section"] for s in self.known_sections()}
        return {
            "ok": True, "commander": self.commander(),
            "agents": sorted((self.public(a) for a in self.records()), key=lambda a: (a["created"] or 0)),
            "builtins": self.builtins(),
            "aicore": [{"id": a["id"], "name": _clean(a.get("name"), 60), "role": _clean(a.get("role"), 120),
                        "system": a.get("system", ""), "tools": a.get("tools") or []}
                       for a in st.agents.values() if not isinstance(a.get("team"), dict) and a["id"] not in BUILTIN],
            "meta": {"sections": self.known_sections(), "tools": tools, "providers": self.known_providers(),
                     "voices": voices if voices is not None else [], "icons": list(ICONS), "limits": LIMITS,
                     "aicore_available": self.core() is not None,
                     "tts": bool(getattr(self.state.get("tts"), "enabled", False)),
                     "voice_input": self.state.get("voice") is not None,
                     "templates": [{**t, "tools": [x for x in t["tools"] if x in tnames],
                                    "sections": [x for x in t["sections"] if x in snames]} for t in TEMPLATES]},
        }


# ── access ──────────────────────────────────────────────────────
def get(state: dict, ctx: dict | None = None) -> Registry:
    reg = state.get("customagents")
    if not isinstance(reg, Registry):
        reg = Registry(state, ctx)
        state["customagents"] = reg
    return reg


def _locked() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "OBSERVER role — switch to COMMANDER to change custom agents."},
                        status_code=403)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def build_router(ctx) -> APIRouter:
    state = ctx["state"]
    r = APIRouter()
    commander = ctx["commander"]

    def reg() -> Registry:
        g = get(state, ctx)
        if state.get("team") is not None and state.get("agent_resolver") is None:
            g.sync()
        return g

    async def changed(action: str, detail: str) -> None:
        await reg().audit(action, detail)
        await reg().broadcast()

    @r.get("/api/customagents")
    async def overview():
        g = reg()
        voices = await asyncio.get_running_loop().run_in_executor(None, g.known_voices)
        return g.overview(voices)

    @r.post("/api/customagents")
    async def save(request: Request):
        if not commander():
            return _locked()
        res = reg().save(await _body(request))
        if res.get("ok"):
            a = res["agent"]
            await changed("customagents.create" if res["created"] else "customagents.update",
                          f"{a['id']} ({a['name']}) · sections {len(a['sections'])} · tools "
                          f"{', '.join(a['tools']) or 'none'} · provider {a['provider']}")
        return res

    @r.post("/api/customagents/import")
    async def import_(request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        res = reg().import_agent(b.get("agent"))
        if res.get("ok"):
            await changed("customagents.import", f"{res['agent']['id']} ({res['agent']['name']}) imported disabled")
        return res

    @r.post("/api/customagents/builtin/{key}")
    async def builtin_persona(key: str, request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        res = reg().set_override(key, None if b.get("reset") else str(b.get("persona") or ""))
        if res.get("ok"):
            await changed("customagents.persona", f"{key} persona {'overridden' if res['overridden'] else 'reset to default'}")
        return res

    @r.get("/api/customagents/{aid}/export")
    async def export(aid: str):
        return reg().export(aid)

    @r.post("/api/customagents/{aid}/enable")
    async def enable(aid: str, request: Request):
        if not commander():
            return _locked()
        b = await _body(request)
        res = reg().set_enabled(aid, bool(b.get("on")))
        if res.get("ok"):
            await changed("customagents.enable", f"{aid} {'enabled' if b.get('on') else 'disabled'}")
        return res

    @r.post("/api/customagents/{aid}/duplicate")
    async def duplicate(aid: str):
        if not commander():
            return _locked()
        res = reg().duplicate(aid)
        if res.get("ok"):
            await changed("customagents.duplicate", f"{aid} → {res['agent']['id']} (disabled)")
        return res

    @r.post("/api/customagents/{aid}/delete")
    async def delete(aid: str):
        if not commander():
            return _locked()
        res = reg().delete(aid)
        if res.get("ok"):
            await changed("customagents.delete", aid)
        return res

    @r.post("/api/customagents/{aid}/test")
    async def test(aid: str, request: Request):
        """A real prompt through the real reply path (engine → this agent → its model / tools)."""
        if not commander():
            return _locked()
        g = reg()
        b = await _body(request)
        prompt = str(b.get("prompt") or "").strip()[:LIMITS["prompt"]]
        if not prompt:
            return {"ok": False, "error": "type a prompt to test"}
        member = (state.get("team") or {}).get(aid)
        if g.get(aid) is None:
            return {"ok": False, "error": "unknown custom agent"}
        if getattr(member, "custom", False) is not True:
            return {"ok": False, "error": "this agent is disabled — enable it to test it"}
        engine = state.get("engine")
        if engine is None:
            return {"ok": False, "error": "the real-time engine is not running"}
        t0 = time.perf_counter()
        cmd = await engine.submit(prompt, source="api", agent=aid, wait=True, speak=False,
                                  host=ctx["host_only"](request))
        secs = []
        try:
            import dashboard_context
            if dashboard_context.LAST.get("cmd_id") == cmd.id:
                secs = list(dashboard_context.LAST.get("sections") or [])
        except Exception:
            pass
        await g.audit("customagents.test", f"{aid} · {cmd.path or 'no route'}")
        return {"ok": not cmd.error, "error": cmd.error or None, "reply": cmd.reply, "agent": cmd.handled_by,
                "path": cmd.path, "ms": round((time.perf_counter() - t0) * 1000),
                "first_token_ms": cmd.marks.get("first_token_ms"), "sections": secs,
                "fallback": bool(member.last.get("fallback")), "steps": member.last.get("steps") or [],
                "approvals": member.last.get("approvals") or []}

    return r


async def start(ctx) -> None:
    """Put the stored agents on the team as soon as the team exists."""
    state = ctx["state"]
    for _ in range(120):
        if state.get("team") is not None:
            break
        await asyncio.sleep(0.5)
    try:
        get(state, ctx).sync()
    except Exception as e:
        print(f"[customagents] sync failed: {type(e).__name__}: {e}")
