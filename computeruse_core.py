"""COMPUTER USE · pure logic (no screen, no model, no I/O).

Everything here is deterministic and unit-tested (tests/test_computeruse.py):
  * the action schema every model adapter is normalised into, and its validation;
  * coordinate mapping between the downscaled frame the model sees and the real
    input coordinates (physical pixels on Windows, points on macOS), multi-monitor;
  * the SENSITIVE-ACTION GATE: payments, credentials, deletes, sends, installs,
    system settings and irreversible choices pause for the operator; secrets
    (card numbers, passwords, keys, government IDs) are never typed by the agent;
  * the task state machine (pause / resume / stop / take over / hand back /
    approval / ask-user) and the action rate limiter.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from dataclasses import dataclass, field

# ═══ ACTION SCHEMA ═══════════════════════════════════════════════════════════

CLICKS = ("click", "double_click", "right_click", "middle_click", "triple_click")
POINTER = CLICKS + ("move",)
ACTION_TYPES = POINTER + (
    "drag", "scroll", "type", "key", "open_app", "open_url", "wait", "read_file",
    "list_dir", "screenshot", "zoom", "done", "ask_user", "mouse_down", "mouse_up",
    "hold_key", "cursor_position",
)
# actions the JSON adapters may use (the native toolset adds zoom / mouse_down|up / hold_key / cursor_position)
JSON_ACTION_TYPES = POINTER + ("drag", "scroll", "type", "key", "open_app", "open_url", "wait",
                               "read_file", "list_dir", "screenshot", "done", "ask_user")
# actions that change something on the machine — the gate inspects these
EFFECTFUL = CLICKS + ("drag", "type", "key", "open_app", "open_url", "mouse_down", "hold_key")

MAX_TYPE = 500
MODIFIERS = ("ctrl", "shift", "alt", "win", "command", "option")
KEY_ALIASES = {
    "return": "enter", "escape": "esc", "control": "ctrl", "ctl": "ctrl", "del": "delete",
    "super": "win", "meta": "win", "cmd": "win", "windows": "win", "start": "win",
    "page_up": "pageup", "page_down": "pagedown", "pgup": "pageup", "pgdn": "pagedown",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "arrow_up": "up", "arrow_down": "down", "arrow_left": "left", "arrow_right": "right",
    "spacebar": "space", " ": "space", "bksp": "backspace", "back_space": "backspace",
    "ins": "insert", "prtsc": "printscreen", "print": "printscreen", "kp_enter": "enter",
    "caps_lock": "capslock", "num_lock": "numlock", "opt": "option", "plus": "+", "minus": "-",
}
NAMED_KEYS = {
    "enter", "esc", "tab", "space", "backspace", "delete", "insert", "home", "end", "pageup",
    "pagedown", "up", "down", "left", "right", "capslock", "numlock", "printscreen", "menu",
    "volumeup", "volumedown", "volumemute", "playpause",
} | {f"f{i}" for i in range(1, 25)} | set(MODIFIERS)
# combos the agent may never send (OS security attention / emergency-stop hotkey / lock)
BLOCKED_COMBOS = {
    frozenset({"ctrl", "alt", "delete"}), frozenset({"ctrl", "alt", "backspace"}),
    frozenset({"ctrl", "shift", "esc"}), frozenset({"win", "l"}),
}
EMERGENCY_HOTKEY = "Ctrl+Alt+Backspace"


class ActionError(ValueError):
    """The model proposed something outside the schema."""


def _int(v, name: str) -> int:
    if isinstance(v, bool) or v is None:
        raise ActionError(f"{name} must be a number")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ActionError(f"{name} must be a number") from None
    if not math.isfinite(f):
        raise ActionError(f"{name} must be finite")
    return int(round(f))


def _xy(raw: dict, w: int, h: int, kx="x", ky="y", required=True) -> tuple[int, int] | None:
    if kx not in raw and ky not in raw:
        c = raw.get("coordinate") if kx == "x" else raw.get("end_coordinate")
        if isinstance(c, (list, tuple)) and len(c) == 2:
            raw = {kx: c[0], ky: c[1]}
        elif not required:
            return None
        else:
            raise ActionError(f"{kx},{ky} are required")
    x, y = _int(raw.get(kx), kx), _int(raw.get(ky), ky)
    if not (0 <= x < w and 0 <= y < h):
        raise ActionError(f"({x},{y}) is outside the {w}x{h} screenshot")
    return x, y


def normalize_key(name: str) -> str:
    k = str(name or "").strip().lower()
    k = KEY_ALIASES.get(k, k)
    if k in NAMED_KEYS or (len(k) == 1 and k.isprintable()):
        return k
    raise ActionError(f"unknown key '{name}'")


def parse_keys(spec) -> list[str]:
    """'ctrl+s' / 'Ctrl + Shift + T' / ['ctrl','s'] / 'Return' → normalised key list."""
    if isinstance(spec, (list, tuple)):
        parts = [str(p) for p in spec]
    else:
        s = str(spec or "").strip()
        if not s:
            raise ActionError("keys are required")
        if s in ("+", "-"):
            parts = [s]
        else:
            parts = [p for p in re.split(r"\s*\+\s*", s) if p != ""]
            if s.endswith("++"):
                parts.append("+")
    keys = [normalize_key(p) for p in parts]
    if not keys or len(keys) > 4:
        raise ActionError("a key combo has 1–4 keys")
    if frozenset(keys) in BLOCKED_COMBOS:
        raise ActionError(f"'{'+'.join(keys)}' is reserved for the operator")
    return keys


def _mods(v) -> list[str]:
    if not v:
        return []
    out = []
    for p in re.split(r"\s*\+\s*", str(v).strip().lower()):
        p = KEY_ALIASES.get(p, p)
        if p not in MODIFIERS:
            raise ActionError(f"'{p}' is not a modifier key")
        out.append(p)
    return out


def validate_action(raw, w: int, h: int, allowed: tuple = ACTION_TYPES) -> dict:
    """Normalise one proposed action into the internal schema or raise ActionError.
    Coordinates are in the *frame* (screenshot) pixel space, w×h."""
    if not isinstance(raw, dict):
        raise ActionError("action must be a JSON object")
    t = str(raw.get("type") or raw.get("action") or "").strip().lower()
    t = {"left_click": "click", "mouse_move": "move", "left_click_drag": "drag", "press": "key",
         "hotkey": "key", "keypress": "key", "type_text": "type", "finish": "done", "complete": "done",
         "left_mouse_down": "mouse_down", "left_mouse_up": "mouse_up", "ask": "ask_user",
         "open": "open_app", "launch": "open_app", "navigate": "open_url", "sleep": "wait"}.get(t, t)
    if t not in allowed:
        raise ActionError(f"unknown action '{t or '?'}' (allowed: {', '.join(allowed)})")
    a: dict = {"type": t}
    if t in POINTER:
        a["x"], a["y"] = _xy(raw, w, h)
        if t != "move":
            a["modifiers"] = _mods(raw.get("modifiers") or raw.get("text") if t in CLICKS else None)
    elif t == "drag":
        if "start_coordinate" in raw and "coordinate" in raw:
            s, e = raw["start_coordinate"], raw["coordinate"]
            if not (isinstance(s, (list, tuple)) and isinstance(e, (list, tuple)) and len(s) == len(e) == 2):
                raise ActionError("drag needs start_coordinate and coordinate as [x, y]")
            raw = {"x": s[0], "y": s[1], "x2": e[0], "y2": e[1]}
        a["x"], a["y"] = _xy(raw, w, h)
        a["x2"], a["y2"] = _xy(raw, w, h, "x2", "y2")
    elif t == "scroll":
        d = str(raw.get("direction") or raw.get("scroll_direction") or "").lower()
        if d not in ("up", "down", "left", "right"):
            raise ActionError("scroll direction must be up/down/left/right")
        a["direction"] = d
        a["amount"] = max(1, min(20, _int(raw.get("amount", raw.get("scroll_amount", 3)), "amount")))
        xy = _xy(raw, w, h, required=False)
        if xy:
            a["x"], a["y"] = xy
    elif t == "type":
        text = raw.get("text")
        if not isinstance(text, str) or not text:
            raise ActionError("type needs non-empty text")
        if len(text) > MAX_TYPE:
            raise ActionError(f"type text is limited to {MAX_TYPE} characters per action")
        if any(ord(c) < 32 and c not in "\n\t" for c in text):
            raise ActionError("type text contains control characters")
        a["text"] = text
    elif t in ("key", "hold_key"):
        a["keys"] = parse_keys(raw.get("keys", raw.get("key", raw.get("text"))))
        if t == "key":
            a["repeat"] = max(1, min(20, _int(raw.get("repeat", 1), "repeat")))
        else:
            a["seconds"] = max(0.1, min(5.0, float(raw.get("duration", raw.get("seconds", 1)) or 1)))
    elif t == "open_app":
        app = str(raw.get("app") or raw.get("name") or "").strip()
        if not app or len(app) > 60 or not re.fullmatch(r"[\w .+&'-]+", app):
            raise ActionError("open_app needs a plain application name")
        a["app"] = app
    elif t == "open_url":
        url = str(raw.get("url") or "").strip()
        if not re.match(r"^https?://[^\s\"'<>]+$", url, re.I) or len(url) > 2000:
            raise ActionError("open_url needs an http(s) URL")
        a["url"] = url
    elif t == "wait":
        try:
            s = float(raw.get("seconds", raw.get("duration", 1)))
        except (TypeError, ValueError):
            raise ActionError("wait seconds must be a number") from None
        a["seconds"] = max(0.1, min(30.0, s if math.isfinite(s) else 1.0))
    elif t in ("read_file", "list_dir"):
        p = str(raw.get("path") or "").strip()
        if not p or len(p) > 500 or "\x00" in p:
            raise ActionError(f"{t} needs a path")
        a["path"] = p
    elif t == "zoom":
        r = raw.get("region")
        if not (isinstance(r, (list, tuple)) and len(r) == 4):
            raise ActionError("zoom needs region [x0, y0, x1, y1]")
        x0, y0 = _xy({"x": r[0], "y": r[1]}, w + 1, h + 1)
        x1, y1 = _xy({"x": r[2], "y": r[3]}, w + 1, h + 1)
        if x1 - x0 < 4 or y1 - y0 < 4:
            raise ActionError("zoom region is too small")
        a["region"] = [x0, y0, x1, y1]
    elif t == "done":
        a["summary"] = str(raw.get("summary") or raw.get("text") or "task complete")[:1000]
    elif t == "ask_user":
        q = str(raw.get("question") or raw.get("text") or "").strip()
        if not q:
            raise ActionError("ask_user needs a question")
        a["question"] = q[:600]
    return a


def describe(a: dict) -> str:
    """One human line for the history list / audit log (typed text truncated)."""
    t = a.get("type")
    if t in POINTER:
        m = "+".join(a.get("modifiers") or [])
        return f"{t} at ({a['x']},{a['y']})" + (f" with {m}" if m else "")
    if t == "drag":
        return f"drag ({a['x']},{a['y']}) → ({a['x2']},{a['y2']})"
    if t == "scroll":
        return f"scroll {a['direction']} ×{a['amount']}" + (f" at ({a['x']},{a['y']})" if "x" in a else "")
    if t == "type":
        s = a["text"].replace("\n", "⏎")
        return f'type "{s[:60]}{"…" if len(s) > 60 else ""}"'
    if t == "key":
        return f"key {'+'.join(a['keys'])}" + (f" ×{a['repeat']}" if a.get("repeat", 1) > 1 else "")
    if t == "hold_key":
        return f"hold {'+'.join(a['keys'])} {a['seconds']:.1f}s"
    if t == "open_app":
        return f"open app {a['app']}"
    if t == "open_url":
        return f"open {a['url'][:120]}"
    if t == "wait":
        return f"wait {a['seconds']:.1f}s"
    if t in ("read_file", "list_dir"):
        return f"{t} {a['path'][:160]}"
    if t == "zoom":
        return f"zoom {a['region']}"
    if t == "done":
        return "done: " + a["summary"][:160]
    if t == "ask_user":
        return "ask: " + a["question"][:160]
    return t or "?"


# ═══ JSON REPLY PARSING (OpenAI-compatible / Ollama / Claude CLI adapters) ═══

def extract_json(text: str) -> dict:
    """First balanced JSON object in a model reply (code fences / prose tolerated)."""
    import json
    s = str(text or "")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    start = s.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            c = s[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(s[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        pass
                    break
        start = s.find("{", start + 1)
    raise ActionError("the model reply contained no JSON object")


def parse_model_reply(text: str, w: int, h: int) -> dict:
    """→ {"reasoning", "action", "sensitive", "sensitive_reason"} (validated)."""
    obj = extract_json(text)
    act = obj.get("action")
    if isinstance(act, str):                      # {"action": "click", "x": .., "y": ..}
        act = {**obj, "type": act}
    if not isinstance(act, dict):
        raise ActionError("reply needs an 'action' object")
    return {
        "reasoning": str(obj.get("reasoning") or obj.get("thought") or "")[:2000],
        "action": validate_action(act, w, h, JSON_ACTION_TYPES),
        "sensitive": bool(obj.get("sensitive")),
        "sensitive_reason": str(obj.get("sensitive_reason") or "")[:300],
    }


# ═══ COORDINATES ═════════════════════════════════════════════════════════════

def fit_size(w: int, h: int, long_edge: int = 1280, max_pixels: int = 1_150_000) -> tuple[int, int]:
    """Downscaled frame size that fits every vision model's limits (never upscales)."""
    s = min(1.0, long_edge / max(w, h), math.sqrt(max_pixels / (w * h)))
    return max(1, int(w * s)), max(1, int(h * s))


@dataclass
class Frame:
    """One captured screen. mon_* are INPUT coordinates of the monitor (Windows: physical
    pixels with per-monitor DPI awareness; macOS: points), cap_* the captured bitmap size
    (Retina = 2× points), img_* the downscaled image the model sees."""
    mon_left: int
    mon_top: int
    mon_w: int
    mon_h: int
    img_w: int
    img_h: int
    cap_w: int = 0
    cap_h: int = 0
    path: str = ""
    jpeg: bytes = b""
    step: int = 0
    ts: float = field(default_factory=time.time)
    thumb: bytes = b""          # tiny grayscale bitmap for change detection

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        sx = self.mon_left + int(round((x + 0.5) * self.mon_w / self.img_w - 0.5))
        sy = self.mon_top + int(round((y + 0.5) * self.mon_h / self.img_h - 0.5))
        sx = min(max(sx, self.mon_left), self.mon_left + self.mon_w - 1)
        sy = min(max(sy, self.mon_top), self.mon_top + self.mon_h - 1)
        return sx, sy

    def to_frame(self, sx: float, sy: float) -> tuple[int, int]:
        x = int(round((sx - self.mon_left + 0.5) * self.img_w / self.mon_w - 0.5))
        y = int(round((sy - self.mon_top + 0.5) * self.img_h / self.mon_h - 0.5))
        return min(max(x, 0), self.img_w - 1), min(max(y, 0), self.img_h - 1)

    def meta(self) -> dict:
        return {"step": self.step, "w": self.img_w, "h": self.img_h, "ts": self.ts,
                "monitor": [self.mon_left, self.mon_top, self.mon_w, self.mon_h]}


def avoid_corners(x: int, y: int, primary: tuple[int, int, int, int], margin: int = 6) -> tuple[int, int]:
    """Keep the agent's own pointer out of the primary screen's fail-safe corners, so the
    corner emergency stop always means the HUMAN slammed the mouse there."""
    l, t, w, h = primary
    corners = ((l, t), (l + w - 1, t), (l, t + h - 1), (l + w - 1, t + h - 1))
    for cx, cy in corners:
        if abs(x - cx) < margin and abs(y - cy) < margin:
            x = cx + margin if cx == l else cx - margin
            y = cy + margin if cy == t else cy - margin
    return x, y


def in_corner(x: int, y: int, primary: tuple[int, int, int, int], tol: int = 2) -> bool:
    l, t, w, h = primary
    return any(abs(x - cx) <= tol and abs(y - cy) <= tol
               for cx, cy in ((l, t), (l + w - 1, t), (l, t + h - 1), (l + w - 1, t + h - 1)))


# ═══ SENSITIVE-ACTION GATE ═══════════════════════════════════════════════════

ALLOW, APPROVE, REFUSE = "allow", "approve", "refuse"


@dataclass
class GateResult:
    verdict: str
    category: str = ""
    reason: str = ""

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "category": self.category, "reason": self.reason}


def _rx(*words: str) -> re.Pattern:
    return re.compile(r"(?<![\w])(?:" + "|".join(words) + r")(?![\w])", re.I)


CATEGORIES: list[tuple[str, str, re.Pattern]] = [
    ("payment", "payment / purchase", _rx(
        r"pay", r"pays", r"paying", r"payment\w*", r"purchas\w*", r"buy", r"buying", r"checkout",
        r"check\s*out", r"place\s+(?:the\s+|my\s+|your\s+)?order", r"order\s+now", r"confirm\s+order",
        r"subscribe\w*", r"subscription", r"donat\w*", r"billing", r"credit\s+card", r"debit\s+card",
        r"card\s+number", r"upi", r"net\s*banking", r"wallet", r"transfer\s+(?:money|funds)",
        r"send\s+money", r"wire\s+transfer", r"invoice", r"add\s+funds", r"top\s*up", r"pay\s+now",
        r"proceed\s+to\s+pay\w*", r"crypto\w*", r"bitcoin")),
    ("credentials", "credentials / sign-in", _rx(
        r"password\w*", r"passcode", r"passphrase", r"pin\s+(?:code|number)", r"credential\w*",
        r"sign\s*in", r"log\s*in", r"login", r"2fa", r"mfa", r"otp", r"one[-\s]time\s+(?:code|password)",
        r"verification\s+code", r"security\s+code", r"api\s+key", r"secret\s+key", r"access\s+token",
        r"authenticat\w*", r"sign\s*up", r"create\s+(?:an\s+)?account")),
    ("delete", "delete / move files", re.compile(
        r"(?<![\w])(?:delet\w*|remov\w*|eras\w*|trash\w*|recycle\s+bin|wipe\w*|shred\w*|uninstall\w*|"
        r"format\s+(?:the\s+)?(?:drive|disk|partition)|empty\s+(?:the\s+)?(?:trash|bin|recycle))(?![\w])|"
        r"(?<![\w])(?:mov\w*|renam\w*|cut)\b[^.\n]{0,60}\b(?:files?|folders?|director\w*|documents?)(?![\w])", re.I)),
    ("send", "send / post / submit", _rx(
        r"send", r"sends", r"sending", r"post", r"posting", r"publish\w*", r"tweet\w*", r"reply", r"replying",
        r"forward", r"forwarding", r"submit\w*", r"upload\w*", r"share", r"sharing", r"email\s+(?:it|this|them)",
        r"send\s+(?:an?\s+)?(?:email|message|dm)", r"comment\s+on", r"retweet", r"go\s+live", r"broadcast")),
    ("install", "install software", _rx(
        r"install\w*", r"setup\.exe", r"\.msi", r"\.pkg", r"\.dmg", r"\.exe", r"winget", r"choco",
        r"pip\s+install", r"npm\s+install", r"brew\s+install", r"run\s+as\s+administrator",
        r"admin(?:istrator)?\s+(?:rights|prompt|permission)", r"user\s+account\s+control", r"uac")),
    ("system", "system / security settings", _rx(
        r"settings", r"control\s+panel", r"system\s+preferences", r"system\s+settings", r"registry",
        r"regedit", r"firewall", r"defender", r"antivirus", r"group\s+policy", r"bios", r"privacy",
        r"security", r"permissions?", r"powershell", r"cmd(?:\.exe)?", r"command\s+prompt",
        r"terminal", r"task\s+manager", r"services\.msc", r"device\s+manager", r"run\s+dialog",
        r"sudo", r"disable\s+\w+", r"turn\s+off\s+\w+")),
    ("irreversible", "irreversible choice", _rx(
        r"don'?t\s+save", r"do\s+not\s+save", r"not\s+save", r"without\s+saving", r"discard\w*",
        r"overwrit\w*", r"replace\s+(?:the\s+)?(?:file|existing)", r"permanent\w*", r"irreversib\w*",
        r"cannot\s+be\s+undone", r"can'?t\s+be\s+undone", r"factory\s+reset", r"reset\s+(?:this\s+)?pc",
        r"confirm\s+(?:delet\w*|remov\w*|purchase|payment|order|reset|uninstall\w*)", r"yes,?\s+delete", r"close\s+all", r"end\s+task", r"force\s+(?:quit|close)",
        r"revoke\w*", r"cancel\s+(?:my\s+)?(?:subscription|order|account)", r"accept\s+(?:the\s+)?terms",
        r"i\s+agree", r"agree\s+(?:and|&)\s+continue")),
]
SENSITIVE_KEYS = {
    frozenset({"shift", "delete"}): ("delete", "Shift+Delete deletes permanently"),
    frozenset({"ctrl", "enter"}): ("send", "Ctrl+Enter usually sends / submits"),
    frozenset({"win", "r"}): ("system", "opens the Run dialog (command execution)"),
    frozenset({"win", "x"}): ("system", "opens the system power-user menu"),
    frozenset({"win", "i"}): ("system", "opens system Settings"),
    frozenset({"alt", "f4"}): ("irreversible", "closes the window (unsaved work may be lost)"),
    frozenset({"win", "command", "q"}): ("irreversible", "quits the application"),
}
SYSTEM_APPS = {"settings", "control panel", "task manager", "terminal", "windows terminal", "cmd",
               "powershell", "registry editor", "regedit", "system preferences", "system settings",
               "activity monitor", "disk utility", "keychain access"}

_SECRET_NOUN = re.compile(r"(?<![\w])(?:password\w*|passcode|passphrase|pin|cvv|cvc|otp|2fa|mfa|"
                          r"one[-\s]time|verification\s+code|security\s+code|secret|api\s+key|token|"
                          r"private\s+key|credential\w*|card\s+number|account\s+number|ssn|aadhaar|pan\s+number)(?![\w])", re.I)
_TYPE_VERB = re.compile(r"(?<![\w])(?:typ\w*|enter\w*|fill\w*|input\w*|paste\w*|provide|put|write|key\s+in|insert)(?![\w])", re.I)
_KEY_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"(?<![\w])sk-[A-Za-z0-9_\-]{16,}"), "an API secret key"),
    (re.compile(r"(?<![\w])(?:AKIA|ASIA)[A-Z0-9]{16}(?![\w])"), "a cloud access key"),
    (re.compile(r"(?<![\w])(?:ghp|gho|ghs|github_pat|glpat|xox[abpr])[-_][A-Za-z0-9_\-]{10,}"), "an access token"),
    (re.compile(r"(?<![\w])eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"), "a session token (JWT)"),
    (re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), "a social security number"),
    (re.compile(r"(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])"), "a PAN-style tax ID"),
]


def luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for c in reversed(digits):
        d = ord(c) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


_VD = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
       [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
       [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
       [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VP = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
       [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
       [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_ok(digits: str) -> bool:
    """Aadhaar numbers carry a Verhoeff check digit — keeps random 12-digit numbers out."""
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VD[c][_VP[i % 8][ord(ch) - 48]]
    return c == 0


def secret_in_text(text: str) -> str | None:
    """Return what kind of secret `text` looks like (the agent must never type it), or None."""
    s = str(text or "")
    for m in re.finditer(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)", s):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            return "a payment card number"
    for m in re.finditer(r"(?<![\d])(?<!\d[ -])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![ -]?\d)", s):
        if verhoeff_ok(re.sub(r"\D", "", m.group(0))):
            return "an Aadhaar national ID number"
    for rx, what in _KEY_PATTERNS:
        if rx.search(s):
            return what
    tok = s.strip()
    if (8 <= len(tok) <= 64 and not re.search(r"\s", tok) and "://" not in tok and "@" not in tok
            and not re.search(r"[\\/]", tok) and not re.search(r"\.[A-Za-z]{2,4}$", tok)):
        classes = sum(bool(re.search(p, tok)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
        if classes >= 3 and re.search(r"\d", tok):
            return "a password-like string"
    return None


def _sentences(text: str) -> list[str]:
    return [p for p in re.split(r"(?<=[.!?;\n])\s+", text or "") if p.strip()]


def check_action(action: dict, reasoning: str = "", sensitive: bool = False, sensitive_reason: str = "",
                 approve_all: bool = False) -> GateResult:
    """The gate. REFUSE beats APPROVE beats ALLOW. Context = the model's declared intent +
    its reasoning (which names the buttons it is about to press) + the action payload."""
    t = action.get("type")
    reasoning = str(reasoning or "")
    if t == "type":
        what = secret_in_text(action["text"])
        if what:
            return GateResult(REFUSE, "secret", f"the text looks like {what} — the agent never types secrets; "
                                                  "take over and enter it yourself")
        for sent in _sentences(reasoning + "\n" + sensitive_reason):
            if _SECRET_NOUN.search(sent) and _TYPE_VERB.search(sent):
                return GateResult(REFUSE, "secret", "the agent is about to enter a password / code / card detail — "
                                                    "it never types secrets; take over and enter it yourself")
    if t not in EFFECTFUL:
        return GateResult(ALLOW)
    payload = ""
    if t == "type":
        payload = action["text"]
    elif t == "open_url":
        payload = action["url"].replace("/", " ").replace("-", " ").replace("_", " ")
    elif t == "open_app":
        payload = action["app"]
        if action["app"].lower() in SYSTEM_APPS:
            return GateResult(APPROVE, "system", f"opening {action['app']} (system tools)")
    elif t in ("key", "hold_key"):
        hit = SENSITIVE_KEYS.get(frozenset(action["keys"]))
        if hit:
            return GateResult(APPROVE, hit[0], hit[1])
    if sensitive:
        cat = next((c for c, _, rx in CATEGORIES if rx.search(sensitive_reason)), "declared")
        return GateResult(APPROVE, cat, sensitive_reason or "the model flagged this step as sensitive")
    context = f"{reasoning}\n{payload}"
    for cat, label, rx in CATEGORIES:
        m = rx.search(context)
        if m:
            return GateResult(APPROVE, cat, f"{label}: “{m.group(0).strip()}”")
    if approve_all:
        return GateResult(APPROVE, "step", "step-by-step mode: every action needs approval")
    return GateResult(ALLOW)


# ═══ PRIVACY GUARD ═══════════════════════════════════════════════════════════
# A visible window whose title names a private file or a password manager stops the
# capture: that frame is never stored or sent to a model, and the human takes over.

PRIVATE_TITLE = re.compile(
    r"(?:^|[\s\\/\"'*•●(\[])(?:\.env(?:\.\w+)?|credentials\.json|token\.json|auth\.json|providers\.json|"
    r"operators\.json|license\.json|commander_memory\.md|operator_memory\.json|license_signing\.key|"
    r"licences_issued\.json|fleet_token\.txt|pair_code\.txt|node_token\.txt|id_rsa|id_ed25519|"
    r"[\w.-]+\.(?:pem|key|pfx|p12|kdbx|ovpn))(?=$|[\s\-–—*•●)\]:|])"
    r"|(?<![\w])(?:keepass(?:xc)?|1password|bitwarden|lastpass|dashlane|keeper\s+password|"
    r"credential\s+manager|keychain\s+access)(?![\w])", re.I)


def private_window(titles: list[str]) -> str | None:
    """First window title that shows private material, or None."""
    for t in titles or []:
        m = PRIVATE_TITLE.search(str(t or ""))
        if m:
            return m.group(0).strip(" \\/\"'*•●([")
    return None


# ═══ STATE MACHINE ═══════════════════════════════════════════════════════════

ACTIVE = ("starting", "thinking", "acting", "awaiting_approval", "awaiting_input", "paused", "taken_over")
FINAL = ("done", "failed", "stopped")


class Stopped(Exception):
    """Raised at a checkpoint once the task was stopped."""


class TaskControl:
    """Pause / resume / stop / take over / hand back / approvals for one running task.
    The agent loop calls `await checkpoint()` before every model call and every action."""

    def __init__(self) -> None:
        self.status = "starting"
        self.stopped = False
        self.stop_reason = ""
        self.paused_reason = ""
        self._resume = asyncio.Event()
        self._resume.set()
        self._decision: asyncio.Future | None = None
        self.pending: dict | None = None        # approval or question waiting for the operator
        self.before_pause = "thinking"

    def phase(self, status: str) -> None:
        """The loop's own progress (thinking/acting) never overrides an operator hold."""
        if self.stopped or self.status in ("paused", "taken_over") or self.status in FINAL:
            return
        self.status = status

    # operator controls
    def pause(self, reason: str = "paused by operator") -> bool:
        if self.status in FINAL or self.stopped or self.status in ("paused", "taken_over"):
            return False
        self.before_pause = self.status
        self.status, self.paused_reason = "paused", reason
        self._resume.clear()
        return True

    def takeover(self, reason: str = "operator took control") -> bool:
        if self.status in FINAL or self.stopped:
            return False
        if self.status != "paused":
            self.before_pause = self.status
        self.status, self.paused_reason = "taken_over", reason
        self._resume.clear()
        if self.pending and self._decision and not self._decision.done():
            self._decision.set_result({"decision": "takeover"})
        return True

    def resume(self) -> bool:
        """RESUME (after pause) and HAND BACK (after take over) both land here."""
        if self.status not in ("paused", "taken_over") or self.stopped:
            return False
        self.status, self.paused_reason = "thinking", ""
        self._resume.set()
        return True

    def stop(self, reason: str = "stopped by operator") -> bool:
        if self.status in FINAL or self.stopped:
            return False
        self.stopped, self.stop_reason = True, reason
        self._resume.set()
        if self._decision and not self._decision.done():
            self._decision.set_result({"decision": "stop"})
        return True

    async def checkpoint(self) -> None:
        if self.stopped:
            raise Stopped(self.stop_reason)
        while not self._resume.is_set():
            await self._resume.wait()
        if self.stopped:
            raise Stopped(self.stop_reason)

    async def ask(self, kind: str, payload: dict, timeout: float) -> dict:
        """Wait for the operator: kind 'approval' → {'decision': approve|deny|takeover|stop},
        kind 'question' → {'decision': 'reply', 'text': ...}. Times out as deny."""
        if self.stopped:
            raise Stopped(self.stop_reason)
        loop = asyncio.get_running_loop()
        self._decision = loop.create_future()
        self.pending = {"kind": kind, **payload, "since": time.time(), "timeout": timeout}
        prev = self.status
        self.status = "awaiting_approval" if kind == "approval" else "awaiting_input"
        try:
            res = await asyncio.wait_for(asyncio.shield(self._decision), timeout)
        except asyncio.TimeoutError:
            res = {"decision": "timeout"}
        finally:
            self.pending = None
            self._decision = None
            if self.status in ("awaiting_approval", "awaiting_input"):
                self.status = prev if prev not in ("awaiting_approval", "awaiting_input") else "thinking"
        if self.stopped:
            raise Stopped(self.stop_reason)
        return res

    def decide(self, decision: str, text: str = "") -> bool:
        if not self.pending or not self._decision or self._decision.done():
            return False
        if decision == "takeover":
            return self.takeover("operator took over at the approval prompt")
        self._decision.set_result({"decision": decision, "text": text})
        return True


class RateLimiter:
    """Minimum gap between actions + a per-minute cap."""

    def __init__(self, min_interval: float = 0.5, per_minute: int = 60, clock=time.monotonic) -> None:
        self.min_interval, self.per_minute, self.clock = min_interval, per_minute, clock
        self.stamps: list[float] = []

    def delay(self) -> float:
        now = self.clock()
        self.stamps = [s for s in self.stamps if now - s < 60]
        d = 0.0
        if self.stamps:
            d = max(d, self.min_interval - (now - self.stamps[-1]))
        if len(self.stamps) >= self.per_minute:
            d = max(d, 60 - (now - self.stamps[0]))
        return max(0.0, d)

    async def wait(self) -> None:
        d = self.delay()
        if d > 0:
            await asyncio.sleep(d)
        self.stamps.append(self.clock())
