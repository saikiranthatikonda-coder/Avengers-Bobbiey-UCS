"""Node telemetry probe — Roadmap Phase 4 (multi-site fleet).

A single, dependency-light collector (psutil + stdlib only) used by BOTH:
  · node_agent.py  — runs on every machine you own, reports to the command server
  · main.py        — registers the command host itself as a fleet node

Deliberately no FastAPI / httpx import so the agent stays tiny: another laptop
only needs `pip install psutil` to join the fleet.
"""

import platform
import socket
import subprocess
import time
import uuid
from pathlib import Path

import psutil

ID_FILE = Path(__file__).parent / "node_id.txt"


def _stable_node_id() -> str:
    """Persisted per-machine id so a node keeps its identity across restarts."""
    try:
        return ID_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        nid = f"{socket.gethostname()[:20]}-{uuid.uuid4().hex[:6]}"
        try:
            ID_FILE.write_text(nid, encoding="utf-8")
        except Exception:
            pass
        return nid


def _platform_label() -> str:
    s = platform.system()
    if s == "Windows":
        return f"Windows {platform.release()}"
    if s == "Darwin":
        return f"macOS {platform.mac_ver()[0] or ''}".strip()
    return f"{s} {platform.release()}"


def _detect_ollama() -> dict:
    """Does this node run Ollama? If so, advertise its models so the command
    host can route inference here (on-prem AI cluster)."""
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
            data = _json.loads(r.read())
        models = [m.get("name") for m in data.get("models", []) if m.get("name")]
        return {"available": True, "port": 11434, "models": models[:12],
                "count": len(models)}
    except Exception:
        return {"available": False, "count": 0, "models": []}


def _read_gpu():
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip().splitlines()[0])
    except Exception:
        pass
    return None


def _scan_peripherals():
    """Windows PnP peripherals; [] elsewhere or on failure."""
    if platform.system() != "Windows":
        return []
    ps = (
        "Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Class -in @('Camera','Image','AudioEndpoint',"
        "'Bluetooth','WPD','DiskDrive','Monitor') } | "
        "Select-Object FriendlyName,Class,Status | ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=9,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not r.stdout.strip():
            return []
        import json as _json
        data = _json.loads(r.stdout)
        if isinstance(data, dict):
            data = [data]
        seen, out = set(), []
        order = {"Camera": 0, "Bluetooth": 1, "WPD": 2, "AudioEndpoint": 3,
                 "DiskDrive": 4, "Monitor": 5, "Image": 6}
        for d in data:
            name = (d.get("FriendlyName") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append({"name": name[:44], "cls": (d.get("Class") or "").strip(),
                        "ok": (d.get("Status") or "").upper() == "OK"})
        out.sort(key=lambda x: order.get(x["cls"], 9))
        return out[:16]
    except Exception:
        return []


class NodeProbe:
    """Stateful sampler — call sample() on a fixed cadence."""

    def __init__(self, name: str | None = None) -> None:
        self.node_id = _stable_node_id()
        self.name = name or socket.gethostname()[:28]
        self.platform = _platform_label()
        self._prev_net = None
        self._prev_ts = 0.0
        self._periph = []
        self._periph_ts = 0.0
        self._ollama = {"available": False, "count": 0, "models": []}
        self._ollama_ts = 0.0
        psutil.cpu_percent(interval=None)  # prime the first reading

    def _net_rate(self):
        now = time.time()
        cur = psutil.net_io_counters()
        up = down = 0.0
        if self._prev_net is not None:
            dt = max(0.5, now - self._prev_ts)
            up = (cur.bytes_sent - self._prev_net.bytes_sent) / dt / 1024
            down = (cur.bytes_recv - self._prev_net.bytes_recv) / dt / 1024
        self._prev_net, self._prev_ts = cur, now
        return max(0.0, round(up, 1)), max(0.0, round(down, 1))

    def _power(self):
        try:
            b = psutil.sensors_battery()
            if b is not None:
                return {"present": True, "percent": round(b.percent),
                        "plugged": bool(b.power_plugged)}
        except Exception:
            pass
        return {"present": False, "ac": True}

    def _volumes(self):
        vols = []
        try:
            for part in psutil.disk_partitions(all=False):
                try:
                    u = psutil.disk_usage(part.mountpoint)
                except Exception:
                    continue
                opts = (part.opts or "").lower()
                vols.append({
                    "name": (part.device.replace("\\", "").rstrip(":") + ":")
                            if ":" in part.device else part.mountpoint,
                    "fstype": part.fstype or "-",
                    "total_gb": round(u.total / 1e9, 1),
                    "used_pct": round(u.percent),
                    "removable": "removable" in opts or "cdrom" in opts,
                })
        except Exception:
            pass
        return vols[:6]

    def _nets(self):
        nets = []
        try:
            stats = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()
            for name, st in stats.items():
                if not st.isup:
                    continue
                ip = ""
                for a in addrs.get(name, []):
                    if a.family == socket.AF_INET:
                        ip = a.address
                        break
                nets.append({"name": name[:22], "speed_mbps": st.speed or 0, "addr": ip})
            nets.sort(key=lambda n: (-(n["speed_mbps"] or 0), n["name"]))
        except Exception:
            pass
        return nets[:6]

    def sample(self) -> dict:
        up, down = self._net_rate()
        vm = psutil.virtual_memory()
        try:
            disk_pct = psutil.disk_usage("/").percent if platform.system() != "Windows" \
                else psutil.disk_usage("C:\\").percent
        except Exception:
            disk_pct = 0
        # peripherals are slow (spawns PowerShell) — refresh at most every 30s
        now = time.time()
        if now - self._periph_ts > 30:
            self._periph = _scan_peripherals()
            self._periph_ts = now
        if now - self._ollama_ts > 25:      # inference capability (on-prem cluster)
            self._ollama = _detect_ollama()
            self._ollama_ts = now
        try:
            uptime_min = round((now - psutil.boot_time()) / 60)
        except Exception:
            uptime_min = None
        return {
            "node_id": self.node_id,
            "name": self.name,
            "platform": self.platform,
            "ts": now,
            "cpu": round(psutil.cpu_percent(interval=None)),
            "cpu_cores": psutil.cpu_count() or 0,
            "mem": round(vm.percent),
            "mem_total_gb": round(vm.total / 1e9, 1),
            "disk": round(disk_pct),
            "net_up_kbps": up,
            "net_down_kbps": down,
            "gpu": _read_gpu(),
            "uptime_min": uptime_min,
            "power": self._power(),
            "volumes": self._volumes(),
            "net": self._nets(),
            "peripherals": self._periph,
            "ollama": self._ollama,
        }
