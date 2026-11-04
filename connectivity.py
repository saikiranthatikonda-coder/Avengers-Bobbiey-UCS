"""WiFi + Bluetooth + ping — cross-platform (Windows / macOS / Linux).

Each probe degrades gracefully: if the platform tool is missing the field
simply reports unavailable rather than crashing, so the dashboard runs on any OS.
"""

import asyncio
import json
import platform
import time


OS = platform.system()   # "Windows" | "Darwin" | "Linux"
_MAC_WIFI_DEV: dict = {"dev": None, "ts": 0.0}     # Wi-Fi BSD name (en0/en1...), cached
_MAC_BT: dict = {"ts": 0.0, "data": None}          # system_profiler is slow -> cache 60 s


async def _run(*args, timeout=6) -> str:
    """Run a fixed tool off the event loop. Raises FileNotFoundError when the tool
    is missing (callers catch it and report the field as unavailable)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except NotImplementedError as e:     # event loop without subprocess support
        raise FileNotFoundError(str(e)) from e
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()                          # never leave a hung netsh/PowerShell behind
        await proc.wait()
        raise
    return stdout.decode("utf-8", errors="ignore")


# ── WiFi ──────────────────────────────────────────────────────────
async def wifi_info() -> dict:
    try:
        if OS == "Windows":
            return _parse_netsh(await _run("netsh", "wlan", "show", "interfaces", timeout=5))
        if OS == "Darwin":
            return await _mac_wifi()
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


def parse_mac_hardware_ports(out: str) -> str | None:
    """Wi-Fi device (en0/en1...) from `networksetup -listallhardwareports`."""
    port = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Hardware Port:"):
            port = line.split(":", 1)[1].strip().lower()
        elif line.startswith("Device:") and port in ("wi-fi", "airport"):
            return line.split(":", 1)[1].strip() or None
    return None


async def _mac_wifi_device() -> str | None:
    if _MAC_WIFI_DEV["dev"] and time.time() - _MAC_WIFI_DEV["ts"] < 600:
        return _MAC_WIFI_DEV["dev"]
    dev = parse_mac_hardware_ports(
        await _run("/usr/sbin/networksetup", "-listallhardwareports", timeout=5))
    _MAC_WIFI_DEV.update(dev=dev, ts=time.time())
    return dev


def _iface_ipv4(name: str) -> str | None:
    """Routable IPv4 on an interface (link-local 169.254.x means no real network)."""
    import socket
    import psutil
    try:
        st = psutil.net_if_stats().get(name)
        if st is not None and not st.isup:
            return None
        for a in psutil.net_if_addrs().get(name, []):
            if a.family == socket.AF_INET and a.address and not a.address.startswith("169.254."):
                return a.address
    except Exception:
        pass
    return None


async def _mac_wifi() -> dict:
    """macOS Wi-Fi. Association is read from the interface itself (it has a routable
    IPv4 address or not). The SSID comes from `networksetup -getairportnetwork`, which
    macOS 14.4+ answers with "not associated" (or redacts) unless the app has Location
    permission, so a missing SSID is reported as unknown, never as disconnected."""
    try:
        dev = await _mac_wifi_device()
    except Exception:
        dev = None
    if not dev:
        return {"connected": False, "reason": "no Wi-Fi interface found", "state": "unknown",
                "ssid": None, "signal": None, "speed_rx_mbps": None, "channel": None}
    power = None
    try:
        out = await _run("/usr/sbin/networksetup", "-getairportpower", dev, timeout=5)
        if ":" in out:
            power = out.rsplit(":", 1)[1].strip().lower()        # "on" | "off"
    except Exception:
        pass
    if power == "off":
        return {"connected": False, "reason": "Wi-Fi is turned off", "state": "off", "interface": dev,
                "ssid": None, "signal": None, "speed_rx_mbps": None, "channel": None}
    ip = _iface_ipv4(dev)
    ssid = None
    try:
        out = (await _run("/usr/sbin/networksetup", "-getairportnetwork", dev, timeout=5)).strip()
        if out.startswith("Current Wi-Fi Network:") or out.startswith("Current AirPort Network:"):
            val = out.split(":", 1)[1].strip()
            if val and "redacted" not in val.lower():
                ssid = val
    except Exception:
        pass
    res = {"connected": bool(ip), "ssid": ssid, "signal": None, "speed_rx_mbps": None,
           "channel": None, "interface": dev,
           "state": "connected" if ip else "disconnected"}
    if ip and not ssid:
        res["ssid_note"] = "network name hidden by macOS (needs Location permission)"
    return res


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
        if OS == "Darwin":
            return await _mac_bluetooth()
        return {"count": None, "devices": [], "available": False,
                "note": f"Bluetooth listing is not available on {OS}"}
    except Exception:
        # tool missing / failed: unknown, not "zero devices"
        return {"count": None, "devices": [], "available": False, "note": "Bluetooth tool not available"}


def parse_mac_bluetooth(raw: str) -> dict:
    """Connected devices from `system_profiler SPBluetoothDataType -json` (macOS 12+:
    "device_connected": [{"<name>": {...}}, ...])."""
    data = json.loads(raw or "{}")
    names: list[str] = []
    for ctl in data.get("SPBluetoothDataType") or []:
        for entry in (ctl.get("device_connected") or []) if isinstance(ctl, dict) else []:
            if isinstance(entry, dict):
                names.extend(str(k) for k in entry.keys())
    return {"count": len(names), "devices": names[:6]}


async def _mac_bluetooth() -> dict:
    if _MAC_BT["data"] is not None and time.time() - _MAC_BT["ts"] < 60:
        return _MAC_BT["data"]
    try:
        d = parse_mac_bluetooth(await _run("/usr/sbin/system_profiler", "SPBluetoothDataType",
                                           "-json", timeout=10))
    except Exception:
        d = {"count": None, "devices": [], "available": False,
             "note": "Bluetooth devices could not be read on this Mac"}
    _MAC_BT.update(ts=time.time(), data=d)
    return d


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
                    is_wifi = any(k in low for k in ("wi-fi", "wifi", "wireless", "wlan")) or \
                        (OS == "Darwin" and name == _MAC_WIFI_DEV["dev"])
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
async def ping_ms() -> int:
    """Link latency as a TCP-handshake round trip (one network RTT). Timing a
    full HTTPS request also bills DNS + TLS + HTTP and read ~1 s here."""
    from netspeed import PROBE_TARGETS, tcp_rtt_ms
    for host, port in PROBE_TARGETS:
        r = await tcp_rtt_ms(host, port)
        if r is not None:
            return int(round(r))
    return -1


async def gather() -> dict:
    wifi, ble, ping = await asyncio.gather(wifi_info(), bluetooth_info(), ping_ms())
    return {"wifi": wifi, "bluetooth": ble, "ping_ms": ping, "os": OS,
            "link": active_link()}
