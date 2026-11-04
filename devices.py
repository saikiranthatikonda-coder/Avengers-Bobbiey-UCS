"""DEVICES & CONNECTIVITY: smart-device adapters over real local protocols, LAN
discovery, and the node-connectivity report (devices_link.py).

Adapters (each speaks the vendor's documented local API, nothing is simulated):
  homeassistant  REST  /api/, /api/states, /api/services/<domain>/<service>  (Bearer token)
  mqtt           MQTT 3.1.1 over TCP, pure Python: publish / read one message
  hue            Hue bridge v1 local API, link-button pairing (POST /api)
  shelly         Gen1 /relay/<n>?turn=…  ·  Gen2+ RPC /rpc/Switch.Set
  tasmota        /cm?cmnd=Power<n> On|Off|Toggle, Dimmer <n>
  esphome        web_server REST  POST /<domain>/<id>/turn_on|turn_off|toggle
  wled           JSON API /json/state
  http           generic webhook device (on/off/status paths)
  cast, dlna     read-only presence (Google Cast eureka_info / UPnP description)

Security: every device address must resolve to a private-LAN / loopback / CGNAT IP
(the connection is pinned to that vetted IP); the only exception is an explicitly
configured HTTPS Home Assistant URL with TLS verification on. Secrets live in the
gitignored .update/devices.json and only ever leave this module masked. Adding,
pairing and removing devices needs COMMANDER on the host machine; control needs
COMMANDER and is audited.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import struct
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import devices_link
import devices_net
from atomicio import atomic_write_text

STORE = Path(__file__).resolve().parent / ".update" / "devices.json"
TIMEOUT = 5.0
MAX_DEVICES = 100
_TRANSPORT = None          # tests inject httpx.MockTransport here

# ── adapter catalog (drives the add form in the dashboard) ────────
F_HOST = {"key": "host", "label": "IP / hostname", "type": "text", "required": True}
ADAPTERS: dict[str, dict] = {
    "homeassistant": {"label": "Home Assistant", "control": True,
                      "note": "Covers Matter, Zigbee, Z-Wave, HomeKit and thousands of brands. Profile → Security → Long-lived access token.",
                      "fields": [{"key": "url", "label": "URL (http://homeassistant.local:8123)", "type": "text", "required": True},
                                 {"key": "token", "label": "Long-lived access token", "type": "password", "required": True, "secret": True}]},
    "mqtt": {"label": "MQTT broker", "control": True, "note": "Publish to / read from topics on a LAN broker (Mosquitto, EMQX, HA add-on).",
             "fields": [F_HOST, {"key": "port", "label": "Port (1883)", "type": "number"},
                        {"key": "user", "label": "Username", "type": "text"},
                        {"key": "password", "label": "Password", "type": "password", "secret": True}]},
    "hue": {"label": "Philips Hue bridge", "control": True, "pair": True,
            "note": "Press the round link button on the bridge, then PAIR within 30 s.",
            "fields": [F_HOST]},
    "shelly": {"label": "Shelly", "control": True, "note": "Gen1 and Gen2+ detected automatically. Password only if device auth is on.",
               "fields": [F_HOST, {"key": "password", "label": "Password (optional)", "type": "password", "secret": True}]},
    "tasmota": {"label": "Tasmota", "control": True, "note": "HTTP command API (/cm). Web password only if set.",
                "fields": [F_HOST, {"key": "user", "label": "User (admin)", "type": "text"},
                           {"key": "password", "label": "Password (optional)", "type": "password", "secret": True}]},
    "esphome": {"label": "ESPHome", "control": True, "note": "Needs web_server: in the YAML. Entities like switch/relay, light/strip.",
                "fields": [F_HOST, {"key": "entities", "label": "Entities (switch/relay, light/strip)", "type": "text", "required": True},
                           {"key": "user", "label": "User (optional)", "type": "text"},
                           {"key": "password", "label": "Password (optional)", "type": "password", "secret": True}]},
    "wled": {"label": "WLED", "control": True, "note": "JSON API, no auth.", "fields": [F_HOST]},
    "http": {"label": "HTTP / webhook device", "control": True, "note": "Any LAN device with on/off URLs.",
             "fields": [{"key": "url", "label": "Base URL (http://192.168.1.50)", "type": "text", "required": True},
                        {"key": "method", "label": "Method (GET/POST/PUT)", "type": "text"},
                        {"key": "on_path", "label": "ON path (/on)", "type": "text"},
                        {"key": "off_path", "label": "OFF path (/off)", "type": "text"},
                        {"key": "status_path", "label": "Status path (/status)", "type": "text"},
                        {"key": "token", "label": "Bearer token (optional)", "type": "password", "secret": True}]},
    "cast": {"label": "Google Cast", "control": False, "note": "Read-only presence.", "fields": [F_HOST]},
    "dlna": {"label": "DLNA / UPnP media", "control": False, "note": "Read-only presence.",
             "fields": [F_HOST, {"key": "port", "label": "Port", "type": "number"}]},
}
SECRET_KEYS = ("token", "password")

_devices: dict[str, dict] = {}
_status: dict[str, dict] = {}
_discovery: dict = {"items": [], "at": 0, "running": False}
_conn: dict = {"data": None, "at": 0.0}
_conn_lock = asyncio.Lock()
_disc_lock = asyncio.Lock()


# ── store ─────────────────────────────────────────────────────────
def load() -> None:
    _devices.clear()
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        for d in data.get("devices", []):
            if isinstance(d, dict) and d.get("kind") in ADAPTERS and d.get("id"):
                _devices[d["id"]] = d
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[devices] could not read {STORE.name}: {type(e).__name__}")


def save() -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(STORE, json.dumps({"devices": list(_devices.values())}, indent=1))


def mask(value: str | None, kind: str = "token") -> str:
    if not value:
        return ""
    if kind == "token" and len(value) >= 20:
        return "••••" + value[-4:]
    return "••••••"


def public(d: dict) -> dict:
    """The browser-safe view of a device: secrets replaced by masks."""
    out = {k: v for k, v in d.items() if k not in SECRET_KEYS}
    for k in SECRET_KEYS:
        if d.get(k):
            out[k] = mask(d[k], k)
            out["has_" + k] = True
    out["control"] = ADAPTERS[d["kind"]]["control"]
    out["label"] = ADAPTERS[d["kind"]]["label"]
    out["status"] = _status.get(d["id"], {"online": None, "detail": "not checked yet"})
    return out


# ── address vetting ───────────────────────────────────────────────
class Refused(Exception):
    pass


def _fmt_host(ip: str) -> str:
    return f"[{ip}]" if ":" in ip else ip


async def _vet(host: str) -> str:
    try:
        return await asyncio.to_thread(devices_net.vet_host, host)
    except ValueError as e:
        raise Refused(str(e))


async def target(d: dict) -> tuple[str, dict, bool]:
    """(base_url pinned to a vetted IP where possible, extra headers, verify_tls)."""
    kind = d["kind"]
    if kind in ("homeassistant", "http"):
        u = urlsplit(d.get("url", ""))
        if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
            raise Refused("URL must be http(s)://host[:port]")
        host = devices_net.clean_host(u.hostname)
        port = f":{u.port}" if u.port else ""
        path = u.path.rstrip("/")
        ips = await asyncio.to_thread(devices_net.resolve, host)
        if not ips:
            raise Refused(f"cannot resolve {host}")
        lan = all(devices_net.is_lan_ip(ip) for ip in ips)
        if not lan:
            if kind == "homeassistant" and u.scheme == "https":
                return f"https://{host}{port}{path}", {}, True       # explicit public HTTPS HA only
            raise Refused(f"{host} is not a private-LAN address — refused (only an https Home Assistant URL may be remote)")
        if u.scheme == "https":
            return f"https://{host}{port}{path}", {}, bool(d.get("verify_tls", True))
        return f"http://{_fmt_host(ips[0])}{port}{path}", {"Host": host + port}, True
    ip = await _vet(d.get("host", ""))
    port = d.get("port")
    if kind == "cast":
        port = 8008
    return f"http://{_fmt_host(ip)}" + (f":{int(port)}" if port else ""), {}, True


async def _http(d: dict, method: str, path: str, *, params=None, json_body=None,
                headers=None, auth=None, timeout: float = TIMEOUT) -> httpx.Response:
    base, extra, verify = await target(d)
    h = {**extra, **(headers or {})}
    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False,
                                 verify=verify, transport=_TRANSPORT) as c:
        return await c.request(method, base + path, params=params, json=json_body, headers=h, auth=auth)


# ── command builders (pure; unit-tested) ──────────────────────────
HA_DOMAINS = {"light", "switch", "fan", "cover", "lock", "climate", "media_player", "scene", "script",
              "input_boolean", "button", "vacuum", "humidifier", "siren", "valve", "automation"}
HA_SERVICES = {"turn_on", "turn_off", "toggle", "open_cover", "close_cover", "stop_cover", "lock", "unlock",
               "media_play", "media_pause", "media_stop", "media_play_pause", "volume_set", "volume_mute",
               "set_temperature", "set_hvac_mode", "press", "start", "pause", "return_to_base",
               "set_percentage", "open_valve", "close_valve", "trigger"}
HA_DATA = {"brightness_pct": (0, 100), "color_temp_kelvin": (1500, 9000), "temperature": (-50, 100),
           "volume_level": (0, 1), "percentage": (0, 100)}
_ENTITY = re.compile(r"^([a-z_]+)\.[a-z0-9_]{1,120}$")


def ha_service_call(entity_id: str, service: str, data: dict | None = None) -> tuple[str, str, dict]:
    m = _ENTITY.match(entity_id or "")
    if not m:
        raise ValueError("invalid entity_id")
    domain = m.group(1)
    if domain not in HA_DOMAINS:
        raise ValueError(f"domain '{domain}' is not controllable from BUCS")
    if service not in HA_SERVICES:
        raise ValueError(f"service '{service}' is not allowed")
    body: dict = {"entity_id": entity_id}
    for k, v in (data or {}).items():
        if k in HA_DATA:
            lo, hi = HA_DATA[k]
            body[k] = max(lo, min(hi, float(v)))
        elif k == "hvac_mode" and v in ("off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"):
            body[k] = v
        elif k == "is_volume_muted":
            body[k] = bool(v)
        elif k == "rgb_color" and isinstance(v, list) and len(v) == 3:
            body[k] = [max(0, min(255, int(x))) for x in v]
    return "POST", f"/api/services/{domain}/{service}", body


def _onoff(action: str) -> str:
    if action not in ("on", "off", "toggle"):
        raise ValueError("action must be on, off or toggle")
    return action


def shelly_cmd(gen: int, action: str, channel: int = 0) -> tuple[str, dict]:
    action, ch = _onoff(action), int(channel)
    if not 0 <= ch <= 7:
        raise ValueError("channel out of range")
    if gen >= 2:
        if action == "toggle":
            return "/rpc/Switch.Toggle", {"id": ch}
        return "/rpc/Switch.Set", {"id": ch, "on": "true" if action == "on" else "false"}
    return f"/relay/{ch}", {"turn": action}


def tasmota_cmd(action: str, channel: int = 1, value=None, user: str = "", password: str = "") -> tuple[str, dict]:
    ch = int(channel)
    if not 0 <= ch <= 8:
        raise ValueError("channel out of range")
    if action == "dimmer":
        cmnd = f"Dimmer {max(0, min(100, int(value)))}"
    elif action == "state":
        cmnd = "State"
    else:
        cmnd = f"Power{ch or ''} {_onoff(action).capitalize()}"
    params = {"cmnd": cmnd}
    if password:
        params = {"user": user or "admin", "password": password, **params}
    return "/cm", params


def hue_cmd(key: str, light: str, action: str, value=None, current_on: bool | None = None) -> tuple[str, str, dict]:
    if not re.fullmatch(r"[A-Za-z0-9\-]{10,64}", key or ""):
        raise ValueError("bridge is not paired")
    if not re.fullmatch(r"\d{1,4}", str(light)):
        raise ValueError("invalid light id")
    if action == "brightness":
        body = {"on": True, "bri": max(1, min(254, int(value)))}
    else:
        a = _onoff(action)
        body = {"on": (not current_on) if a == "toggle" else a == "on"}
    return "PUT", f"/api/{key}/lights/{light}/state", body


_ESP_ENTITY = re.compile(r"^(switch|light|fan|cover|lock|button)/[a-z0-9_]{1,64}$")


def esphome_cmd(entity: str, action: str, value=None) -> tuple[str, str, dict]:
    if not _ESP_ENTITY.match(entity or ""):
        raise ValueError("entity must look like switch/relay or light/strip")
    domain = entity.split("/", 1)[0]
    params: dict = {}
    if domain == "button":
        verb = "press"
    elif domain == "cover":
        verb = {"on": "open", "off": "close", "toggle": "toggle"}[_onoff(action)]
    elif domain == "lock":
        verb = {"on": "lock", "off": "unlock", "toggle": "open"}[_onoff(action)]
    elif action == "brightness" and domain == "light":
        verb, params = "turn_on", {"brightness": max(0, min(255, int(value)))}
    else:
        verb = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}[_onoff(action)]
    return "POST", f"/{entity}/{verb}", params


def wled_cmd(action: str, value=None) -> dict:
    if action == "brightness":
        return {"on": True, "bri": max(1, min(255, int(value)))}
    return {"on": {"on": True, "off": False, "toggle": "t"}[_onoff(action)]}


def _rel_path(p: str | None, default: str = "/") -> str:
    p = (p or default).strip()
    if not p.startswith("/") or "://" in p or "\\" in p or ".." in p or len(p) > 200:
        raise ValueError("path must be a relative path like /on")
    return p


# ── MQTT 3.1.1 (just enough: CONNECT, PUBLISH QoS0, SUBSCRIBE, DISCONNECT) ──
def _varint(n: int) -> bytes:
    out = b""
    while True:
        b, n = n % 128, n // 128
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _mstr(s: str | bytes) -> bytes:
    b = s.encode() if isinstance(s, str) else s
    return struct.pack("!H", len(b)) + b


def mqtt_connect_packet(client_id: str, user: str = "", password: str = "", keepalive: int = 30) -> bytes:
    flags = 0x02 | (0x80 if user else 0) | (0x40 if (user and password) else 0)
    var = _mstr("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive)
    payload = _mstr(client_id) + (_mstr(user) if user else b"") + (_mstr(password) if (user and password) else b"")
    body = var + payload
    return b"\x10" + _varint(len(body)) + body


def mqtt_publish_packet(topic: str, payload: str, retain: bool = False) -> bytes:
    body = _mstr(topic) + payload.encode()
    return bytes([0x30 | (1 if retain else 0)]) + _varint(len(body)) + body


def mqtt_subscribe_packet(topic: str, pid: int = 1) -> bytes:
    body = struct.pack("!H", pid) + _mstr(topic) + b"\x00"
    return b"\x82" + _varint(len(body)) + body


def check_topic(topic: str, wildcards: bool = False) -> str:
    if not topic or len(topic) > 200 or "\x00" in topic:
        raise ValueError("invalid topic")
    if not wildcards and ("+" in topic or "#" in topic):
        raise ValueError("wildcards are not allowed when publishing")
    return topic


async def _mqtt_read_packet(r: asyncio.StreamReader) -> tuple[int, bytes]:
    hdr = (await r.readexactly(1))[0]
    mult, n = 1, 0
    for _ in range(4):
        b = (await r.readexactly(1))[0]
        n += (b & 0x7F) * mult
        if not b & 0x80:
            break
        mult *= 128
    if n > 65536:
        raise ValueError("MQTT packet too large")
    return hdr, await r.readexactly(n)


async def mqtt_session(d: dict, publish: tuple | None = None, read_topic: str | None = None,
                       wait: float = 3.0) -> dict:
    ip = await _vet(d.get("host", ""))
    port = int(d.get("port") or 1883)
    r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=TIMEOUT)
    try:
        w.write(mqtt_connect_packet("bucs-" + secrets.token_hex(3), d.get("user", ""), d.get("password", "")))
        await w.drain()
        hdr, body = await asyncio.wait_for(_mqtt_read_packet(r), timeout=TIMEOUT)
        if hdr != 0x20 or len(body) < 2:
            return {"ok": False, "error": "not an MQTT broker"}
        if body[1] != 0:
            reasons = {1: "protocol version", 2: "client id", 3: "server unavailable", 4: "bad username/password", 5: "not authorised"}
            return {"ok": False, "error": "broker refused: " + reasons.get(body[1], str(body[1]))}
        out: dict = {"ok": True}
        if publish:
            w.write(mqtt_publish_packet(*publish))
            await w.drain()
            out["published"] = publish[0]
        if read_topic:
            w.write(mqtt_subscribe_packet(read_topic))
            await w.drain()
            end = time.monotonic() + wait
            while time.monotonic() < end:
                try:
                    hdr, body = await asyncio.wait_for(_mqtt_read_packet(r), timeout=max(0.1, end - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                if hdr >> 4 == 3:
                    tl = struct.unpack("!H", body[:2])[0]
                    off = 2 + tl + (2 if (hdr >> 1) & 3 else 0)
                    out["message"] = {"topic": body[2:2 + tl].decode(errors="replace")[:200],
                                      "payload": body[off:].decode(errors="replace")[:2000]}
                    break
            out.setdefault("message", None)
        w.write(b"\xe0\x00")
        await w.drain()
        return out
    finally:
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass


# ── status ────────────────────────────────────────────────────────
def _js(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:
        return None


async def _shelly_gen(d: dict) -> int:
    if d.get("gen"):
        return int(d["gen"])
    j = _js(await _http(d, "GET", "/shelly")) or {}
    gen = int(j.get("gen") or 1)
    d["gen"] = gen
    return gen


def _auth(d: dict, digest: bool = False):
    if not d.get("password"):
        return None
    user = d.get("user") or "admin"
    return httpx.DigestAuth(user, d["password"]) if digest else (user, d["password"])


async def check(d: dict) -> dict:
    """One honest status probe. online=None means 'unknown', never a guess."""
    kind = d["kind"]
    try:
        if kind == "homeassistant":
            r = await _http(d, "GET", "/api/", headers={"Authorization": f"Bearer {d.get('token', '')}"})
            if r.status_code == 401:
                return {"online": True, "detail": "reachable · token rejected (401)", "auth": False}
            ok = r.status_code == 200
            return {"online": ok, "detail": (_js(r) or {}).get("message", f"HTTP {r.status_code}") if ok else f"HTTP {r.status_code}"}
        if kind == "mqtt":
            res = await mqtt_session(d)
            return {"online": True, "detail": "broker accepted connection" if res["ok"] else res["error"], "auth": res["ok"]}
        if kind == "hue":
            if not d.get("token"):
                j = _js(await _http(d, "GET", "/api/0/config")) or {}
                return {"online": True, "detail": f"{j.get('name', 'bridge')} · NOT PAIRED"}
            j = _js(await _http(d, "GET", f"/api/{d['token']}/lights"))
            if isinstance(j, list):
                return {"online": True, "detail": "bridge answered · pairing no longer valid", "auth": False}
            lights = [{"id": k, "name": str(v.get("name", k))[:40], "on": bool(v.get("state", {}).get("on")),
                       "bri": v.get("state", {}).get("bri"), "reachable": v.get("state", {}).get("reachable")}
                      for k, v in (j or {}).items()][:60]
            return {"online": True, "detail": f"{len(lights)} light(s) · {sum(l['on'] for l in lights)} on", "lights": lights}
        if kind == "shelly":
            gen = await _shelly_gen(d)
            if gen >= 2:
                r = await _http(d, "GET", "/rpc/Shelly.GetStatus", auth=_auth(d, digest=True))
                j = _js(r) or {}
                sw = [{"ch": int(k.split(":")[1]), "on": bool(v.get("output"))} for k, v in j.items()
                      if k.startswith("switch:") and isinstance(v, dict)]
            else:
                r = await _http(d, "GET", "/status", auth=_auth(d))
                j = _js(r) or {}
                sw = [{"ch": i, "on": bool(x.get("ison"))} for i, x in enumerate(j.get("relays", []))]
            if r.status_code == 401:
                return {"online": True, "detail": "reachable · password required", "auth": False}
            return {"online": r.status_code == 200, "detail": f"Gen{gen} · " + (", ".join(f"ch{s['ch']} {'ON' if s['on'] else 'off'}" for s in sw) or "no relays"),
                    "channels": sw}
        if kind == "tasmota":
            path, params = tasmota_cmd("state", 1, user=d.get("user", ""), password=d.get("password", ""))
            j = _js(await _http(d, "GET", path, params=params)) or {}
            if "WARNING" in j:
                return {"online": True, "detail": "reachable · " + str(j["WARNING"])[:60], "auth": False}
            pw = {k: v for k, v in j.items() if k.startswith("POWER")}
            return {"online": bool(j), "detail": ", ".join(f"{k} {v}" for k, v in pw.items()) or "no relays",
                    "channels": [{"ch": int(k[5:] or 1), "on": v == "ON"} for k, v in pw.items()]}
        if kind == "esphome":
            ents = []
            for ent in [e.strip() for e in d.get("entities", "").split(",") if e.strip()][:12]:
                r = await _http(d, "GET", "/" + ent, auth=_auth(d))
                j = _js(r) or {}
                ents.append({"entity": ent, "state": str(j.get("state", f"HTTP {r.status_code}"))[:30]})
            return {"online": True, "detail": ", ".join(f"{e['entity']} {e['state']}" for e in ents) or "online", "entities": ents}
        if kind == "wled":
            j = _js(await _http(d, "GET", "/json/state")) or {}
            return {"online": "on" in j, "detail": f"{'ON' if j.get('on') else 'off'} · bri {j.get('bri', '—')}", "on": bool(j.get("on"))}
        if kind == "http":
            r = await _http(d, "GET", _rel_path(d.get("status_path")), headers=_bearer(d))
            return {"online": r.status_code < 500, "detail": f"HTTP {r.status_code}"}
        if kind == "cast":
            r = await _http(d, "GET", "/setup/eureka_info", params={"params": "name,build_info"})
            j = _js(r) or {}
            return {"online": True, "detail": j.get("name") or f"answers on :8008 (HTTP {r.status_code})"}
        if kind == "dlna":
            r = await _http(d, "GET", _rel_path(d.get("path"), "/"))
            return {"online": True, "detail": f"answers (HTTP {r.status_code})"}
    except Refused as e:
        return {"online": False, "detail": str(e)}
    except (httpx.TimeoutException, asyncio.TimeoutError):
        return {"online": False, "detail": "offline · timed out"}
    except (httpx.ConnectError, ConnectionError, OSError):
        return {"online": False, "detail": "offline · connection refused/unreachable"}
    except Exception as e:
        return {"online": None, "detail": f"error: {type(e).__name__}"}
    return {"online": None, "detail": "unknown adapter"}


def _bearer(d: dict) -> dict:
    return {"Authorization": f"Bearer {d['token']}"} if d.get("token") else {}


async def refresh(did: str) -> dict:
    d = _devices[did]
    st = await check(d)
    st["checked"] = time.time()
    _status[did] = st
    return st


# ── control ───────────────────────────────────────────────────────
async def control(d: dict, action: str, p: dict) -> dict:
    kind = d["kind"]
    if not ADAPTERS[kind]["control"]:
        raise ValueError(f"{ADAPTERS[kind]['label']} is read-only in BUCS")
    if kind == "homeassistant":
        method, path, body = ha_service_call(p.get("entity_id", ""), p.get("service") or action, p.get("data") or {})
        r = await _http(d, method, path, json_body=body, headers=_bearer(d), timeout=10)
        return {"ok": r.status_code == 200, "http": r.status_code}
    if kind == "mqtt":
        if action == "publish":
            payload = str(p.get("payload", ""))
            if len(payload) > 4096:
                raise ValueError("payload too large")
            res = await mqtt_session(d, publish=(check_topic(p.get("topic", "")), payload, bool(p.get("retain"))))
        elif action == "read":
            res = await mqtt_session(d, read_topic=check_topic(p.get("topic", ""), wildcards=True))
        else:
            raise ValueError("MQTT actions: publish, read")
        return res
    if kind == "hue":
        cur = None
        if action == "toggle":
            j = _js(await _http(d, "GET", f"/api/{d.get('token', '')}/lights/{int(p.get('light', 0))}")) or {}
            cur = bool((j.get("state") or {}).get("on"))
        method, path, body = hue_cmd(d.get("token", ""), str(p.get("light", "")), action, p.get("value"), cur)
        j = _js(await _http(d, method, path, json_body=body))
        ok = isinstance(j, list) and all("success" in x for x in j)
        return {"ok": ok, "error": None if ok else str(j)[:160]}
    if kind == "shelly":
        gen = await _shelly_gen(d)
        path, params = shelly_cmd(gen, action, int(p.get("channel", 0)))
        r = await _http(d, "GET", path, params=params, auth=_auth(d, digest=gen >= 2))
        return {"ok": r.status_code == 200, "http": r.status_code}
    if kind == "tasmota":
        path, params = tasmota_cmd(action, int(p.get("channel", 1)), p.get("value"), d.get("user", ""), d.get("password", ""))
        j = _js(await _http(d, "GET", path, params=params)) or {}
        return {"ok": bool(j) and "WARNING" not in j, "result": {k: v for k, v in j.items() if k != "WARNING"}}
    if kind == "esphome":
        ents = [e.strip() for e in d.get("entities", "").split(",") if e.strip()]
        ent = p.get("entity") or (ents[0] if ents else "")
        if ent not in ents:
            raise ValueError("unknown entity for this device")
        method, path, params = esphome_cmd(ent, action, p.get("value"))
        r = await _http(d, method, path, params=params, auth=_auth(d))
        return {"ok": r.status_code == 200, "http": r.status_code}
    if kind == "wled":
        r = await _http(d, "POST", "/json/state", json_body=wled_cmd(action, p.get("value")))
        return {"ok": r.status_code == 200, "http": r.status_code}
    if kind == "http":
        if action not in ("on", "off"):
            raise ValueError("HTTP device actions: on, off")
        path = _rel_path(d.get("on_path" if action == "on" else "off_path"), "/" + action)
        method = (d.get("method") or "POST").upper()
        if method not in ("GET", "POST", "PUT"):
            raise ValueError("method must be GET, POST or PUT")
        r = await _http(d, method, path, headers=_bearer(d))
        return {"ok": 200 <= r.status_code < 300, "http": r.status_code}
    raise ValueError("unsupported")


async def entities(d: dict) -> dict:
    """Home Assistant: controllable entities with state (read-only listing)."""
    if d["kind"] != "homeassistant":
        return {"items": []}
    r = await _http(d, "GET", "/api/states", headers=_bearer(d), timeout=10)
    if r.status_code != 200:
        return {"items": [], "error": f"HTTP {r.status_code}"}
    items = []
    for s in _js(r) or []:
        eid = s.get("entity_id", "")
        if _ENTITY.match(eid) and eid.split(".")[0] in HA_DOMAINS:
            items.append({"entity_id": eid, "state": str(s.get("state"))[:24],
                          "name": str((s.get("attributes") or {}).get("friendly_name") or eid)[:60]})
    items.sort(key=lambda x: x["entity_id"])
    return {"items": items[:300], "total": len(items)}


# ── add / pair ────────────────────────────────────────────────────
def _clean(kind: str, body: dict) -> dict:
    if kind not in ADAPTERS:
        raise ValueError("unknown device type")
    d: dict = {"kind": kind, "name": str(body.get("name") or ADAPTERS[kind]["label"]).strip()[:60]}
    for f in ADAPTERS[kind]["fields"]:
        v = body.get(f["key"])
        if v in (None, ""):
            if f.get("required"):
                raise ValueError(f"{f['label']} is required")
            continue
        if f["type"] == "number":
            v = int(v)
            if not 1 <= v <= 65535:
                raise ValueError("port out of range")
        else:
            v = str(v).strip()[:2000 if f.get("secret") else 300]
        d[f["key"]] = v
    if "host" in d:
        d["host"] = devices_net.clean_host(d["host"])
    for k in ("on_path", "off_path", "status_path"):
        if k in d:
            _rel_path(d[k])
    if kind == "esphome":
        for e in d["entities"].split(","):
            if e.strip() and not _ESP_ENTITY.match(e.strip()):
                raise ValueError(f"bad entity '{e.strip()}' (use switch/relay, light/strip)")
    if kind == "http" and d.get("method") and d["method"].upper() not in ("GET", "POST", "PUT"):
        raise ValueError("method must be GET, POST or PUT")
    if kind == "homeassistant" and body.get("verify_tls") is False:
        d["verify_tls"] = False
    return d


async def hue_pair(host: str) -> dict:
    d = {"kind": "hue", "host": devices_net.clean_host(host)}
    r = await _http(d, "POST", "/api", json_body={"devicetype": "bucs#dashboard"})
    j = _js(r)
    if isinstance(j, list) and j and "success" in j[0]:
        return {"ok": True, "token": j[0]["success"].get("username", "")}
    if isinstance(j, list) and j and (j[0].get("error") or {}).get("type") == 101:
        return {"ok": False, "pending": True, "error": "Press the link button on the bridge, then PAIR again within 30 s."}
    return {"ok": False, "error": "not a Hue bridge response"}


# ── connectivity + discovery (cached) ─────────────────────────────
async def connectivity_snapshot(state: dict | None = None, force: bool = False) -> dict:
    async with _conn_lock:
        age = time.time() - _conn["at"]
        if _conn["data"] is None or age > 300 or (force and age > 15):
            _conn["data"] = await devices_link.gather((state or {}).get("fleet"))
            _conn["at"] = time.time()
    # link probes are cached (slow), but the fleet counts are cheap and shown next to the
    # core FLEET card, so they are re-read on every request instead of lagging up to 5 min
    fleet = (state or {}).get("fleet")
    if fleet is not None and isinstance(_conn["data"], dict):
        try:
            agg = fleet.snapshot().get("aggregate", {})
            _conn["data"]["fleet"] = {"nodes": agg.get("nodes", 0), "online": agg.get("online", 0)}
        except Exception:
            pass
    return _conn["data"]


async def run_discovery() -> dict:
    if _disc_lock.locked():
        return _discovery
    async with _disc_lock:
        _discovery["running"] = True
        try:
            res = await asyncio.to_thread(devices_net.discover)
        finally:
            _discovery["running"] = False
        known = {(d["kind"], d.get("host")) for d in _devices.values()}
        for it in res["items"]:
            it["added"] = (it["kind"], it["host"]) in known
            it["addable"] = it["kind"] in ADAPTERS
        if not res["items"]:
            res["note"] = ("No replies. Either nothing on this network advertises itself, or replies are blocked: "
                           "Wi-Fi client isolation, or Windows Firewall dropping inbound UDP on a Public network profile. "
                           "Devices can still be added by IP.")
        _discovery.update(res)
        return _discovery


# ── router ────────────────────────────────────────────────────────
def _locked() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "OBSERVER role — command actions are locked. Switch to COMMANDER to proceed."},
                        status_code=403)


def _hostonly() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "devices and their secrets can only be added or removed on the host machine"},
                        status_code=403)


def _bad(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": msg}, status_code=code)


async def _body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def build_router(ctx) -> APIRouter:
    load()
    router = APIRouter()
    commander, host_only, audit = ctx["commander"], ctx["host_only"], ctx["audit"]

    @router.get("/api/devices")
    async def list_devices():
        # "added" is recomputed per request so a discovery row flips to ADDED / back to ADD as
        # soon as a device is added or removed (here or in another tab), not on the next scan
        known = {(d["kind"], d.get("host")) for d in _devices.values()}
        disc = {k: v for k, v in _discovery.items()}
        disc["items"] = [{**it, "added": (it.get("kind"), it.get("host")) in known} for it in _discovery.get("items", [])]
        return {"devices": [public(d) for d in _devices.values()],
                "adapters": {k: {kk: vv for kk, vv in v.items()} for k, v in ADAPTERS.items()},
                "discovery": disc,
                "commander": bool(commander())}

    @router.post("/api/devices")
    async def add_device(request: Request):
        if not commander():
            return _locked()
        if not host_only(request):
            return _hostonly()
        if len(_devices) >= MAX_DEVICES:
            return _bad("device limit reached")
        b = await _body(request)
        try:
            d = _clean(str(b.get("kind", "")), b)
            await target(d)                                   # refuses non-LAN addresses up front
        except Refused as e:
            return _bad(str(e), 403)
        except (ValueError, TypeError) as e:
            return _bad(str(e))
        d["id"] = secrets.token_hex(4)
        d["added"] = time.time()
        _devices[d["id"]] = d
        save()
        await audit("devices.add", f"{d['kind']} '{d['name']}' at {d.get('host') or urlsplit(d.get('url', '')).hostname}")
        await refresh(d["id"])
        return {"ok": True, "device": public(d)}

    @router.post("/api/devices/hue/pair")
    async def pair_hue(request: Request):
        if not commander():
            return _locked()
        if not host_only(request):
            return _hostonly()
        b = await _body(request)
        try:
            res = await hue_pair(str(b.get("host", "")))
        except Refused as e:
            return _bad(str(e), 403)
        except ValueError as e:
            return _bad(str(e))
        except (httpx.HTTPError, OSError):
            return _bad("bridge unreachable", 502)
        if not res["ok"]:
            return res
        did = str(b.get("id") or "")
        if did in _devices and _devices[did]["kind"] == "hue":
            _devices[did]["token"] = res["token"]
            d = _devices[did]
        else:
            if len(_devices) >= MAX_DEVICES:
                return _bad("device limit reached")
            d = {"id": secrets.token_hex(4), "kind": "hue", "name": str(b.get("name") or "Hue bridge")[:60],
                 "host": devices_net.clean_host(str(b["host"])), "token": res["token"], "added": time.time()}
            _devices[d["id"]] = d
        save()
        await audit("devices.pair", f"Hue bridge '{d['name']}' at {d['host']}")
        await refresh(d["id"])
        return {"ok": True, "device": public(d)}

    @router.post("/api/devices/discover")
    async def discover():
        if not commander():
            return _locked()
        return await run_discovery()

    @router.get("/api/devices/connectivity")
    async def connectivity(refresh: int = 0):
        return await connectivity_snapshot(ctx.get("state"), force=bool(refresh))

    @router.get("/api/devices/{did}/status")
    async def device_status(did: str):
        if did not in _devices:
            return _bad("unknown device", 404)
        st = _status.get(did) or {}
        if time.time() - st.get("checked", 0) > 5:
            st = await refresh(did)
        return {"ok": True, "device": public(_devices[did])}

    @router.get("/api/devices/{did}/entities")
    async def device_entities(did: str):
        if did not in _devices:
            return _bad("unknown device", 404)
        try:
            return await entities(_devices[did])
        except Refused as e:
            return _bad(str(e), 403)
        except (httpx.HTTPError, OSError):
            return _bad("device unreachable", 502)

    @router.post("/api/devices/{did}/control")
    async def device_control(did: str, request: Request):
        if not commander():
            return _locked()
        if did not in _devices:
            return _bad("unknown device", 404)
        d = _devices[did]
        b = await _body(request)
        action = str(b.get("action", ""))[:40]
        params = b.get("params") if isinstance(b.get("params"), dict) else {}
        what = params.get("entity_id") or params.get("topic") or params.get("entity") or params.get("light") or params.get("channel", "")
        try:
            res = await control(d, action, params)
        except Refused as e:
            res = {"ok": False, "error": str(e)}
        except (ValueError, TypeError, KeyError) as e:
            await audit("devices.control", f"{d['name']}: {action} {what} refused ({e})")
            return _bad(str(e))
        except (httpx.TimeoutException, asyncio.TimeoutError):
            res = {"ok": False, "error": "device timed out"}
        except (httpx.HTTPError, OSError):
            res = {"ok": False, "error": "device unreachable"}
        await audit("devices.control", f"{d['name']} ({d['kind']}): {action} {what} → {'ok' if res.get('ok') else 'failed'}")
        if d["kind"] != "mqtt" or action != "read":
            await refresh(did)
        return {**res, "device": public(d)}

    @router.post("/api/devices/{did}/remove")
    async def device_remove(did: str, request: Request):
        if not commander():
            return _locked()
        if not host_only(request):
            return _hostonly()
        d = _devices.pop(did, None)
        if not d:
            return _bad("unknown device", 404)
        _status.pop(did, None)
        save()
        await audit("devices.remove", f"{d['kind']} '{d['name']}'")
        return {"ok": True}

    return router


async def start(ctx) -> None:
    """Background: one LAN discovery shortly after boot, then honest status polling."""
    await asyncio.sleep(20)
    try:
        await run_discovery()
    except Exception as e:
        print(f"[devices] discovery failed: {type(e).__name__}")
    n = 0
    while True:
        ids = list(_devices)
        if ids:
            await asyncio.gather(*(asyncio.wait_for(refresh(i), timeout=TIMEOUT * 2) for i in ids),
                                 return_exceptions=True)
        if n % 5 == 0:
            try:
                await connectivity_snapshot(ctx.get("state"))
            except Exception:
                pass
        n += 1
        await asyncio.sleep(60)
