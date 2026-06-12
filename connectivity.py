"""WiFi + Bluetooth + ping. Windows-only via netsh + PowerShell."""

import asyncio
import time

import httpx


async def wifi_info() -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            "netsh", "wlan", "show", "interfaces",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        out = stdout.decode("utf-8", errors="ignore")
    except FileNotFoundError:
        return {"connected": False, "reason": "netsh not available"}
    except Exception as e:
        return {"connected": False, "reason": str(e)}

    low = out.lower()
    if "no wireless" in low or "is not enabled" in low or "no wlan" in low:
        return {"connected": False, "reason": "no wifi adapter"}

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
        "ssid": ssid,
        "signal": info.get("signal"),
        "speed_rx_mbps": info.get("receive rate (mbps)"),
        "speed_tx_mbps": info.get("transmit rate (mbps)"),
        "auth": info.get("authentication"),
        "channel": info.get("channel"),
        "state": state or "unknown",
    }


async def bluetooth_info() -> dict:
    cmd = (
        "Get-PnpDevice -Class Bluetooth -Status OK -ErrorAction SilentlyContinue | "
        "Where-Object { $_.FriendlyName -notmatch 'Adapter|Radio|Enumerator' } | "
        "Select-Object -ExpandProperty FriendlyName"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-Command", cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
        names = [s.strip() for s in stdout.decode("utf-8", errors="ignore").splitlines() if s.strip()]
        return {"count": len(names), "devices": names[:6]}
    except Exception as e:
        return {"count": 0, "devices": [], "error": str(e)}


async def ping_ms(url: str = "https://www.google.com/generate_204") -> int:
    t = time.time()
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.head(url)
        return int((time.time() - t) * 1000)
    except Exception:
        return -1


async def gather() -> dict:
    wifi_t = asyncio.create_task(wifi_info())
    ble_t = asyncio.create_task(bluetooth_info())
    ping_t = asyncio.create_task(ping_ms())
    wifi, ble, ping = await asyncio.gather(wifi_t, ble_t, ping_t)
    return {"wifi": wifi, "bluetooth": ble, "ping_ms": ping}
