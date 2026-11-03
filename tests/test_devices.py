"""DEVICES & CONNECTIVITY: adapter commands, LAN allowlist, secret masking, commander
lock, discovery + connectivity parsers. No network or hardware: HTTP goes through an
httpx.MockTransport, DNS and subprocesses are patched."""

import asyncio
import json
import socket
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import devices  # noqa: E402
import devices_link  # noqa: E402
import devices_net  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

TOKEN = "eyJhbGciOiJIUzI1NiJ9.test-long-lived-token-ABCD1234"


def run(coro):
    return asyncio.run(coro)


class Allowlist(unittest.TestCase):
    def test_private_and_loopback_allowed(self):
        for ip in ("192.168.1.20", "10.0.0.5", "172.16.3.4", "127.0.0.1", "::1", "fe80::1", "100.101.102.103", "fd12::1"):
            self.assertTrue(devices_net.is_lan_ip(ip), ip)

    def test_public_and_special_refused(self):
        for ip in ("8.8.8.8", "1.1.1.1", "142.250.183.14", "2606:4700::1111", "169.254.169.254",
                   "0.0.0.0", "224.0.0.251", "255.255.255.255", "::ffff:8.8.8.8", "garbage"):
            self.assertFalse(devices_net.is_lan_ip(ip), ip)

    def test_vet_host_refuses_names_resolving_public(self):
        with mock.patch.object(devices_net, "resolve", return_value=["93.184.216.34"]):
            with self.assertRaises(ValueError):
                devices_net.vet_host("evil.example.com")
        with mock.patch.object(devices_net, "resolve", return_value=["192.168.1.9", "8.8.8.8"]):
            with self.assertRaises(ValueError):          # any public answer = refused (rebinding)
                devices_net.vet_host("mixed.example.com")
        with mock.patch.object(devices_net, "resolve", return_value=["192.168.1.9"]):
            self.assertEqual(devices_net.vet_host("http://shelly.local/x"), "192.168.1.9")

    def test_clean_host_rejects_junk(self):
        for bad in ("", "a b", "x;rm", "http://"):
            with self.assertRaises(ValueError):
                devices_net.clean_host(bad)

    def test_target_pins_ip_and_ha_https_exception(self):
        with mock.patch.object(devices_net, "resolve", return_value=["192.168.1.30"]):
            base, hdr, _ = run(devices.target({"kind": "shelly", "host": "shelly1.local"}))
            self.assertEqual(base, "http://192.168.1.30")
            base, hdr, _ = run(devices.target({"kind": "homeassistant", "url": "http://ha.local:8123"}))
            self.assertEqual((base, hdr["Host"]), ("http://192.168.1.30:8123", "ha.local:8123"))
        with mock.patch.object(devices_net, "resolve", return_value=["93.184.216.34"]):
            base, _, verify = run(devices.target({"kind": "homeassistant", "url": "https://ha.example.com", "verify_tls": False}))
            self.assertEqual(base, "https://ha.example.com")
            self.assertTrue(verify)                         # public HA always verifies TLS
            for d in ({"kind": "homeassistant", "url": "http://ha.example.com"},
                      {"kind": "http", "url": "https://hooks.example.com"},
                      {"kind": "tasmota", "host": "x.example.com"}):
                with self.assertRaises(devices.Refused):
                    run(devices.target(d))


class Commands(unittest.TestCase):
    def test_home_assistant(self):
        m, p, body = devices.ha_service_call("light.kitchen", "turn_on", {"brightness_pct": 150, "evil": 1})
        self.assertEqual((m, p), ("POST", "/api/services/light/turn_on"))
        self.assertEqual(body, {"entity_id": "light.kitchen", "brightness_pct": 100})
        for eid, svc in (("homeassistant.x", "restart"), ("shell_command.rm", "turn_on"),
                         ("light.kitchen", "reload"), ("light/../x", "turn_on")):
            with self.assertRaises(ValueError):
                devices.ha_service_call(eid, svc)

    def test_shelly(self):
        self.assertEqual(devices.shelly_cmd(1, "on", 0), ("/relay/0", {"turn": "on"}))
        self.assertEqual(devices.shelly_cmd(2, "off", 1), ("/rpc/Switch.Set", {"id": 1, "on": "false"}))
        self.assertEqual(devices.shelly_cmd(3, "toggle"), ("/rpc/Switch.Toggle", {"id": 0}))
        with self.assertRaises(ValueError):
            devices.shelly_cmd(1, "explode")

    def test_tasmota(self):
        self.assertEqual(devices.tasmota_cmd("on", 1), ("/cm", {"cmnd": "Power1 On"}))
        self.assertEqual(devices.tasmota_cmd("toggle", 2)[1]["cmnd"], "Power2 Toggle")
        self.assertEqual(devices.tasmota_cmd("dimmer", 1, 140)[1]["cmnd"], "Dimmer 100")
        p = devices.tasmota_cmd("off", 1, password="pw")[1]
        self.assertEqual((p["user"], p["password"], p["cmnd"]), ("admin", "pw", "Power1 Off"))
        with self.assertRaises(ValueError):
            devices.tasmota_cmd("Backlog", 1)

    def test_hue(self):
        key = "a" * 40
        self.assertEqual(devices.hue_cmd(key, "3", "on"), ("PUT", f"/api/{key}/lights/3/state", {"on": True}))
        self.assertEqual(devices.hue_cmd(key, "3", "toggle", current_on=True)[2], {"on": False})
        self.assertEqual(devices.hue_cmd(key, "3", "brightness", 999)[2], {"on": True, "bri": 254})
        with self.assertRaises(ValueError):
            devices.hue_cmd("", "3", "on")
        with self.assertRaises(ValueError):
            devices.hue_cmd(key, "../config", "on")

    def test_esphome_wled_paths(self):
        self.assertEqual(devices.esphome_cmd("switch/relay", "on"), ("POST", "/switch/relay/turn_on", {}))
        self.assertEqual(devices.esphome_cmd("light/strip", "brightness", 300)[2], {"brightness": 255})
        with self.assertRaises(ValueError):
            devices.esphome_cmd("../admin", "on")
        self.assertEqual(devices.wled_cmd("toggle"), {"on": "t"})
        for bad in ("http://x/on", "../on", "on"):
            with self.assertRaises(ValueError):
                devices._rel_path(bad)

    def test_mqtt_packets(self):
        c = devices.mqtt_connect_packet("bucs-1", "u", "p")
        self.assertEqual(c[0], 0x10)
        self.assertIn(b"\x00\x04MQTT\x04\xc2", c)
        pub = devices.mqtt_publish_packet("home/lamp", "ON", retain=True)
        self.assertEqual(pub, b"\x31" + bytes([2 + 9 + 2]) + b"\x00\x09home/lampON")
        self.assertEqual(devices._varint(321), b"\xc1\x02")
        with self.assertRaises(ValueError):
            devices.check_topic("home/#")
        self.assertEqual(devices.check_topic("home/#", wildcards=True), "home/#")


class Api(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p_store = mock.patch.object(devices, "STORE", Path(self.tmp.name) / ".update" / "devices.json")
        self.p_store.start()
        devices._devices.clear(); devices._status.clear()
        self.role = {"commander": True, "host": True}
        self.audits = []
        self.requests = []

        async def audit(action, detail=""):
            self.audits.append((action, detail))

        def handler(req: httpx.Request):
            self.requests.append(req)
            if req.url.path == "/api/":
                return httpx.Response(200, json={"message": "API running."})
            if req.url.path.startswith("/api/services/"):
                return httpx.Response(200, json=[])
            return httpx.Response(404)

        self.p_tr = mock.patch.object(devices, "_TRANSPORT", httpx.MockTransport(handler))
        self.p_tr.start()
        self.p_dns = mock.patch.object(devices_net, "resolve", return_value=["192.168.1.40"])
        self.p_dns.start()
        app = FastAPI()
        app.include_router(devices.build_router({
            "state": {}, "audit": audit, "commander": lambda: self.role["commander"],
            "host_only": lambda request: self.role["host"]}))
        self.c = TestClient(app)

    def tearDown(self):
        mock.patch.stopall()
        devices._devices.clear(); devices._status.clear()
        self.tmp.cleanup()

    def _add_ha(self):
        r = self.c.post("/api/devices", json={"kind": "homeassistant", "name": "HA", "url": "http://ha.local:8123", "token": TOKEN})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["device"]

    def test_add_masks_token_everywhere(self):
        dev = self._add_ha()
        self.assertTrue(dev["status"]["online"])
        for body in (json.dumps(dev), self.c.get("/api/devices").text, self.c.get(f"/api/devices/{dev['id']}/status").text):
            self.assertNotIn(TOKEN, body)
            self.assertNotIn(TOKEN[:-4], body)
        self.assertEqual(dev["token"], "••••" + TOKEN[-4:])
        self.assertIn(TOKEN, devices.STORE.read_text(encoding="utf-8"))      # kept server-side only
        self.assertTrue(any(r.headers.get("authorization") == f"Bearer {TOKEN}" for r in self.requests))
        self.assertEqual(self.audits[0][0], "devices.add")
        self.assertNotIn(TOKEN, json.dumps(self.audits))

    def test_add_refuses_public_address(self):
        with mock.patch.object(devices_net, "resolve", return_value=["8.8.8.8"]):
            r = self.c.post("/api/devices", json={"kind": "shelly", "host": "dns.google"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(devices._devices, {})

    def test_commander_and_host_locks(self):
        dev = self._add_ha()
        self.role["commander"] = False
        for path, body in (("/api/devices", {"kind": "wled", "host": "192.168.1.5"}),
                           (f"/api/devices/{dev['id']}/control", {"action": "turn_on", "params": {"entity_id": "light.a"}}),
                           (f"/api/devices/{dev['id']}/remove", {}), ("/api/devices/discover", {}),
                           ("/api/devices/hue/pair", {"host": "192.168.1.2"})):
            self.assertEqual(self.c.post(path, json=body).status_code, 403, path)
        self.role.update(commander=True, host=False)              # remote commander
        self.assertEqual(self.c.post("/api/devices", json={"kind": "wled", "host": "192.168.1.5"}).status_code, 403)
        self.assertEqual(self.c.post(f"/api/devices/{dev['id']}/remove").status_code, 403)
        n = len(self.requests)
        r = self.c.post(f"/api/devices/{dev['id']}/control",
                        json={"action": "turn_on", "params": {"entity_id": "light.kitchen", "data": {"brightness_pct": 40}}})
        self.assertTrue(r.json()["ok"], r.text)
        sent = [q for q in self.requests[n:] if q.url.path == "/api/services/light/turn_on"]
        self.assertEqual(json.loads(sent[0].content), {"entity_id": "light.kitchen", "brightness_pct": 40.0})
        self.assertEqual(self.audits[-1][0], "devices.control")

    def test_control_refused_service_is_audited(self):
        dev = self._add_ha()
        r = self.c.post(f"/api/devices/{dev['id']}/control",
                        json={"action": "restart", "params": {"entity_id": "homeassistant.core"}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("refused", self.audits[-1][1])

    def test_read_only_adapters_cannot_be_controlled(self):
        r = self.c.post("/api/devices", json={"kind": "cast", "host": "192.168.1.70", "name": "TV"})
        did = r.json()["device"]["id"]
        self.assertEqual(self.c.post(f"/api/devices/{did}/control", json={"action": "on"}).status_code, 400)

    def test_offline_is_honest(self):
        def boom(req):
            raise httpx.ConnectError("refused")
        with mock.patch.object(devices, "_TRANSPORT", httpx.MockTransport(boom)):
            r = self.c.post("/api/devices", json={"kind": "wled", "host": "192.168.1.80"})
        st = r.json()["device"]["status"]
        self.assertFalse(st["online"])
        self.assertIn("offline", st["detail"])


class Discovery(unittest.TestCase):
    @staticmethod
    def _rr(name, rtype, rdata):
        return devices_net._enc_name(name) + struct.pack("!HHIH", rtype, 1, 120, len(rdata)) + rdata

    def test_mdns_reply_parsed(self):
        inst = "Living Room._googlecast._tcp.local"
        txt = b"".join(bytes([len(s)]) + s for s in (b"fn=Living Room TV", b"md=Chromecast"))
        recs = [self._rr("_googlecast._tcp.local", 12, devices_net._enc_name(inst)),
                self._rr(inst, 33, struct.pack("!HHH", 0, 0, 8009) + devices_net._enc_name("cc-1.local")),
                self._rr(inst, 16, txt),
                self._rr("cc-1.local", 1, socket.inet_aton("192.168.1.77"))]
        pkt = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 3) + b"".join(recs)
        out = devices_net.collate_mdns([(pkt, "192.168.1.77")])
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["kind"], out[0]["host"], out[0]["port"], out[0]["name"], out[0]["model"]),
                         ("cast", "192.168.1.77", 8009, "Living Room TV", "Chromecast"))
        # a reply from a public source address is dropped
        self.assertEqual(devices_net.collate_mdns([(pkt.replace(socket.inet_aton("192.168.1.77"), socket.inet_aton("8.8.8.8")), "8.8.8.8")]), [])

    def test_query_and_compression_loop_guard(self):
        q = devices_net.build_mdns_query(["_hue._tcp.local"])
        self.assertIn(b"\x04_hue\x04_tcp\x05local\x00\x00\x0c\x00\x01", q)
        with self.assertRaises(ValueError):
            devices_net._read_name(b"\xc0\x00", 0)

    def test_ssdp(self):
        h = devices_net.parse_ssdp(b"HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.2:80/description.xml\r\n"
                                   b"SERVER: Linux/3.14.0 UPnP/1.0 IpBridge/1.60.0\r\nhue-bridgeid: 001788FFFE\r\n\r\n")
        self.assertEqual(devices_net.classify_ssdp(h)[0], "hue")
        self.assertEqual(devices_net.classify_ssdp({"st": "urn:schemas-upnp-org:device:MediaRenderer:1"})[0], "dlna")


class Connectivity(unittest.TestCase):
    TS = json.dumps({"BackendState": "Running", "CurrentTailnet": {"Name": "me@example.com"},
                     "Self": {"HostName": "host", "DNSName": "host.tail1234.ts.net.", "TailscaleIPs": ["100.64.1.2", "fd7a::1"]},
                     "Peer": {"a": {"Online": True}, "b": {"Online": False}}})

    def test_tailscale_parser(self):
        d = devices_link.parse_tailscale(self.TS)
        self.assertEqual((d["state"], d["ips"], d["dns"], d["peers"], d["peers_online"]),
                         ("Running", ["100.64.1.2"], "host.tail1234.ts.net", 2, 1))
        self.assertFalse(devices_link.parse_tailscale("failed to connect")["ok"])

    def test_tailscale_probe_connected_url(self):
        async def fake_run(*a, timeout=6):
            return self.TS
        with mock.patch.object(devices_link, "find_bin", return_value="tailscale"), \
             mock.patch.object(devices_link, "_run", fake_run):
            e = run(devices_link.probe_tailscale())
        self.assertEqual(e["status"], "connected")
        self.assertEqual(e["url"], f"http://host.tail1234.ts.net:{devices_link.PORT}")
        with mock.patch.object(devices_link, "find_bin", return_value=None):
            self.assertEqual(run(devices_link.probe_tailscale())["status"], "not_installed")

    def test_netsh_wifi_parser(self):
        import connectivity
        out = ("    Name                   : Wi-Fi\r\n    State                  : connected\r\n"
               "    SSID                   : HomeNet\r\n    Signal                 : 81%\r\n")
        d = connectivity._parse_netsh(out)
        self.assertTrue(d["connected"]); self.assertEqual(d["ssid"], "HomeNet")
        self.assertFalse(connectivity._parse_netsh("There is no wireless interface on the system.")["connected"])

    def test_other_parsers(self):
        self.assertEqual(devices_link.parse_zerotier("200 info 8056c2e21c 1.14.0 ONLINE\n")["state"], "ONLINE")
        self.assertFalse(devices_link.parse_zerotier("zerotier-cli: missing authentication token")["ok"])
        self.assertFalse(devices_link.parse_mbn("Mobile Broadband Service (wwansvc) is not running.")["present"])
        bt = devices_link.parse_bt_pnp("OK|USB\\VID_8087&PID_0033\\5&1|Intel(R) Wireless Bluetooth(R)\n"
                                       "OK|BTHENUM\\{0000}\\7&2|Headphones\n")
        self.assertEqual((len(bt["adapters"]), bt["peer_entries"]), (1, 1))

    def test_gather_survives_a_failing_probe(self):
        async def ok():
            return devices_link._entry("x", "X", "connected", "", [])

        async def bad():
            raise RuntimeError("boom")
        with mock.patch.object(devices_link, "PROBES", (ok, bad)):
            r = run(devices_link.gather())
        self.assertEqual([m["status"] for m in r["methods"]], ["connected", "unknown"])


if __name__ == "__main__":
    unittest.main()
