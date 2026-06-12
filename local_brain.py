"""Templated fallback brain that produces contextual, varied responses using
real-time system data. No LLM required — used when the `claude` CLI is
unavailable, and for the cheap periodic ticks of every agent."""

import os
import platform
import random
import time
from datetime import datetime
from pathlib import Path


def _greeting() -> str:
    h = datetime.now().hour
    if h < 5:   return "Late night, sir"
    if h < 12:  return "Good morning, sir"
    if h < 17:  return "Good afternoon, sir"
    if h < 21:  return "Good evening, sir"
    return "Evening, sir"


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _scan_folder(path: Path, max_items: int = 5000) -> tuple[int, int]:
    """Cheap folder scan: returns (file_count, total_bytes). Bounded by max_items."""
    count, total = 0, 0
    try:
        for entry in path.rglob("*"):
            if count >= max_items:
                break
            try:
                if entry.is_file():
                    count += 1
                    total += entry.stat().st_size
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass
    return count, total


class LocalBrain:
    """Drop-in brain that returns context-aware responses without an LLM."""

    def __init__(self, sysmon=None, news=None) -> None:
        self.sysmon = sysmon
        self.news = news
        self._stark_step = 0
        self._widow_step = 0
        self._hulk_step = 0
        self._vision_step = 0

    # ── public API ─────────────────────────────────────────────
    def for_agent(self, agent: str, prompt: str = "") -> str:
        fn = {
            "jarvis":  self._jarvis,
            "captain": self._captain,
            "stark":   self._stark,
            "widow":   self._widow,
            "hawkeye": self._hawkeye,
            "hulk":    self._hulk,
            "thor":    self._thor,
            "vision":  self._vision,
        }.get(agent, self._jarvis)
        try:
            return fn(prompt)
        except Exception as e:
            return f"Standing by, sir. [local-brain note: {e}]"

    # ── helpers ────────────────────────────────────────────────
    def _m(self) -> dict:
        return (self.sysmon.latest if self.sysmon else {}) or {}

    def _articles(self) -> list[dict]:
        return list(self.news.recent) if self.news else []

    # ── per-agent voices ──────────────────────────────────────
    def _jarvis(self, prompt: str) -> str:
        m = self._m()
        cpu, mem = m.get("cpu", 0), m.get("mem", 0)
        if prompt:
            return (
                f"{_greeting()}. Brain core offline — running on local routines. "
                f"You said: \"{prompt[:90]}\". I'll log it for review."
            )
        return random.choice([
            f"{_greeting()}. All eight Avengers active and synchronized.",
            f"Systems nominal, sir. CPU {cpu:.0f}%, memory {mem:.0f}%.",
            f"Standing by. Nothing requires your attention at this moment.",
            f"{_greeting()}. Diagnostics green across the board.",
        ])

    def _captain(self, prompt: str) -> str:
        now = datetime.now()
        next_hour = (now.replace(minute=0, second=0, microsecond=0)
                     .replace(hour=(now.hour + 1) % 24))
        mins = int((next_hour - now).total_seconds() // 60)
        return random.choice([
            f"Schedule check: next hourly announcement in {mins} minutes.",
            f"Routine queue clear. Standing by for orders.",
            f"All units reporting on schedule. No delays.",
            f"It is {now.strftime('%H:%M')}. Discipline holding.",
        ])

    def _stark(self, prompt: str) -> str:
        m = self._m()
        cpu, mem = m.get("cpu", 0), m.get("mem", 0)
        disk, up, down = m.get("disk", 0), m.get("net_up", 0), m.get("net_down", 0)
        pool = [
            f"CPU at {cpu:.0f}%. {self._cpu_comment(cpu)}",
            f"Memory load: {mem:.0f}%. {self._mem_comment(mem)}",
            f"Network: ↑{up:.0f} KB/s ↓{down:.0f} KB/s. Pipes are flowing.",
            f"Disk usage at {disk:.0f}%. {self._disk_comment(disk)}",
            f"Everything humming within tolerance. Mark four armor would approve.",
        ]
        self._stark_step += 1
        return pool[self._stark_step % len(pool)]

    def _widow(self, prompt: str) -> str:
        arts = self._articles()
        if not arts:
            return random.choice([
                "World feed dark — no news source configured. Set NEWSAPI_KEY in your .env.",
                "Intel quiet. Awaiting fresh signal.",
                "No new headlines pulled. Reattempting on next cycle.",
            ])
        self._widow_step += 1
        art = arts[self._widow_step % len(arts)]
        return f"Intel update: {art['title']} — via {art.get('source', 'unknown')}."

    def _hawkeye(self, prompt: str) -> str:
        m = self._m()
        cpu, mem, disk = m.get("cpu", 0), m.get("mem", 0), m.get("disk", 0)
        alerts = []
        if cpu > 85:  alerts.append(f"CPU {cpu:.0f}%")
        if mem > 85:  alerts.append(f"MEM {mem:.0f}%")
        if disk > 90: alerts.append(f"DISK {disk:.0f}%")
        if alerts:
            return f"Eyes on target. Vitals elevated: {' / '.join(alerts)}."
        return random.choice([
            f"Vitals nominal. CPU {cpu:.0f}%, MEM {mem:.0f}%, DISK {disk:.0f}%.",
            "All clear. Nothing on the scope.",
            "Watching. Nothing moves without me seeing it.",
        ])

    def _hulk(self, prompt: str) -> str:
        # Real work: scan a small folder each tick. Rotates through a few.
        targets = [
            Path.home() / "Desktop",
            Path.home() / "Downloads",
            Path.home() / "Documents",
        ]
        self._hulk_step += 1
        target = targets[self._hulk_step % len(targets)]
        if not target.exists():
            return f"Target folder unreachable. Hulk will try another."
        count, size = _scan_folder(target, max_items=2000)
        return f"Smashed through {target.name}: {count} files, {_human_bytes(size)}."

    def _thor(self, prompt: str) -> str:
        now = datetime.now()
        hour_str = now.strftime("%H:%M")
        return random.choice([
            f"Hear me — the hour stands at {hour_str}. All realms calm.",
            f"By Mjolnir, the time is {hour_str}. Asgard sleeps; Midgard watches.",
            f"The clock turns to {hour_str}. Maintain your post, mortal.",
            f"At {hour_str}, the watch continues unbroken.",
        ])

    def _vision(self, prompt: str) -> str:
        self._vision_step += 1
        m = self._m()
        arts = self._articles()
        now = datetime.now().strftime("%H:%M")
        bits = []
        if m:
            bits.append(f"CPU {m.get('cpu', 0):.0f}%, memory {m.get('mem', 0):.0f}%")
        if arts:
            bits.append(f"{len(arts)} headlines tracked")
        if not bits:
            return "Synthesis incomplete. Awaiting more data."
        return f"Synthesis at {now}: {'; '.join(bits)}. Picture remains stable."

    # ── tone helpers ───────────────────────────────────────────
    @staticmethod
    def _cpu_comment(v: float) -> str:
        if v < 20: return "Plenty of headroom."
        if v < 60: return "Comfortable load."
        if v < 85: return "Working warm but fine."
        return "Approaching saturation — recommend triage."

    @staticmethod
    def _mem_comment(v: float) -> str:
        if v < 50: return "Memory ample."
        if v < 80: return "Memory steady."
        if v < 90: return "Memory tight — close idle tabs if convenient."
        return "Memory critical — paging imminent."

    @staticmethod
    def _disk_comment(v: float) -> str:
        if v < 70: return "Storage healthy."
        if v < 90: return "Storage filling up."
        return "Storage nearly exhausted — recommend a sweep."
