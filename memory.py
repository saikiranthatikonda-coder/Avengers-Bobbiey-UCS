"""Operator Memory engine.

A persistent, human-readable memory (operator_memory.json) that learns who the
operator is and how they work — and keeps updating itself:

  profile        name + a TEXT appearance sketch written by the vision AI at
                 enrollment. Recognition is description-based (words, not
                 biometrics): no face embeddings, no identity databases. The
                 operator can read/delete everything — it's just JSON.
  facts          stable things learned about the operator (from vision
                 observations, synthesized periodically by the brain, or added
                 manually / by voice).
  observations   rolling log of AI vision observations (who + what).
  patterns       behavioural telemetry: per-hour activity histogram, arrivals,
                 sessions, voice-command frequencies.

The file lives next to the app and is gitignored — it never leaves the machine.
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

MEMORY_FILE = Path(__file__).parent / "operator_memory.json"

_DEFAULT = {
    "profile": {"name": None, "appearance": None, "enrolled_at": None},
    "facts": [],           # [{text, ts, source}]
    "observations": [],    # [{ts, who, text}]
    "patterns": {
        "hourly_activity": [0] * 24,   # active minutes per hour (today)
        "hourly_date": None,           # date the histogram belongs to
        "arrivals_today": 0,
        "arrivals_date": None,
        "sessions_total": 0,
        "voice_commands": {},          # command → count
        "guest_sightings": 0,
    },
    "stats": {"created": None, "updated": None, "syntheses": 0},
}


class OperatorMemory:
    def __init__(self, hub=None, brain=None) -> None:
        self.hub = hub
        self.brain = brain
        self.data = json.loads(json.dumps(_DEFAULT))   # deep copy
        self._load()
        if not self.data["stats"]["created"]:
            self.data["stats"]["created"] = time.time()
        self._roll_day()

    # ── persistence ───────────────────────────────────────────────
    def _load(self) -> None:
        try:
            if MEMORY_FILE.exists():
                stored = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
                for k in _DEFAULT:
                    if k in stored:
                        self.data[k] = stored[k]
                # backfill any new keys
                for k, v in _DEFAULT["patterns"].items():
                    self.data["patterns"].setdefault(k, json.loads(json.dumps(v)))
        except Exception:
            pass

    def save(self) -> None:
        try:
            self.data["stats"]["updated"] = time.time()
            MEMORY_FILE.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass

    async def _announce(self) -> None:
        if self.hub:
            await self.hub.broadcast({"type": "memory-updated"})

    def _roll_day(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        p = self.data["patterns"]
        if p.get("hourly_date") != today:
            p["hourly_activity"] = [0] * 24
            p["hourly_date"] = today
        if p.get("arrivals_date") != today:
            p["arrivals_today"] = 0
            p["arrivals_date"] = today

    # ── enrollment / identity (text description, not biometric) ──
    async def enroll(self, appearance: str, name: str | None = None) -> None:
        self.data["profile"]["appearance"] = appearance.strip()[:600]
        if name:
            self.data["profile"]["name"] = name.strip()[:60]
        self.data["profile"]["enrolled_at"] = time.time()
        self.save()
        await self._announce()

    async def forget(self) -> None:
        self.data = json.loads(json.dumps(_DEFAULT))
        self.data["stats"]["created"] = time.time()
        try:
            MEMORY_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        self.save()
        await self._announce()

    @property
    def enrolled(self) -> bool:
        return bool(self.data["profile"].get("appearance"))

    @property
    def name(self) -> str | None:
        return self.data["profile"].get("name")

    # ── recorders (every interaction teaches it something) ───────
    async def record_observation(self, text: str, who: str = "unknown") -> None:
        self._roll_day()
        self.data["observations"] = ([{
            "ts": time.time(), "who": who, "text": text.strip()[:280],
        }] + self.data["observations"])[:60]
        if who == "guest":
            self.data["patterns"]["guest_sightings"] += 1
        self.save()
        await self._announce()

    async def record_presence(self, state: str, prev: str) -> None:
        self._roll_day()
        p = self.data["patterns"]
        if state == "active" and prev in ("away", "offline", "no-user"):
            p["arrivals_today"] += 1
            p["sessions_total"] += 1
            self.save()
            await self._announce()

    def record_command(self, command: str) -> None:
        cmd = (command or "").strip().lower()[:60]
        if not cmd:
            return
        vc = self.data["patterns"]["voice_commands"]
        vc[cmd] = vc.get(cmd, 0) + 1
        # keep the map bounded — drop the rarest when large
        if len(vc) > 40:
            rarest = min(vc, key=vc.get)
            vc.pop(rarest, None)
        self.save()

    def add_active_minute(self) -> None:
        self._roll_day()
        hour = datetime.now().hour
        self.data["patterns"]["hourly_activity"][hour] += 1
        # save lazily — once a minute is fine
        self.save()

    async def add_fact(self, text: str, source: str = "manual") -> None:
        text = (text or "").strip()[:240]
        if not text:
            return
        # dedupe on near-identical text
        low = text.lower()
        if any(f["text"].lower() == low for f in self.data["facts"]):
            return
        self.data["facts"] = ([{
            "text": text, "ts": time.time(), "source": source,
        }] + self.data["facts"])[:40]
        self.save()
        await self._announce()

    # ── periodic synthesis: observations → stable facts ──────────
    async def synthesize(self) -> None:
        """Ask the brain to distill recent observations + patterns into new
        stable facts about the operator. Runs on a schedule."""
        if not self.brain:
            return
        recent = [o for o in self.data["observations"][:15] if o["who"] != "guest"]
        if len(recent) < 3:
            return
        obs_text = "\n".join(f"- {o['text']}" for o in recent)
        known = "\n".join(f"- {f['text']}" for f in self.data["facts"][:12]) or "(none yet)"
        p = self.data["patterns"]
        busiest = max(range(24), key=lambda h: p["hourly_activity"][h])
        top_cmds = sorted(p["voice_commands"].items(), key=lambda x: -x[1])[:3]
        prompt = (
            "You maintain a memory file about your operator. From the recent webcam "
            "observations and behaviour below, extract AT MOST 2 NEW stable facts "
            "about the operator worth remembering long-term (habits, style, "
            "environment, work patterns). Do not repeat known facts. No identity "
            "speculation. Reply with one fact per line, no bullets; reply NONE if "
            "nothing new.\n\n"
            f"KNOWN FACTS:\n{known}\n\nRECENT OBSERVATIONS:\n{obs_text}\n\n"
            f"BEHAVIOUR: busiest hour {busiest}:00; arrivals today "
            f"{p['arrivals_today']}; frequent commands: "
            + (", ".join(c for c, _ in top_cmds) or "none")
        )
        try:
            reply = await self.brain.think(
                prompt, system="You are JARVIS's memory subsystem. Be terse and factual.",
                agent="vision", timeout=60, fast=True)
        except Exception:
            return
        if not reply or reply.lstrip().startswith("[") or "NONE" in reply.upper()[:12]:
            return
        added = 0
        for line in reply.splitlines():
            line = line.strip(" -•\t")
            if 8 < len(line) < 240 and added < 2:
                await self.add_fact(line, source="ai-synthesis")
                added += 1
        if added:
            self.data["stats"]["syntheses"] += 1
            self.save()

    # ── summaries ─────────────────────────────────────────────────
    def summary_text(self) -> str:
        prof = self.data["profile"]
        p = self.data["patterns"]
        name = prof.get("name") or "sir"
        bits = []
        if self.enrolled:
            bits.append(f"You are enrolled as {name}.")
        facts = [f["text"] for f in self.data["facts"][:4]]
        if facts:
            bits.append("What I know: " + "; ".join(facts) + ".")
        act = sum(p["hourly_activity"])
        if act:
            busiest = max(range(24), key=lambda h: p["hourly_activity"][h])
            bits.append(f"Today: about {act} active minutes, busiest around {busiest}:00, "
                        f"{p['arrivals_today']} arrivals.")
        if not bits:
            bits.append("My memory of you is just getting started, sir — "
                        "enroll on camera and I will learn as we work.")
        return " ".join(bits)[:500]

    def snapshot(self) -> dict:
        self._roll_day()
        p = self.data["patterns"]
        top_cmds = sorted(p["voice_commands"].items(), key=lambda x: -x[1])[:5]
        return {
            "enrolled": self.enrolled,
            "name": self.data["profile"].get("name"),
            "appearance": self.data["profile"].get("appearance"),
            "enrolled_at": self.data["profile"].get("enrolled_at"),
            "facts": self.data["facts"][:12],
            "observations": self.data["observations"][:12],
            "hourly_activity": p["hourly_activity"],
            "arrivals_today": p["arrivals_today"],
            "sessions_total": p["sessions_total"],
            "guest_sightings": p["guest_sightings"],
            "top_commands": top_cmds,
            "active_minutes_today": sum(p["hourly_activity"]),
            "updated": self.data["stats"].get("updated"),
            "syntheses": self.data["stats"].get("syntheses", 0),
        }
