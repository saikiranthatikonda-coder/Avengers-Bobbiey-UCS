"""DEVICES · network helpers: the LAN address allowlist and passive-ish discovery.

Pure stdlib (no zeroconf dependency): a one-shot mDNS/DNS-SD query sent from an
ephemeral port (RFC 6762 §6.7 "legacy unicast", so responders answer us directly
and Windows' own mDNS service keeps port 5353) and an SSDP M-SEARCH. Both are
blocking and run in a worker thread (asyncio.to_thread) from devices.py.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import struct
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

# ── address allowlist (anti-SSRF) ─────────────────────────────────
_CGNAT = ipaddress.ip_network("100.64.0.0/10")          # Tailscale / carrier NAT
_BLOCKED = {ipaddress.ip_address("169.254.169.254"),     # cloud metadata endpoints
            ipaddress.ip_address("fd00:ec2::254")}


def is_lan_ip(ip: str) -> bool:
    """True only for loopback / private / link-local / CGNAT unicast addresses."""
    try:
        a = ipaddress.ip_address(str(ip).split("%", 1)[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    if a.is_loopback:
        return True
    if a in _BLOCKED or a.is_multicast or a.is_unspecified or a.is_reserved:
        return False
    if a.version == 4 and (a in _CGNAT):
        return True
    return bool(a.is_loopback or a.is_private or a.is_link_local)


_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-\.]{0,251}[A-Za-z0-9])?$")


def clean_host(host: str) -> str:
    """Normalise a user-typed host (IP, hostname, or URL) to a bare host[:port]-free name."""
    host = (host or "").strip()
    if "://" in host:
        host = urlsplit(host).hostname or ""
    host = host.strip("[]").strip()
    if not host:
        raise ValueError("host is required")
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return host
    except ValueError:
        pass
    if not _HOST_RE.match(host):
        raise ValueError("invalid host name")
    return host.lower()


def resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    out: list[str] = []
    for *_x, sa in infos:
        if sa[0] not in out:
            out.append(sa[0])
    return out


def vet_host(host: str) -> str:
    """Resolve host and return ONE vetted LAN IP to connect to (pinning defeats DNS
    rebinding). Raises ValueError when any resolved address is outside the LAN."""
    host = clean_host(host)
    ips = resolve(host)
    if not ips:
        raise ValueError(f"cannot resolve {host}")
    bad = [ip for ip in ips if not is_lan_ip(ip)]
    if bad:
        raise ValueError(f"{host} is not a private-LAN address — refused")
    return ips[0]


def local_ipv4() -> list[str]:
    """Private IPv4 addresses of interfaces that are up (multicast egress points)."""
    try:
        import psutil
        stats = psutil.net_if_stats()
        out = []
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            if st is not None and not st.isup:
                continue
            for a in addrs:
                if a.family == socket.AF_INET and is_lan_ip(a.address) and not a.address.startswith("127."):
                    out.append(a.address)
        return out
    except Exception:
        return []


# ── mDNS / DNS-SD ─────────────────────────────────────────────────
MDNS_TYPES = {
    "_hap._tcp.local":            ("homekit", "HomeKit accessory"),
    "_hue._tcp.local":            ("hue", "Philips Hue bridge"),
    "_shelly._tcp.local":         ("shelly", "Shelly"),
    "_googlecast._tcp.local":     ("cast", "Google Cast"),
    "_esphomelib._tcp.local":     ("esphome", "ESPHome"),
    "_wled._tcp.local":           ("wled", "WLED"),
    "_home-assistant._tcp.local": ("homeassistant", "Home Assistant"),
    "_mqtt._tcp.local":           ("mqtt", "MQTT broker"),
    "_http._tcp.local":           ("http", "HTTP device"),
}


def _enc_name(name: str) -> bytes:
    out = b""
    for label in name.strip(".").split("."):
        b = label.encode("utf-8")
        out += bytes([len(b)]) + b
    return out + b"\x00"


def build_mdns_query(types=None, qid: int = 0x4243) -> bytes:
    types = list(types or MDNS_TYPES)
    pkt = struct.pack("!HHHHHH", qid, 0, len(types), 0, 0, 0)
    for t in types:
        pkt += _enc_name(t) + struct.pack("!HH", 12, 1)      # PTR, IN
    return pkt


def _read_name(data: bytes, off: int) -> tuple[str, int]:
    labels, jumped, end, hops = [], False, off, 0
    while True:
        if off >= len(data):
            raise ValueError("truncated name")
        ln = data[off]
        if ln == 0:
            off += 1
            break
        if ln & 0xC0 == 0xC0:
            if off + 1 >= len(data):
                raise ValueError("truncated pointer")
            ptr = ((ln & 0x3F) << 8) | data[off + 1]
            if not jumped:
                end = off + 2
            jumped, off, hops = True, ptr, hops + 1
            if hops > 30:
                raise ValueError("pointer loop")
            continue
        off += 1
        labels.append(data[off:off + ln].decode("utf-8", errors="replace"))
        off += ln
    return ".".join(labels), (end if jumped else off)


def parse_dns(data: bytes) -> list[dict]:
    """Every resource record (answers + authority + additional) of a DNS message."""
    if len(data) < 12:
        return []
    _id, _fl, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
    off = 12
    for _ in range(qd):
        _n, off = _read_name(data, off)
        off += 4
    recs = []
    for _ in range(an + ns + ar):
        name, off = _read_name(data, off)
        if off + 10 > len(data):
            break
        rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", data[off:off + 10])
        off += 10
        rd = data[off:off + rdlen]
        rec = {"name": name, "type": rtype}
        try:
            if rtype == 12:                                   # PTR
                rec["target"] = _read_name(data, off)[0]
            elif rtype == 33 and rdlen >= 6:                  # SRV
                _p, _w, port = struct.unpack("!HHH", rd[:6])
                rec["port"], rec["target"] = port, _read_name(data, off + 6)[0]
            elif rtype == 16:                                 # TXT
                txt, i = {}, 0
                while i < len(rd):
                    ln = rd[i]
                    s = rd[i + 1:i + 1 + ln].decode("utf-8", errors="replace")
                    i += 1 + ln
                    if s:
                        k, _, v = s.partition("=")
                        txt[k.lower()[:40]] = v[:200]
                rec["txt"] = txt
            elif rtype == 1 and rdlen == 4:                   # A
                rec["ip"] = socket.inet_ntoa(rd)
        except Exception:
            pass
        recs.append(rec)
        off += rdlen
    return recs


def _instance_label(instance: str, stype: str) -> str:
    lab = instance[:-(len(stype) + 1)] if instance.endswith("." + stype) else instance.split(".")[0]
    return re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1))), lab).replace("\\", "")


def collate_mdns(packets: list[tuple[bytes, str]]) -> list[dict]:
    """Turn raw (packet, source_ip) replies into one entry per advertised service."""
    ptr, srv, txt, a = [], {}, {}, {}
    for data, src in packets:
        try:
            recs = parse_dns(data)
        except Exception:
            continue
        for r in recs:
            n = r["name"].lower()
            if r["type"] == 12 and "target" in r:
                ptr.append((n, r["target"], src))
            elif r["type"] == 33 and "target" in r:
                srv[n] = (r["target"].lower(), r["port"])
            elif r["type"] == 16 and "txt" in r:
                txt[n] = r["txt"]
            elif r["type"] == 1 and "ip" in r:
                a[n] = r["ip"]
    found, seen = [], set()
    for stype, instance, src in ptr:
        if stype not in MDNS_TYPES:
            continue
        kind, label = MDNS_TYPES[stype]
        il = instance.lower()
        target, port = srv.get(il, ("", 0))
        ip = a.get(target) or src
        name = _instance_label(instance, stype)
        t = txt.get(il, {})
        low = name.lower()
        if kind == "http":                     # refine generic _http advertisers
            if low.startswith("shelly"):
                kind, label = "shelly", "Shelly"
            elif low.startswith(("tasmota", "sonoff")):
                kind, label = "tasmota", "Tasmota"
            elif low.startswith("wled"):
                kind, label = "wled", "WLED"
        key = (kind, ip, port)
        if key in seen or not is_lan_ip(ip):
            continue
        seen.add(key)
        found.append({"kind": kind, "label": label, "name": (t.get("fn") or name)[:80],
                      "host": ip, "port": port, "via": "mdns", "service": stype,
                      "model": (t.get("md") or t.get("model") or t.get("app") or "")[:60]})
    return found


def mdns_scan(timeout: float = 2.5) -> list[dict]:
    query = build_mdns_query()
    socks = []
    for ip in (local_ipv4() or ["0.0.0.0"]):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            if ip != "0.0.0.0":
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.bind((ip, 0))
            s.settimeout(0.25)
            s.sendto(query, ("224.0.0.251", 5353))
            socks.append(s)
        except OSError:
            continue
    packets: list[tuple[bytes, str]] = []
    end = time.monotonic() + timeout
    try:
        while time.monotonic() < end and socks:
            for s in socks:
                try:
                    data, addr = s.recvfrom(9000)
                    packets.append((data, addr[0]))
                except (socket.timeout, OSError):
                    pass
            if len(packets) > 400:
                break
    finally:
        for s in socks:
            s.close()
    return collate_mdns(packets)


# ── SSDP ──────────────────────────────────────────────────────────
MSEARCH = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
           "MAN: \"ssdp:discover\"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n").encode()


def parse_ssdp(data: bytes) -> dict:
    h = {}
    for line in data.decode("utf-8", errors="replace").split("\r\n")[1:]:
        k, sep, v = line.partition(":")
        if sep:
            h[k.strip().lower()] = v.strip()[:300]
    return h


def classify_ssdp(h: dict, desc: dict | None = None) -> tuple[str, str]:
    desc = desc or {}
    blob = " ".join([h.get("st", ""), h.get("server", ""), h.get("usn", ""),
                     desc.get("deviceType", ""), desc.get("manufacturer", ""),
                     desc.get("modelName", "")]).lower()
    if "hue-bridgeid" in h or "ipbridge" in blob or "philips hue" in blob:
        return "hue", "Philips Hue bridge"
    if "mediarenderer" in blob or "mediaserver" in blob or "dial" in blob:
        return "dlna", "DLNA / UPnP media"
    if "internetgatewaydevice" in blob:
        return "upnp", "Router (UPnP IGD)"
    if "wled" in blob:
        return "wled", "WLED"
    return "upnp", "UPnP device"


def _fetch_desc(location: str) -> dict:
    u = urlsplit(location)
    if u.scheme != "http" or not u.hostname or not is_lan_ip(u.hostname):
        return {}
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(location, timeout=2) as r:
            raw = r.read(65536)
        root = ET.fromstring(raw)
    except Exception:
        return {}
    out = {}
    for el in root.iter():
        tag = el.tag.split("}", 1)[-1]
        if tag in ("friendlyName", "manufacturer", "modelName", "deviceType") and tag not in out and el.text:
            out[tag] = el.text.strip()[:80]
    return out


def ssdp_scan(timeout: float = 3.0, describe: int = 15) -> list[dict]:
    socks = []
    for ip in (local_ipv4() or ["0.0.0.0"]):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if ip != "0.0.0.0":
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.bind((ip, 0))
            s.settimeout(0.25)
            s.sendto(MSEARCH, ("239.255.255.250", 1900))
            socks.append(s)
        except OSError:
            continue
    by_loc: dict[str, dict] = {}
    end = time.monotonic() + timeout
    try:
        while time.monotonic() < end and socks:
            for s in socks:
                try:
                    data, addr = s.recvfrom(4096)
                except (socket.timeout, OSError):
                    continue
                if not is_lan_ip(addr[0]):
                    continue
                h = parse_ssdp(data)
                loc = h.get("location") or addr[0]
                ent = by_loc.setdefault(loc, {"host": addr[0], "hdr": h, "sts": set()})
                ent["sts"].add(h.get("st", ""))
                if "hue-bridgeid" in h:
                    ent["hdr"]["hue-bridgeid"] = h["hue-bridgeid"]
    finally:
        for s in socks:
            s.close()
    found, seen = [], set()
    for i, (loc, ent) in enumerate(by_loc.items()):
        h = dict(ent["hdr"])
        h["st"] = " ".join(sorted(ent["sts"]))
        desc = _fetch_desc(loc) if i < describe and loc.startswith("http") else {}
        kind, label = classify_ssdp(h, desc)
        key = (kind, ent["host"], desc.get("friendlyName", ""))
        if key in seen:
            continue
        seen.add(key)
        try:
            port = (urlsplit(loc).port or 0) if loc.startswith("http") else 0
        except ValueError:
            port = 0
        found.append({"kind": kind, "label": label, "via": "ssdp", "host": ent["host"], "port": port,
                      "name": (desc.get("friendlyName") or h.get("server") or ent["host"])[:80],
                      "model": " ".join(x for x in (desc.get("manufacturer"), desc.get("modelName")) if x)[:60],
                      "service": (desc.get("deviceType") or "")[:80]})
    return found


def discover() -> dict:
    """Run both scans (blocking; call in a thread)."""
    t0 = time.time()
    errors = []
    md: list[dict] = []
    sd: list[dict] = []
    try:
        md = mdns_scan()
    except Exception as e:
        errors.append(f"mdns: {type(e).__name__}")
    try:
        sd = ssdp_scan()
    except Exception as e:
        errors.append(f"ssdp: {type(e).__name__}")
    # an mDNS hit is richer than an SSDP one for the same host+kind
    have = {(d["kind"], d["host"]) for d in md}
    items = md + [d for d in sd if (d["kind"], d["host"]) not in have]
    items.sort(key=lambda d: (d["kind"], d["host"]))
    return {"items": items[:200], "at": time.time(), "took_s": round(time.time() - t0, 1),
            "interfaces": local_ipv4(), "errors": errors}
