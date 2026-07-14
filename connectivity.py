"""WiFi + Bluetooth + ping — cross-platform (Windows / macOS / Linux).

Each probe degrades gracefully: if the platform tool is missing the field
simply reports unavailable rather than crashing, so the dashboard runs on any OS.
"""

import asyncio
import platform
import time

import httpx

OS = platform.system()   # "Windows" | "Darwin" | "Linux"


async def _run(*args, timeout=6) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return stdout.decode("utf-8", errors="ignore")


# ── WiFi ──────────────────────────────────────────────────────────
async def wifi_info() -> dict:
    try:
        if OS == "Windows":
            return _parse_netsh(await _run("netsh", "wlan", "show", "interfaces", timeout=5))
        if OS == "Darwin":
            ssid = ""
            try:
                out = await _run("/usr/sbin/networksetup", "-getairportnetwork", "en0", timeout=5)
                if ":" in out:
                    ssid = out.split(":", 1)[1].strip()
            except Exception:
                pass
            ok = bool(ssid) and "not associated" not in ssid.lower()
            return {"connected": ok, "ssid": ssid or None, "signal": None,
                    "speed_rx_mbps": None, "channel": None,
                    "state": "connected" if ok else "disconnected"}
        # Linux
        try:
            out = await _run("nmcli", "-t", "-f", "active,ssid,signal,rate", "dev", "wifi", timeout=5)
            for line in out.splitlines():
                if line.startswith("yes:"):
                    _, ssid, sig, rate = (line.split(":") + ["", "", ""])[:4]
                    return {"connected": True, "ssid": ssid or None,
                            "signal": (sig + "%") if sig else None,
                            "speed_rx_mbps": rate.split()[0] if rate else None,
                            "channel": None, "state": "connected"}
        except Exception:
            pass
        return {"connected": False, "reason": "no wifi tool", "state": "unknown"}
    except FileNotFoundError:
        return {"connected": False, "reason": "wifi tool not available", "state": "unknown"}
    except Exception as e:
        return {"connected": False, "reason": str(e), "state": "unknown"}


def _parse_netsh(out: str) -> dict:
    low = out.lower()
    if "no wireless" in low or "is not enabled" in low or "no wlan" in low:
        return {"connected": False, "reason": "no wifi adapter", "state": "unknown"}
    info: dict[str, str] = {}
    for line in out.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        info[key.strip().lower()] = val.strip()
    state = info.get("state", "").lower()
    ssid = info.get("ssid") or None
    return {
        "connected": state == "connected" and bool(ssid),
        "ssid": ssid, "signal": info.get("signal"),
        "speed_rx_mbps": info.get("receive rate (mbps)"),
        "speed_tx_mbps": info.get("transmit rate (mbps)"),
        "auth": info.get("authentication"), "channel": info.get("channel"),
        "state": state or "unknown",
    }


# ── Bluetooth ─────────────────────────────────────────────────────
async def bluetooth_info() -> dict:
    try:
        if OS == "Windows":
            cmd = ("Get-PnpDevice -Class Bluetooth -Status OK -ErrorAction SilentlyContinue | "
                   "Where-Object { $_.FriendlyName -notmatch 'Adapter|Radio|Enumerator' } | "
                   "Select-Object -ExpandProperty FriendlyName")
            out = await _run("powershell", "-NoProfile", "-Command", cmd, timeout=8)
            names = [s.strip() for s in out.splitlines() if s.strip()]
            return {"count": len(names), "devices": names[:6]}
        if OS == "Linux":
            out = await _run("bluetoothctl", "devices", "Connected", timeout=5)
            names = [l.split(" ", 2)[2] for l in out.splitlines() if l.startswith("Device")]
            return {"count": len(names), "devices": names[:6]}
        # macOS: system_profiler is slow; report adapter-only to stay responsive
        return {"count": 0, "devices": [], "note": "n/a"}
    except Exception:
        return {"count": 0, "devices": []}


# ── Active uplink (the REAL primary connection, wifi or wired) ─────
def _primary_ip() -> str | None:
    """Outbound IP via the default route — identifies the real uplink NIC."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def active_link() -> dict | None:
    """The interface that actually carries traffic to the internet, with its
    link type and speed — so the dashboard shows Ethernet when WiFi is down."""
    import socket
    import psutil
    pip = _primary_ip()
    if not pip:
        return None
    try:
        stats = psutil.net_if_stats()
        for name, alist in psutil.net_if_addrs().items():
            for a in alist:
                if a.family == socket.AF_INET and a.address == pip:
                    st = stats.get(name)
                    low = name.lower()
                    is_wifi = any(k in low for k in ("wi-fi", "wifi", "wireless", "wlan"))
                    return {
                        "name": name, "addr": a.address,
                        "type": "wifi" if is_wifi else "ethernet",
                        "speed_mbps": (st.speed if st else 0) or 0,
                        "up": bool(st.isup) if st else True,
                    }
    except Exception:
        pass
    return None


# ── Ping ──────────────────────────────────────────────────────────
async def ping_ms(url: str = "https://www.google.com/generate_204") -> int:
    t = time.time()
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as c:
            await c.head(url)
        return int((time.time() - t) * 1000)
    except Exception:
        return -1


async def gather() -> dict:
    wifi, ble, ping = await asyncio.gather(wifi_info(), bluetooth_info(), ping_ms())
    return {"wifi": wifi, "bluetooth": ble, "ping_ms": ping, "os": OS,
            "link": active_link()}
