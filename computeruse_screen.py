"""COMPUTER USE · screen capture, input injection, OS permissions, emergency-stop watchdog.

Windows: the process is made per-monitor DPI aware BEFORE anything captures or moves the
mouse, so mss monitor rects, captured bitmaps, GetCursorPos and SetCursorPos are all in the
same physical-pixel space (correct at 125 % / 150 % scaling and across monitors, including
monitors left of / above the primary with negative coordinates). Mouse input goes through
SetCursorPos + mouse_event (never MOUSEEVENTF_ABSOLUTE, which only spans the primary screen);
text through SendInput KEYEVENTF_UNICODE (any language); key combos through pyautogui.

macOS: pyautogui + mss work once the app running BUCS (Terminal / iTerm / Python) has
Accessibility and Screen Recording permission. mss captures Retina bitmaps at 2× while
input uses points, so coordinates are always mapped against the monitor rect (points).

Everything here is blocking: callers run it with asyncio.to_thread.
"""

from __future__ import annotations

import ctypes
import io
import os
import platform
import sys
import threading
import time

from computeruse_core import Frame, avoid_corners, fit_size, in_corner

SYSTEM = platform.system()          # "Windows" | "Darwin" | "Linux"
IS_WIN, IS_MAC = SYSTEM == "Windows", SYSTEM == "Darwin"
_dpi_done = False
_lock = threading.RLock()


def set_dpi_awareness() -> str:
    """Per-monitor-v2 DPI awareness (Windows 10 1703+), falling back to older APIs."""
    global _dpi_done
    if not IS_WIN or _dpi_done:
        return "n/a" if not IS_WIN else "set"
    _dpi_done = True
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "unaware"


def dpi_mode() -> str:
    if not IS_WIN:
        return "n/a"
    try:
        ctx = ctypes.windll.user32.GetThreadDpiAwarenessContext()
        aw = ctypes.windll.user32.GetAwarenessFromDpiAwarenessContext(ctypes.c_void_p(ctx))
        return {0: "unaware", 1: "system", 2: "per-monitor"}.get(aw, str(aw))
    except Exception:
        return "unknown"


def _mss():
    set_dpi_awareness()
    import mss
    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()


def _pyautogui():
    set_dpi_awareness()                      # must precede pyautogui's own SetProcessDPIAware
    import pyautogui
    pyautogui.FAILSAFE = True                # mouse slammed into a corner → FailSafeException
    pyautogui.PAUSE = 0.03
    return pyautogui


# ── availability / permissions ────────────────────────────────────────────

def libs() -> dict:
    out = {}
    for mod in ("mss", "PIL", "pyautogui"):
        try:
            __import__(mod)
            out[mod] = True
        except Exception:
            out[mod] = False
    return out


def _mac_flag(framework: str, fn: str) -> bool | None:
    try:
        lib = ctypes.cdll.LoadLibrary(f"/System/Library/Frameworks/{framework}.framework/{framework}")
        f = getattr(lib, fn)
        f.restype = ctypes.c_bool
        return bool(f())
    except Exception:
        return None


def permissions() -> dict:
    """Can this process capture the screen and inject input? Honest, per platform."""
    have = libs()
    problems, howto = [], []
    missing = [m for m, ok in have.items() if not ok]
    if missing:
        pkgs = {"mss": "mss", "PIL": "pillow", "pyautogui": "pyautogui"}
        problems.append("missing Python packages: " + ", ".join(pkgs[m] for m in missing))
        howto.append("pip install " + " ".join(pkgs[m] for m in missing))
    screen = inp = not missing
    if IS_MAC:
        rec = _mac_flag("CoreGraphics", "CGPreflightScreenCaptureAccess")
        acc = _mac_flag("ApplicationServices", "AXIsProcessTrusted")
        if rec is False:
            screen = False
            problems.append("Screen Recording permission not granted")
            howto.append("System Settings → Privacy & Security → Screen Recording → enable the app running BUCS "
                         "(Terminal/iTerm/Python), then restart BUCS")
        if acc is False:
            inp = False
            problems.append("Accessibility permission not granted (mouse/keyboard control)")
            howto.append("System Settings → Privacy & Security → Accessibility → enable the app running BUCS, then restart BUCS")
    elif SYSTEM == "Linux":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            screen = inp = False
            problems.append("no graphical display (DISPLAY not set)")
            howto.append("run BUCS inside a desktop session (X11; Wayland blocks synthetic input)")
        elif os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
            inp = False
            problems.append("Wayland blocks synthetic input")
            howto.append("log in with an X11 / Xorg session")
    return {"platform": SYSTEM, "screen": screen, "input": inp, "ok": screen and inp,
            "problems": problems, "howto": howto, "libs": have,
            "dpi": dpi_mode() if IS_WIN and _dpi_done else ("n/a" if not IS_WIN else "not yet set")}


# ── monitors & capture ────────────────────────────────────────────────────

def monitors() -> list[dict]:
    with _lock, _mss() as s:
        mons = s.monitors[1:]
    out = []
    for i, m in enumerate(mons, start=1):
        out.append({"index": i, "left": m["left"], "top": m["top"], "width": m["width"], "height": m["height"],
                    "primary": bool(m.get("is_primary")) or (m["left"] == 0 and m["top"] == 0),
                    "name": str(m.get("name") or f"Display {i}")[:60]})
    if out and not any(m["primary"] for m in out):
        out[0]["primary"] = True
    return out


def primary_rect() -> tuple[int, int, int, int]:
    for m in monitors():
        if m["primary"]:
            return m["left"], m["top"], m["width"], m["height"]
    return 0, 0, 1920, 1080


def capture(monitor: int = 1, long_edge: int = 1280, quality: int = 70, step: int = 0) -> tuple[Frame, object]:
    """Grab one monitor → (Frame with downscaled JPEG, full-res PIL image)."""
    from PIL import Image
    with _lock, _mss() as s:
        mons = s.monitors
        idx = monitor if 1 <= monitor < len(mons) else 1
        m = mons[idx]
        shot = s.grab(m)
    full = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    iw, ih = fit_size(m["width"], m["height"], long_edge)
    img = full.resize((iw, ih), Image.LANCZOS) if (iw, ih) != full.size else full
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    thumb = img.convert("L").resize((48, 30), Image.BILINEAR).tobytes()
    fr = Frame(mon_left=m["left"], mon_top=m["top"], mon_w=m["width"], mon_h=m["height"], img_w=iw, img_h=ih,
               cap_w=shot.size[0], cap_h=shot.size[1], jpeg=buf.getvalue(), step=step, thumb=thumb)
    return fr, full


def window_titles(rect: tuple[int, int, int, int] | None = None) -> list[str]:
    """Titles of visible, non-minimised top-level windows overlapping `rect` (Windows only;
    elsewhere []). Used by the privacy guard before a frame is stored or sent."""
    if not IS_WIN:
        return []
    from ctypes import wintypes as wt
    u = ctypes.windll.user32
    out: list[str] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        try:
            if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            if rect:
                r = wt.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                l, t, w, h = rect
                if r.right <= l or r.left >= l + w or r.bottom <= t or r.top >= t + h:
                    return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            out.append(buf.value)
        except Exception:
            pass
        return True
    u.EnumWindows(cb, 0)
    return out


def change_ratio(a: bytes, b: bytes) -> float:
    """Mean absolute difference of two 48×30 thumbnails, 0..1 (0 = identical)."""
    if not a or not b or len(a) != len(b):
        return 1.0
    return sum(abs(x - y) for x, y in zip(a, b)) / (255.0 * len(a))


def zoom_image(full, frame: Frame, region: list[int], quality: int = 80) -> bytes:
    """Crop `region` (frame coords) from the full-res capture, sized to fit the frame box."""
    from PIL import Image
    sx = full.size[0] / frame.img_w
    sy = full.size[1] / frame.img_h
    x0, y0, x1, y1 = region
    crop = full.crop((int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)))
    tw, th = fit_size(crop.size[0], crop.size[1], max(frame.img_w, frame.img_h))
    if crop.size[0] < tw:
        crop = crop.resize((tw, th), Image.LANCZOS)
    elif (tw, th) != crop.size:
        crop = crop.resize((tw, th), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# ── input ─────────────────────────────────────────────────────────────────

if IS_WIN:
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _ME = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}


class Input:
    """Synthetic mouse/keyboard. Every pointer target passes avoid_corners so the fail-safe
    corner always means a human. Raises pyautogui.FailSafeException on a corner stop."""

    def __init__(self) -> None:
        self.pg = _pyautogui()
        self.primary = primary_rect()
        self.last_set: tuple[int, int] | None = None

    def _key(self, k: str) -> str:
        if k == "win":
            return "command" if IS_MAC else ("win" if IS_WIN else "winleft")
        if k == "option":
            return "alt"
        return k

    def cursor(self) -> tuple[int, int]:
        if IS_WIN:
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return pt.x, pt.y
        p = self.pg.position()
        return int(p[0]), int(p[1])

    def _failsafe(self) -> None:
        x, y = self.cursor()
        if in_corner(x, y, self.primary):
            raise self.pg.FailSafeException("mouse in a screen corner — emergency stop")

    def move(self, x: int, y: int) -> None:
        self._failsafe()
        x, y = avoid_corners(x, y, self.primary)
        if IS_WIN:
            ctypes.windll.user32.SetCursorPos(int(x), int(y))
        else:
            self.pg.moveTo(x, y, duration=0.12)
        self.last_set = (x, y)

    def _hold(self, mods: list[str]):
        for m in mods:
            self.pg.keyDown(self._key(m))

    def _release(self, mods: list[str]):
        for m in reversed(mods):
            try:
                self.pg.keyUp(self._key(m))
            except Exception:
                pass

    def button(self, which: str, down: bool) -> None:
        self._failsafe()
        if IS_WIN:
            ctypes.windll.user32.mouse_event(_ME[which][0 if down else 1], 0, 0, 0, 0)
        else:
            (self.pg.mouseDown if down else self.pg.mouseUp)(button=which)

    def click(self, x: int, y: int, button: str = "left", count: int = 1, mods: list[str] | None = None) -> None:
        mods = mods or []
        self.move(x, y)
        time.sleep(0.05)
        self._hold(mods)
        try:
            for i in range(count):
                self.button(button, True)
                time.sleep(0.02)
                self.button(button, False)
                if i < count - 1:
                    time.sleep(0.06)
        finally:
            self._release(mods)

    def drag(self, x: int, y: int, x2: int, y2: int) -> None:
        self.move(x, y)
        time.sleep(0.05)
        self.button("left", True)
        try:
            steps = 12
            for i in range(1, steps + 1):
                self.move(int(x + (x2 - x) * i / steps), int(y + (y2 - y) * i / steps))
                time.sleep(0.02)
        finally:
            self.button("left", False)

    def scroll(self, direction: str, amount: int, x: int | None = None, y: int | None = None) -> None:
        if x is not None:
            self.move(x, y)
        self._failsafe()
        if IS_WIN:
            vertical = direction in ("up", "down")
            sign = 1 if direction in ("up", "right") else -1
            for _ in range(amount):
                ctypes.windll.user32.mouse_event(0x0800 if vertical else 0x01000, 0, 0,
                                                 ctypes.c_uint32(sign * 120 & 0xFFFFFFFF).value, 0)
                time.sleep(0.02)
        elif direction in ("up", "down"):
            self.pg.scroll(amount * (1 if direction == "up" else -1))
        else:
            self.pg.hscroll(amount * (1 if direction == "right" else -1))

    def type_text(self, text: str) -> None:
        self._failsafe()
        if not IS_WIN:
            self.pg.write(text, interval=0.01)
            return
        send = ctypes.windll.user32.SendInput
        for ch in text:
            if ch == "\n":
                self.pg.press("enter")
                continue
            if ch == "\t":
                self.pg.press("tab")
                continue
            units = ch.encode("utf-16-le")
            for i in range(0, len(units), 2):
                code = int.from_bytes(units[i:i + 2], "little")
                arr = (_INPUT * 2)()
                for j, flags in enumerate((0x0004, 0x0004 | 0x0002)):      # UNICODE, UNICODE|KEYUP
                    arr[j].type = 1
                    arr[j].u.ki = _KEYBDINPUT(0, code, flags, 0, 0)
                send(2, arr, ctypes.sizeof(_INPUT))
            time.sleep(0.008)

    def key(self, keys: list[str], repeat: int = 1) -> None:
        self._failsafe()
        ks = [self._key(k) for k in keys]
        for _ in range(repeat):
            if len(ks) == 1:
                self.pg.press(ks[0])
            else:
                self.pg.hotkey(*ks, interval=0.03)
            time.sleep(0.03)

    def hold_key(self, keys: list[str], seconds: float) -> None:
        self._failsafe()
        ks = [self._key(k) for k in keys]
        for k in ks:
            self.pg.keyDown(k)
        try:
            time.sleep(seconds)
        finally:
            for k in reversed(ks):
                self.pg.keyUp(k)

    def release_all(self) -> None:
        """Safety net after a stop: never leave a button or modifier held down."""
        for k in ("ctrl", "shift", "alt", "win" if IS_WIN else "command"):
            try:
                self.pg.keyUp(self._key(k) if k != "command" else "command")
            except Exception:
                pass
        for b in ("left", "right", "middle"):
            try:
                if IS_WIN:
                    if ctypes.windll.user32.GetAsyncKeyState({"left": 0x01, "right": 0x02, "middle": 0x04}[b]) & 0x8000:
                        ctypes.windll.user32.mouse_event(_ME[b][1], 0, 0, 0, 0)
                else:
                    self.pg.mouseUp(button=b)
            except Exception:
                pass


# ── watchdog: corner fail-safe, emergency hotkey, human-input detection ───

def _hotkey_down() -> bool:
    """Ctrl+Alt+Backspace held (Windows; physical keys — the agent can never send this combo)."""
    if not IS_WIN:
        return False
    g = ctypes.windll.user32.GetAsyncKeyState
    return all(g(vk) & 0x8000 for vk in (0x11, 0x12, 0x08))


class Watchdog(threading.Thread):
    """Polls every 80 ms while a task runs. on_emergency(reason) for the corner / hotkey;
    on_human(reason) when the pointer moves away from where the agent left it (the human
    grabbed the mouse) — the engine then pauses in TAKE OVER mode."""

    def __init__(self, inp: Input, on_emergency, on_human, detect_human: bool = True) -> None:
        super().__init__(daemon=True, name="computeruse-watchdog")
        self.inp, self.on_emergency, self.on_human = inp, on_emergency, on_human
        self.detect_human = detect_human
        self.halt = threading.Event()
        self.agent_busy = threading.Event()     # set while the agent itself is moving the pointer
        self.suspended = threading.Event()      # set while paused / taken over (human owns the mouse)
        self.baseline: tuple[int, int] | None = None

    def rebase(self) -> None:
        try:
            self.baseline = self.inp.cursor()
        except Exception:
            self.baseline = None

    def run(self) -> None:
        self.rebase()
        while not self.halt.wait(0.08):
            try:
                x, y = self.inp.cursor()
            except Exception:
                continue
            if _hotkey_down():
                self.on_emergency("emergency hotkey Ctrl+Alt+Backspace")
                return
            if in_corner(x, y, self.inp.primary):
                self.on_emergency("mouse moved into a screen corner (fail-safe)")
                return
            if self.agent_busy.is_set() or self.suspended.is_set():
                self.baseline = (x, y)
                continue
            if self.detect_human and self.baseline and (abs(x - self.baseline[0]) + abs(y - self.baseline[1])) > 40:
                self.baseline = (x, y)
                self.on_human("mouse moved by a human — agent paused (TAKE OVER)")
