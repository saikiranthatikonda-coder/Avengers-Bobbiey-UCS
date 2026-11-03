"""DEVICES · NODE CONNECTIVITY: what this host can actually use to link BUCS nodes.

Every probe is a real measurement on this machine (binary present, service state,
CLI status output, listening port). Nothing is assumed: a method is "connected"
only when its own tool says so. Subprocesses go through connectivity._run (off the
event loop, killed on timeout); blocking psutil/socket checks run in a thread.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import socket
import time

import connectivity

OS = platform.system()
PORT = os.getenv("JARVIS_PORT", "8765")
BIND = os.getenv("JARVIS_HOST", "127.0.0.1")

_WIN_PATHS = {
    "tailscale":    [r"C:\Program Files\Tailscale\tailscale.exe"],
    "wg":           [r"C:\Program Files\WireGuard\wg.exe"],
    "zerotier-cli": [r"C:\ProgramData\ZeroTier\One\zerotier-one_x64.exe",
                     r"C:\Program Files (x86)\ZeroTier\One\zerotier-cli.bat"],
    "cloudflared":  [r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                     r"C:\Program Files\cloudflared\cloudflared.exe"],
}


def find_bin(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    for c in _WIN_PATHS.get(name, []) if OS == "Windows" else []:
        if os.path.exists(c):
            return c
    return None


async def _run(*args, timeout=6) -> str:
    try:
        return await connectivity._run(*args, timeout=timeout)
    except (FileNotFoundError, PermissionError, asyncio.TimeoutError, OSError):
        return ""


def _svc(name: str) -> str | None:
    """Windows service status ('running'/'stopped'/…) or None when not installed."""
    if OS != "Windows":
        return None
    try:
        import psutil
        return psutil.win_service_get(name).status()
    except Exception:
        return None


def _svc_prefix(prefix: str) -> list[tuple[str, str]]:
    if OS != "Windows":
        return []
    try:
        import psutil
        return [(s.name(), s.status()) for s in psutil.win_service_iter() if s.name().startswith(prefix)]
    except Exception:
        return []


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.6):
            return True
    except OSError:
        return False


def _entry(key, name, status, detail, steps, url=None, extra=None) -> dict:
    return {"key": key, "name": name, "status": status, "detail": detail,
            "steps": steps, "url": url, **(extra or {})}


# ── parsers (pure, unit-tested) ───────────────────────────────────
def parse_tailscale(out: str) -> dict:
    """`tailscale status --json` → state, own IPs/name, peer counts."""
    try:
        d = json.loads(out)
    except Exception:
        return {"ok": False}
    self_ = d.get("Self") or {}
    peers = list((d.get("Peer") or {}).values())
    dns = (self_.get("DNSName") or "").rstrip(".")
    return {"ok": True, "state": d.get("BackendState", "Unknown"),
            "ips": [ip for ip in (self_.get("TailscaleIPs") or d.get("TailscaleIPs") or []) if ":" not in ip],
            "host": self_.get("HostName", ""), "dns": dns,
            "tailnet": (d.get("CurrentTailnet") or {}).get("Name", ""),
            "peers": len(peers), "peers_online": sum(1 for p in peers if p.get("Online"))}


def parse_zerotier(out: str) -> dict:
    """`zerotier-cli info` → '200 info <address> <version> ONLINE|OFFLINE|TUNNELED'."""
    parts = out.strip().split()
    if len(parts) >= 5 and parts[0] == "200" and parts[1] == "info":
        return {"ok": True, "address": parts[2], "version": parts[3], "state": parts[4]}
    return {"ok": False}


def parse_wg_interfaces(out: str) -> list[str]:
    return [w for w in out.split() if w]


def parse_mbn(out: str) -> dict:
    """`netsh mbn show interfaces`."""
    low = out.lower()
    if not out.strip() or "is not running" in low or "no mobile broadband" in low or "there is no" in low:
        return {"present": False}
    info = {}
    for line in out.splitlines():
        k, sep, v = line.partition(":")
        if sep:
            info[k.strip().lower()] = v.strip()
    return {"present": True, "state": info.get("state", ""), "provider": info.get("provider name", "")}


def parse_bt_pnp(out: str) -> dict:
    """Lines 'Status|InstanceId|FriendlyName' from Get-PnpDevice -Class Bluetooth. The radio
    itself is a USB/PCI/SDIO device; paired peripherals enumerate under BTHENUM/BTHLE."""
    adapters, peers = [], 0
    for line in out.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) != 3:
            continue
        st, inst, name = parts
        if inst.upper().startswith(("USB\\", "PCI\\", "SD\\", "ACPI\\", "UART\\")):
            adapters.append({"name": name, "ok": st.upper() == "OK"})
        elif inst.upper().startswith(("BTHENUM\\", "BTHLE\\", "BTHLEDEVICE\\")):
            peers += 1
    return {"adapters": adapters, "peer_entries": peers}


# ── probes ────────────────────────────────────────────────────────
def _lan_sync() -> dict:
    import psutil
    stats = psutil.net_if_stats()
    ifs = []
    for name, addrs in psutil.net_if_addrs().items():
        st = stats.get(name)
        if st is None or not st.isup:
            continue
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                ifs.append({"name": name, "ip": a.address, "speed_mbps": st.speed or 0})
    return {"interfaces": ifs}


async def probe_lan() -> dict:
    d = await asyncio.to_thread(_lan_sync)
    ifs = [i for i in d["interfaces"] if not i["ip"].startswith("100.")]
    exposed = BIND not in ("127.0.0.1", "localhost", "::1")
    ip = ifs[0]["ip"] if ifs else None
    url = f"http://{ip}:{PORT}" if ip else None
    if not ifs:
        status, detail = "down", "no active IPv4 interface"
    else:
        status = "connected" if exposed else "available"
        detail = ", ".join(f"{i['name']} {i['ip']}" for i in ifs[:4]) + \
                 ("" if exposed else f" · BUCS bound to {BIND} (this PC only)")
    steps = ([] if exposed else ["Set JARVIS_HOST=0.0.0.0 in .env and restart BUCS so other machines can reach it.",
                                 f"Allow TCP {PORT} once in Windows Firewall (elevated shell): "
                                 f"New-NetFirewallRule -DisplayName \"Bobbiey UCS\" -Direction Inbound -Action Allow -Protocol TCP -LocalPort {PORT}"]) + \
            ([f"On the other machine (same network): join-fleet.cmd {url}"] if url else []) + \
            ["Office Wi-Fi often isolates clients; a phone hotspot or a mesh VPN (Tailscale) avoids that."]
    return _entry("lan", "LAN", status, detail, steps, url if exposed else None, {"interfaces": ifs[:8]})


async def probe_tailscale() -> dict:
    b = find_bin("tailscale")
    steps = ["Install Tailscale on this host and on every node (tailscale.com/download), sign in to the same tailnet.",
             "Set JARVIS_HOST=0.0.0.0 here (Tailscale traffic arrives on the 100.x interface).",
             f"On a remote node: join-fleet.cmd http://<this-host-100.x-IP>:{PORT}"]
    if not b:
        return _entry("tailscale", "Tailscale", "not_installed", "tailscale CLI not found", steps)
    d = parse_tailscale(await _run(b, "status", "--json", timeout=6))
    if not d.get("ok"):
        return _entry("tailscale", "Tailscale", "available", "installed · daemon not answering (run: tailscale up)", steps)
    if d["state"] != "Running":
        return _entry("tailscale", "Tailscale", "available", f"installed · {d['state']} (run: tailscale up)", steps)
    ip = d["ips"][0] if d["ips"] else None
    url = f"http://{d['dns'] or ip}:{PORT}" if (d["dns"] or ip) else None
    steps = [f"Remote nodes in the tailnet join with: join-fleet.cmd {url}",
             "Requires JARVIS_HOST=0.0.0.0 on this host (BUCS currently binds " + BIND + ")."]
    return _entry("tailscale", "Tailscale", "connected",
                  f"{ip or '—'} · {d['dns'] or d['host']} · peers {d['peers_online']}/{d['peers']} online",
                  steps, url)


async def probe_wireguard() -> dict:
    b = find_bin("wg")
    svcs = await asyncio.to_thread(_svc_prefix, "WireGuardTunnel$")
    running = [n.split("$", 1)[1] for n, st in svcs if st == "running"]
    steps = ["Install WireGuard (wireguard.com/install) on this host and each node; give each a tunnel with a peer for the host.",
             "Activate the tunnel, then from a node: join-fleet.cmd http://<this-host-tunnel-IP>:" + PORT]
    if running:
        return _entry("wireguard", "WireGuard", "connected", "tunnel(s) up: " + ", ".join(running[:4]), steps)
    if b or svcs or await asyncio.to_thread(_svc, "WireGuardManager"):
        ifs = parse_wg_interfaces(await _run(b, "show", "interfaces", timeout=4)) if b else []
        if ifs:
            return _entry("wireguard", "WireGuard", "connected", "interfaces: " + ", ".join(ifs[:4]), steps)
        return _entry("wireguard", "WireGuard", "available", "installed · no active tunnel", steps)
    return _entry("wireguard", "WireGuard", "not_installed", "wg / WireGuard service not found", steps)


async def probe_zerotier() -> dict:
    b = find_bin("zerotier-cli")
    steps = ["Install ZeroTier (zerotier.com/download) on this host and the nodes; join the same network ID and authorise members at my.zerotier.com.",
             "From a node: join-fleet.cmd http://<this-host-ZeroTier-IP>:" + PORT]
    if not b:
        st = await asyncio.to_thread(_svc, "ZeroTierOneService")
        if st:
            return _entry("zerotier", "ZeroTier", "available", f"service {st} · CLI not found", steps)
        return _entry("zerotier", "ZeroTier", "not_installed", "zerotier-cli not found", steps)
    args = (b, "-q", "info") if b.lower().endswith("zerotier-one_x64.exe") else (b, "info")
    d = parse_zerotier(await _run(*args, timeout=5))
    if d.get("ok") and d["state"] in ("ONLINE", "TUNNELED"):
        return _entry("zerotier", "ZeroTier", "connected", f"node {d['address']} · {d['state']} · v{d['version']}", steps)
    return _entry("zerotier", "ZeroTier", "available",
                  "installed · " + (d.get("state") or "service not answering (may need an elevated shell)"), steps)


async def probe_cloudflared() -> dict:
    b = find_bin("cloudflared")
    svc = await asyncio.to_thread(_svc, "Cloudflared")
    steps = ["Create a tunnel in the Cloudflare Zero Trust dashboard and install it as a service: cloudflared service install <token>.",
             f"Route a hostname to http://localhost:{PORT} and protect it with Cloudflare Access (BUCS remote login still applies).",
             "Nodes then join with: join-fleet.cmd https://<your-hostname>"]
    if svc == "running":
        return _entry("cloudflared", "Cloudflare Tunnel", "connected", "cloudflared service running", steps)
    if b or svc:
        ver = (await _run(b, "--version", timeout=5)).strip().splitlines()[:1] if b else []
        det = "installed" + (f" · {ver[0][:60]}" if ver else "") + (f" · service {svc}" if svc else " · no tunnel service")
        return _entry("cloudflared", "Cloudflare Tunnel", "available", det, steps)
    return _entry("cloudflared", "Cloudflare Tunnel", "not_installed", "cloudflared not found", steps)


async def probe_ssh() -> dict:
    svc = await asyncio.to_thread(_svc, "sshd")
    listening = await asyncio.to_thread(_port_open, 22)
    steps = (["Windows: Settings → System → Optional features → add OpenSSH Server, then (elevated): "
              "Start-Service sshd; Set-Service sshd -StartupType Automatic"] if OS == "Windows" else
             ["Install and enable openssh-server (sudo systemctl enable --now ssh)."]) + \
            [f"From a node, tunnel BUCS without exposing it: ssh -N -L {PORT}:127.0.0.1:{PORT} <user>@<this-host>, "
             f"then join-fleet.cmd http://127.0.0.1:{PORT}"]
    if listening:
        return _entry("ssh", "SSH server", "connected", "port 22 listening" + (f" · sshd {svc}" if svc else ""), steps)
    if svc:
        return _entry("ssh", "SSH server", "available", f"sshd installed · {svc}", steps)
    client = bool(shutil.which("ssh"))
    return _entry("ssh", "SSH server", "not_installed",
                  "no SSH server" + (" · ssh client present (can reach other hosts)" if client else ""), steps)


async def probe_bluetooth() -> dict:
    steps = ["Bluetooth is for nearby peripherals and phones, not for linking BUCS nodes (use LAN / Tailscale)."]
    if OS == "Windows":
        cmd = ("Get-PnpDevice -Class Bluetooth -PresentOnly -ErrorAction SilentlyContinue | "
               "ForEach-Object { '{0}|{1}|{2}' -f $_.Status, $_.InstanceId, $_.FriendlyName }")
        d = parse_bt_pnp(await _run("powershell", "-NoProfile", "-Command", cmd, timeout=8))
        if not d["adapters"]:
            return _entry("bluetooth", "Bluetooth", "not_installed", "no Bluetooth adapter present", steps)
        ok = [a for a in d["adapters"] if a["ok"]]
        bt = await connectivity.bluetooth_info()
        det = (ok or d["adapters"])[0]["name"] + f" · {bt.get('count', 0)} connected device(s)"
        return _entry("bluetooth", "Bluetooth", "connected" if ok else "available", det, steps)
    bt = await connectivity.bluetooth_info()
    return _entry("bluetooth", "Bluetooth", "available" if bt.get("count") else "unknown",
                  f"{bt.get('count', 0)} connected device(s)", steps)


async def probe_wifi() -> dict:
    w = await connectivity.wifi_info()
    steps = ["Nodes on the same Wi-Fi can join directly over LAN unless the access point isolates clients."]
    if w.get("connected"):
        det = f"{w.get('ssid')} · signal {w.get('signal') or '—'} · {w.get('auth') or ''}".rstrip(" ·")
        return _entry("wifi", "Wi-Fi", "connected", det, steps)
    if w.get("reason") == "no wifi adapter":
        return _entry("wifi", "Wi-Fi", "not_installed", "no Wi-Fi adapter", steps)
    return _entry("wifi", "Wi-Fi", "available" if w.get("state") not in ("unknown", None) else "unknown",
                  w.get("reason") or w.get("state") or "not connected", steps)


async def probe_cellular() -> dict:
    steps = ["A phone hotspot is the reliable fallback when office Wi-Fi isolates clients: connect host and nodes to it, then use the LAN URL.",
             "Windows Mobile Hotspot on this PC also works: nodes join http://192.168.137.1:" + PORT]
    hotspot = False
    try:
        d = await asyncio.to_thread(_lan_sync)
        hotspot = any(i["ip"] == "192.168.137.1" for i in d["interfaces"])
    except Exception:
        pass
    mbn = parse_mbn(await _run("netsh", "mbn", "show", "interfaces", timeout=5)) if OS == "Windows" else {"present": False}
    if hotspot:
        return _entry("cellular", "Cellular / Hotspot", "connected", "Windows Mobile Hotspot is sharing (192.168.137.1)", steps,
                      f"http://192.168.137.1:{PORT}")
    if mbn.get("present"):
        st = "connected" if mbn.get("state", "").lower() == "connected" else "available"
        return _entry("cellular", "Cellular / Hotspot", st,
                      f"mobile broadband {mbn.get('state') or '—'} {mbn.get('provider') or ''}".strip(), steps)
    return _entry("cellular", "Cellular / Hotspot", "not_installed", "no mobile-broadband modem · hotspot not active", steps)


PROBES = (probe_lan, probe_wifi, probe_tailscale, probe_wireguard, probe_zerotier,
          probe_cloudflared, probe_ssh, probe_bluetooth, probe_cellular)


async def gather(fleet_state=None) -> dict:
    global PORT, BIND                       # read at call time: .env may load after import
    PORT, BIND = os.getenv("JARVIS_PORT", "8765"), os.getenv("JARVIS_HOST", "127.0.0.1")
    t0 = time.time()
    res = await asyncio.gather(*(p() for p in PROBES), return_exceptions=True)
    methods = []
    for p, r in zip(PROBES, res):
        if isinstance(r, Exception):
            k = p.__name__.replace("probe_", "")
            methods.append(_entry(k, k.upper(), "unknown", f"probe failed: {type(r).__name__}", []))
        else:
            methods.append(r)
    out = {"methods": methods, "at": time.time(), "took_s": round(time.time() - t0, 1),
           "bind": BIND, "port": PORT, "os": OS}
    if fleet_state is not None:
        try:
            agg = fleet_state.snapshot().get("aggregate", {})
            out["fleet"] = {"nodes": agg.get("nodes", 0), "online": agg.get("online", 0)}
        except Exception:
            pass
    return out
