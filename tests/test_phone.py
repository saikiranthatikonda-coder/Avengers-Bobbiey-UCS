"""Phone agent (phone.py): webhook signatures (incl. tunnel URL reconstruction), TwiML,
conversation turns with a fake brain, outbound guardrails, token masking, status callbacks,
summaries + follow-ups. No network: Twilio REST is mocked with httpx.MockTransport and
livefacts.ground is patched."""

import asyncio
import json
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qsl, urlencode

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import phone  # noqa: E402
import phone_providers as pp  # noqa: E402
import phone_tunnel  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SID = "AC" + "0123456789abcdef" * 2
TOKEN = "f" * 8 + "0123456789abcdef" + "e" * 8          # test-only value, 32 hex chars
CALL = "CA" + "a" * 32
NUM_SID = "PN" + "b" * 32
PUBLIC = "https://quiet-river-demo.trycloudflare.com"


class FakeBrain:
    mode = "llm"
    provider = None

    def __init__(self, replies=None, delay=0.0):
        self.replies = list(replies or [])
        self.delay = delay
        self.calls = []

    async def think(self, prompt, system=None, agent="jarvis", timeout=120.0, fast=False):
        self.calls.append({"prompt": prompt, "system": system})
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.replies.pop(0) if self.replies else "Sure, I can help with that."


class FakeTwilio:
    def __init__(self):
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode() if request.content else ""
        self.requests.append((request.method, request.url.path, parse_qsl(body, keep_blank_values=True),
                              request.headers.get("authorization", "")))
        p = request.url.path
        if p.endswith(f"/Accounts/{SID}.json"):
            return httpx.Response(200, json={"friendly_name": "Owner account", "status": "active", "type": "Full"})
        if p.endswith("/IncomingPhoneNumbers.json"):
            return httpx.Response(200, json={"incoming_phone_numbers": [
                {"sid": NUM_SID, "phone_number": "+15550001111", "friendly_name": "Main", "capabilities": {"voice": True},
                 "voice_url": "", "status_callback": ""}]})
        if p.endswith(f"/IncomingPhoneNumbers/{NUM_SID}.json"):
            d = dict(parse_qsl(body))
            return httpx.Response(200, json={"sid": NUM_SID, "phone_number": "+15550001111",
                                             "voice_url": d.get("VoiceUrl"), "status_callback": d.get("StatusCallback")})
        if p.endswith("/Calls.json"):
            return httpx.Response(201, json={"sid": CALL, "status": "queued"})
        if p.endswith(f"/Calls/{CALL}.json") and request.method == "POST":
            return httpx.Response(200, json={"sid": CALL, "status": "completed"})
        if p.endswith(f"/Calls/{CALL}.json"):
            return httpx.Response(200, json={"status": "completed", "duration": "42", "price": "-0.0130", "price_unit": "USD"})
        return httpx.Response(404, json={"message": "not found", "code": 20404})


def sign(url, params, token=TOKEN):
    return pp.twilio_signature(token, url, list(params.items()))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="phone-test-"))
        self.tw = FakeTwilio()
        self.brain = FakeBrain()
        self.commander, self.host = True, True
        self.audits = []

        async def audit(action, detail=""):
            self.audits.append((action, detail))
        self.state = {"brain": self.brain}
        self.ctx = {"state": self.state, "audit": audit, "commander": lambda: self.commander,
                    "host_only": lambda request: self.host, "phone_dir": self.tmp,
                    "phone_transport": httpx.MockTransport(self.tw.handler)}
        app = FastAPI()
        app.include_router(phone.build_router(self.ctx))
        self.agent = self.state["phone_agent"]
        self.agent.price_delay = 0
        self.patch = mock.patch("livefacts.ground", mock.AsyncMock(return_value="[CLOCK test]\n"))
        self.patch.start()
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.patch.stop()
        self.agent.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def configure(self, **extra):
        self.agent.cfg.update(account_sid=SID, auth_token=TOKEN, public_base_url=PUBLIC, public_url_source="manual",
                              selected_number="+15550001111", selected_number_id=NUM_SID, **extra)

    def hook(self, kind, params, query="", sig=None, forwarded=True, url=None):
        path = f"/phone/twilio/{kind}" + (f"?{query}" if query else "")
        url = url or PUBLIC + path
        headers = {"content-type": "application/x-www-form-urlencoded",
                   "x-twilio-signature": sig if sig is not None else sign(url, params)}
        if forwarded:
            headers.update({"x-forwarded-proto": "https", "x-forwarded-host": PUBLIC.split("//")[1]})
        return self.client.post(path, content=urlencode(params), headers=headers)

    def inbound(self, frm="+919876543210"):
        return self.hook("voice", {"CallSid": CALL, "From": frm, "To": "+15550001111", "Direction": "inbound",
                                   "CallStatus": "ringing"})


class SignatureTests(Base):
    def test_known_vector(self):
        # Twilio's documented example (docs: "Validating requests"), token "12345"
        url = "https://mycompany.com/myapp.php?foo=1&bar=2"
        params = {"CallSid": "CA1234567890ABCDE", "Caller": "+12349013030", "Digits": "1234",
                  "From": "+12349013030", "To": "+18005551212"}
        self.assertEqual(pp.twilio_signature("12345", url, list(params.items())), "0/KCTR6DLpKmkAf8muzZqo1nDgQ=")

    def test_accepts_signed_tunnel_request(self):
        self.configure()
        r = self.inbound()
        self.assertEqual(r.status_code, 200)
        self.assertIn("<Gather", r.text)
        self.assertEqual(self.agent.hooks["accepted"], 1)

    def test_rejects_bad_missing_and_wrong_token(self):
        self.configure()
        p = {"CallSid": CALL, "From": "+919876543210", "To": "+15550001111"}
        self.assertEqual(self.hook("voice", p, sig="AAAA").status_code, 403)
        self.assertEqual(self.hook("voice", p, sig="").status_code, 403)
        bad = pp.twilio_signature("0" * 32, PUBLIC + "/phone/twilio/voice", list(p.items()))
        self.assertEqual(self.hook("voice", p, sig=bad).status_code, 403)
        # tampered param after signing
        s = sign(PUBLIC + "/phone/twilio/voice", p)
        r = self.client.post("/phone/twilio/voice", content=urlencode({**p, "From": "+10000000000"}),
                             headers={"content-type": "application/x-www-form-urlencoded", "x-twilio-signature": s,
                                      "x-forwarded-proto": "https", "x-forwarded-host": PUBLIC.split("//")[1]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.agent.hooks["rejected"], 4)
        self.assertEqual(self.agent.store.calls(), [])

    def test_rejects_when_not_configured(self):
        p = {"CallSid": CALL}
        self.assertEqual(self.hook("voice", p).status_code, 403)
        self.assertEqual(self.hook("status", p).status_code, 403)

    def test_signature_over_internal_url_is_rejected(self):
        # signed for the loopback URL uvicorn sees, not the public URL Twilio called
        self.configure()
        p = {"CallSid": CALL}
        s = sign("http://testserver/phone/twilio/voice", p)
        self.assertEqual(self.hook("voice", p, sig=s).status_code, 403)

    def test_configured_base_fallback_and_query(self):
        # no forwarding headers: the configured public URL (plus the exact query string) is used
        self.configure()
        p = {"CallSid": CALL, "From": "+919876543210", "To": "+15550001111"}
        r = self.hook("voice", p, query="c=99", forwarded=False)
        self.assertEqual(r.status_code, 200)

    def test_candidates_and_port_variants(self):
        c = pp.public_url_candidates({"Host": "127.0.0.1:8765", "X-Forwarded-Proto": "https",
                                      "X-Forwarded-Host": "a-b.trycloudflare.com"}, "http", "/phone/twilio/gather",
                                     "c=3", "https://x.example.com/")
        self.assertEqual(c[0], "https://a-b.trycloudflare.com/phone/twilio/gather?c=3")
        self.assertIn("https://a-b.trycloudflare.com:443/phone/twilio/gather?c=3", c)
        self.assertIn("https://x.example.com/phone/twilio/gather?c=3", c)
        # a garbage host header is ignored
        c2 = pp.public_url_candidates({"host": "evil.com/<x>"}, "http", "/p", "", None)
        self.assertEqual(c2, [])

    def test_every_webhook_is_guarded(self):
        self.configure()
        for kind in ("voice", "gather", "wait", "dial", "status"):
            r = self.client.post(f"/phone/twilio/{kind}", content="CallSid=" + CALL,
                                 headers={"content-type": "application/x-www-form-urlencoded"})
            self.assertEqual(r.status_code, 403, kind)


class TwimlTests(Base):
    def test_inbound_greeting_disclosure_gather(self):
        self.configure(owner_name="Sai", assistant_name="Jarvis")
        x = self.inbound().text
        self.assertTrue(x.startswith('<?xml version="1.0" encoding="UTF-8"?><Response>'))
        self.assertIn("I am Jarvis, an AI assistant", x)
        self.assertIn("reached Sai", x)
        self.assertIn('input="speech"', x)
        self.assertIn('speechTimeout="auto"', x)
        self.assertIn('bargeIn="true"', x)
        self.assertIn('language="en-IN"', x)
        self.assertIn('voice="Polly.Raveena"', x)
        self.assertIn("/phone/twilio/gather?c=1", x)
        self.assertNotIn("recorded", x)

    def test_recording_announced_and_started(self):
        self.configure(recording=True)
        x = self.inbound().text
        self.assertIn("This call is being recorded.", x)
        time.sleep(0.3)
        self.assertTrue(any(p.endswith(f"/Calls/{CALL}/Recordings.json") for _, p, _, _ in self.tw.requests))

    def test_disclosure_can_be_disabled(self):
        self.configure(disclosure_enabled=False)
        self.assertNotIn("AI assistant", self.inbound().text)

    def test_goodbye_and_escaping(self):
        x = pp.twiml_goodbye("Bye <now> & \"later\"", "Polly.Raveena", "en-IN")
        self.assertIn("Bye &lt;now&gt; &amp;", x)
        self.assertTrue(x.endswith("<Hangup/></Response>"))
        g = pp.twiml_gather("hi", "/phone/twilio/gather?c=1&x=2", "alice", "en-US")
        self.assertIn('action="/phone/twilio/gather?c=1&amp;x=2"', g)

    def test_blocked_caller_rejected(self):
        self.configure()
        self.agent.store.upsert_contact({"name": "Spam", "number": "+919876543210", "blocked": True})
        self.assertIn("<Reject", self.inbound().text)


class ConversationTests(Base):
    def gather(self, speech, conf="0.91"):
        return self.hook("gather", {"CallSid": CALL, "SpeechResult": speech, "Confidence": conf}, query="c=1")

    def test_turn_uses_brain_with_context(self):
        self.configure()
        self.agent.store.upsert_contact({"name": "Ravi", "number": "+919876543210", "notes": "prefers mornings"})
        self.inbound()
        self.brain.replies = ["Sure, I'll tell him you called about the invoice."]
        x = self.gather("Hi, I'm calling about the invoice").text
        self.assertIn("<Say", x)
        self.assertIn("about the invoice.", x)
        self.assertIn("<Gather", x)
        prompt, system = self.brain.calls[0]["prompt"], self.brain.calls[0]["system"]
        self.assertIn("[CLOCK test]", prompt)
        self.assertIn("Ravi", prompt)
        self.assertIn("prefers mornings", prompt)
        self.assertIn("Them: Hi, I'm calling about the invoice", prompt)
        self.assertIn("Never claim to be human", system)
        turns = self.agent.store.turns(1)
        self.assertEqual([t["who"] for t in turns], ["agent", "caller", "agent"])
        self.assertAlmostEqual(turns[1]["confidence"], 0.91)
        live = self.client.get("/api/phone/live").json()["active"][0]
        self.assertEqual(len(live["turns"]), 3)

    def test_end_marker_hangs_up(self):
        self.configure()
        self.inbound()
        self.brain.replies = ["Great, have a nice day! [END]"]
        x = self.gather("ok that's everything for today then").text
        self.assertIn("Great, have a nice day!", x)
        self.assertNotIn("[END]", x)
        self.assertIn("<Hangup/>", x)

    def test_caller_goodbye(self):
        self.configure()
        self.inbound()
        x = self.gather("okay bye").text
        self.assertIn("<Hangup/>", x)
        self.assertEqual(self.brain.calls, [])

    def test_handoff_dials_owner(self):
        self.configure(handoff_enabled=True, owner_number="+919000000001")
        self.inbound()
        x = self.gather("can I speak to a real person please").text
        self.assertIn("<Dial", x)
        self.assertIn("<Number>+919000000001</Number>", x)
        x2 = self.hook("dial", {"CallSid": CALL, "DialCallStatus": "no-answer"}, query="c=1").text
        self.assertIn("take a message", x2)
        self.assertIn("<Gather", x2)

    def test_no_handoff_without_owner_number(self):
        self.configure(handoff_enabled=True, owner_number="")
        self.inbound()
        self.assertNotIn("<Dial", self.gather("transfer me to a real person").text)

    def test_silence_reprompt_then_goodbye(self):
        self.configure()
        self.inbound()
        x1 = self.hook("gather", {"CallSid": CALL, "SpeechResult": ""}, query="c=1").text
        self.assertIn("Are you still there?", x1)
        x2 = self.hook("gather", {"CallSid": CALL}, query="c=1").text
        self.assertIn("<Hangup/>", x2)

    def test_max_turns(self):
        self.configure(max_turns=1)
        self.inbound()
        self.gather("first question about something")
        self.assertIn("<Hangup/>", self.gather("second question about something").text)

    def test_slow_brain_redirects_to_wait(self):
        self.configure(llm_wait_s=1)
        self.inbound()
        self.brain.delay = 1.6
        self.brain.replies = ["Here is the slow answer."]
        x = self.gather("tell me something thoughtful").text
        self.assertIn("<Redirect", x)
        self.assertIn("/phone/twilio/wait?c=1", x)
        x2 = self.hook("wait", {"CallSid": CALL}, query="c=1").text
        self.assertIn("Here is the slow answer.", x2)

    def test_offline_brain_is_honest(self):
        self.configure()
        self.brain.mode = "offline"
        self.inbound()
        self.assertIn("can't answer that right now", self.gather("what is your price").text)


class OutboundTests(Base):
    def contact(self, **kw):
        c = {"name": "Asha", "number": "+919876543210", "consent": True, "consent_source": "verbal, test"}
        c.update(kw)
        return self.agent.store.upsert_contact(c)

    def test_guardrails(self):
        self.configure(window_start="00:00", window_end="23:59")
        noon = datetime(2026, 11, 3, 6, 30, tzinfo=timezone.utc)                 # 12:00 IST
        c = self.contact(consent=False)
        self.assertTrue(any("consent" in e for e in self.agent.outbound_problems(c, noon)))
        c = self.contact(dnc=True)
        self.assertTrue(any("do-not-call" in e for e in self.agent.outbound_problems(c, noon)))
        c = self.contact()
        self.assertEqual(self.agent.outbound_problems(c, noon), [])
        self.agent.cfg.update(window_start="10:00", window_end="19:00")
        late = datetime(2026, 11, 3, 16, 30, tzinfo=timezone.utc)                # 22:00 IST
        self.assertTrue(any("calling hours" in e for e in self.agent.outbound_problems(c, late)))
        # callee in London: 12:00 IST = 06:30 London → outside callee window
        uk = self.contact(number="+447700900123", name="Tom")
        self.assertTrue(any("callee" in e for e in self.agent.outbound_problems(uk, noon)))
        self.agent.cfg["public_base_url"] = ""
        self.assertTrue(any("public https URL" in e for e in self.agent.outbound_problems(c, noon)))

    def test_commander_and_host_required(self):
        self.configure()
        c = self.contact()
        self.commander = False
        r = self.client.post("/api/phone/call/prepare", json={"contact_id": c["id"], "purpose": "confirm meeting"}).json()
        self.assertIn("OBSERVER", r["error"])
        self.commander, self.host = True, False
        r = self.client.post("/api/phone/call/prepare", json={"contact_id": c["id"], "purpose": "confirm meeting"}).json()
        self.assertIn("host machine", r["error"])
        r = self.client.post("/api/phone/call/confirm", json={"token": "x"}).json()
        self.assertIn("host machine", r["error"])
        self.assertEqual(self.tw.requests, [])

    def test_two_step_call_places_real_request(self):
        self.configure()
        c = self.contact()
        with mock.patch("phone.in_window", return_value=True):
            r = self.client.post("/api/phone/call/confirm", json={"token": "forged"}).json()
            self.assertFalse(r["ok"])
            p = self.client.post("/api/phone/call/prepare",
                                 json={"contact_id": c["id"], "purpose": "Confirm Thursday meeting"}).json()
            self.assertTrue(p["ok"], p)
            self.assertEqual(self.tw.requests, [])                               # nothing dialled before confirm
            r = self.client.post("/api/phone/call/confirm", json={"token": p["confirm_token"]}).json()
            self.assertTrue(r["ok"], r)
            again = self.client.post("/api/phone/call/confirm", json={"token": p["confirm_token"]}).json()
            self.assertFalse(again["ok"])                                         # single use
        method, path, data, auth = self.tw.requests[-1]
        self.assertTrue(path.endswith("/Calls.json"))
        d = dict(data)
        self.assertEqual(d["To"], "+919876543210")
        self.assertEqual(d["From"], "+15550001111")
        self.assertEqual(d["Url"], f"{PUBLIC}/phone/twilio/voice?c={r['call_id']}")
        self.assertEqual([v for k, v in data if k == "StatusCallbackEvent"], ["initiated", "ringing", "answered", "completed"])
        self.assertTrue(auth.startswith("Basic "))
        self.assertEqual(self.agent.store.call(r["call_id"])["sid"], CALL)
        # the answered call greets with the outbound disclosure
        x = self.hook("voice", {"CallSid": CALL, "From": "+15550001111", "To": "+919876543210",
                                "Direction": "outbound-api"}, query=f"c={r['call_id']}").text
        self.assertIn("calling on behalf of", x)
        # rate limit: per-contact gap
        with mock.patch("phone.in_window", return_value=True):
            self.agent.live.clear()
            self.assertTrue(any("already called" in e for e in self.agent.outbound_problems(self.agent.store.contact(c["id"]))))
        self.assertTrue(any(a[0] == "phone.call.outbound" for a in self.audits))

    def test_hourly_rate_limit(self):
        self.configure(outbound_per_hour=1, per_contact_gap_h=0)
        self.agent.store.log_outbound(999, "+911111111111")
        with mock.patch("phone.in_window", return_value=True):
            self.assertTrue(any("per hour" in e for e in self.agent.outbound_problems(self.contact())))

    def test_hangup(self):
        self.configure()
        self.inbound()
        r = self.client.post("/api/phone/hangup", json={"id": 1}).json()
        self.assertTrue(r["ok"], r)
        m, p, data, _ = self.tw.requests[-1]
        self.assertTrue(p.endswith(f"/Calls/{CALL}.json"))
        self.assertEqual(dict(data), {"Status": "completed"})
        self.commander = False
        self.assertIn("OBSERVER", self.client.post("/api/phone/hangup", json={"id": 1}).json()["error"])


class ConfigTests(Base):
    def test_token_masked_everywhere(self):
        r = self.client.post("/api/phone/config", json={"account_sid": SID, "auth_token": TOKEN}).json()
        self.assertTrue(r["ok"], r)
        blob = json.dumps(r) + json.dumps(self.client.get("/api/phone").json())
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn(SID, blob)
        self.assertIn(TOKEN[-4:], r["config"]["auth_token"])
        self.assertNotIn(TOKEN, json.dumps(self.audits))
        self.assertNotIn(TOKEN, repr(self.agent.provider))
        self.assertEqual(json.loads((self.tmp / "phone.json").read_text())["auth_token"], TOKEN)
        # echoing the masked value back keeps the real token
        self.client.post("/api/phone/config", json={"auth_token": r["config"]["auth_token"]})
        self.assertEqual(self.agent.cfg["auth_token"], TOKEN)
        self.assertEqual(pp.mask(TOKEN), "••••••••" + TOKEN[-4:])

    def test_config_requires_commander_and_host_and_validates(self):
        self.commander = False
        self.assertFalse(self.client.post("/api/phone/config", json={"owner_name": "X"}).json()["ok"])
        self.commander, self.host = True, False
        self.assertIn("host", self.client.post("/api/phone/config", json={"owner_name": "X"}).json()["error"])
        self.host = True
        r = self.client.post("/api/phone/config", json={"owner_name": "Sai", "public_base_url": "http://insecure.example"}).json()
        self.assertFalse(r["ok"])
        self.assertEqual(self.agent.cfg["owner_name"], "the owner")                # all-or-nothing
        r = self.client.post("/api/phone/config", json={"voice": "Polly.Aditi\"/><Hangup/>"}).json()
        self.assertFalse(r["ok"])

    def test_honest_not_configured_state(self):
        s = self.client.get("/api/phone").json()
        self.assertFalse(s["setup"]["ready"])
        self.assertIn("Not configured", s["setup"]["next"])
        self.assertEqual(s["active"], [])
        self.assertEqual(s["calls"], [])
        self.assertIsNone(s["analytics"]["answer_rate"])

    def test_verify_and_point_number(self):
        self.client.post("/api/phone/config", json={"account_sid": SID, "auth_token": TOKEN,
                                                    "public_base_url": PUBLIC})
        v = self.client.post("/api/phone/verify").json()
        self.assertTrue(v["ok"], v)
        self.assertEqual(self.agent.cfg["selected_number"], "+15550001111")          # the only voice number
        r = self.client.post("/api/phone/numbers/point").json()
        self.assertTrue(r["ok"], r)
        d = dict(self.tw.requests[-1][2])
        self.assertEqual(d["VoiceUrl"], PUBLIC + "/phone/twilio/voice")
        self.assertEqual(d["StatusCallback"], PUBLIC + "/phone/twilio/status")
        self.assertTrue(r["setup"]["steps"][4]["ok"])

    def test_bad_credentials_reported(self):
        self.configure()
        self.tw.handler = lambda req: httpx.Response(401, json={"message": "Authenticate", "code": 20003})
        self.agent.transport = httpx.MockTransport(lambda req: self.tw.handler(req))
        v = self.client.post("/api/phone/verify").json()
        self.assertFalse(v["ok"])
        self.assertIn("20003", v["error"])
        self.assertNotIn(TOKEN, json.dumps(v))

    def test_contact_consent_requires_source(self):
        r = self.client.post("/api/phone/contacts", json={"name": "A", "number": "+919876543210", "consent": True}).json()
        self.assertFalse(r["ok"])
        r = self.client.post("/api/phone/contacts", json={"name": "A", "number": "+91 98765-43210", "consent": True,
                                                          "consent_source": "signed form"}).json()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["contact"]["number"], "+919876543210")
        self.assertIsNotNone(r["contact"]["consent_ts"])
        self.assertFalse(self.client.post("/api/phone/contacts", json={"name": "B", "number": "98765"}).json()["ok"])


class StatusAndSummaryTests(Base):
    def test_status_timeline_and_summary(self):
        self.configure()
        self.inbound()
        self.brain.replies = ["Got it, I'll pass that on."]
        self.hook("gather", {"CallSid": CALL, "SpeechResult": "Please ask Sai to send the contract by Friday"}, query="c=1")
        self.hook("status", {"CallSid": CALL, "CallStatus": "in-progress", "SequenceNumber": "1"})
        self.brain.replies = ['{"summary": "Caller asked for the contract.", '
                              '"followups": [{"action": "Send the contract", "due": "2026-11-06"}]}']
        r = self.hook("status", {"CallSid": CALL, "CallStatus": "completed", "CallDuration": "42"})
        self.assertEqual(r.status_code, 204)
        # late out-of-order callback must not revert the status
        self.hook("status", {"CallSid": CALL, "CallStatus": "ringing"})
        for _ in range(40):
            c = self.agent.store.call(1)
            if c["summary"] and c["price"]:
                break
            time.sleep(0.05)
        c = self.agent.store.call(1)
        self.assertEqual(c["status"], "completed")
        self.assertEqual(c["duration"], 42)
        self.assertEqual(c["summary"], "Caller asked for the contract.")
        self.assertEqual(c["price"], "-0.0130")
        fu = self.agent.store.followups(status="open")
        self.assertEqual([(f["text"], f["due"]) for f in fu], [("Send the contract", "2026-11-06")])
        self.assertEqual([e["status"] for e in self.agent.store.events(1)][-3:], ["in-progress", "completed", "ringing"])
        self.assertEqual(self.agent.live, {})
        det = self.client.get("/api/phone/calls/1").json()
        self.assertEqual(len(det["turns"]), 3)
        a = self.client.get("/api/phone/analytics").json()
        self.assertEqual(a["total"], 1)
        self.assertEqual(a["avg_duration_s"], 42)
        self.assertEqual(a["turns_per_call"], 3)
        self.assertTrue(self.client.post("/api/phone/followups", json={"id": fu[0]["id"]}).json()["ok"])
        self.assertEqual(self.agent.store.followups(status="open"), [])

    def test_summary_without_brain_is_not_faked(self):
        self.configure()
        self.inbound()
        self.hook("gather", {"CallSid": CALL, "SpeechResult": "hello there"}, query="c=1")
        self.brain.mode = "offline"
        res = asyncio.run(self.agent.summarize(1))
        self.assertFalse(res["ok"])
        self.assertIsNone(self.agent.store.call(1)["summary"])
        self.assertEqual(self.agent.store.call(1)["summary_engine"], "unavailable")

    def test_parse_summary(self):
        d = phone.parse_summary('Sure!\n{"summary": "x", "followups": ["call back", {"action": "email", "due": ""}, {"bad": 1}]}')
        self.assertEqual(d["followups"], [{"action": "call back", "due": ""}, {"action": "email", "due": ""}])
        self.assertIsNone(phone.parse_summary("no json here"))
        self.assertIsNone(phone.parse_summary('{"followups": []}'))


class MiscTests(unittest.TestCase):
    def test_tunnel_url_parse(self):
        line = "2026-11-03T10:00:00Z INF |  https://brave-lion-sample-words.trycloudflare.com                  |"
        self.assertEqual(phone_tunnel.parse_url(line), "https://brave-lion-sample-words.trycloudflare.com")
        self.assertIsNone(phone_tunnel.parse_url("requesting https://api.trycloudflare.com/tunnel"))

    def test_window_and_tz(self):
        self.assertEqual(phone.tz_for_number("+919876543210"), "Asia/Kolkata")
        self.assertIsNone(phone.tz_for_number("+15550001111"))
        t = datetime(2026, 11, 3, 4, 0, tzinfo=timezone.utc)                    # 09:30 IST
        self.assertFalse(phone.in_window(t, "Asia/Kolkata", "10:00", "19:00"))
        self.assertTrue(phone.in_window(t, "Asia/Kolkata", "09:00", "19:00"))
        self.assertTrue(phone.in_window(t, "Asia/Kolkata", "22:00", "10:00"))     # overnight window

    def test_stub_providers_refuse(self):
        for name in ("vonage", "plivo", "exotel"):
            p = pp.PROVIDERS[name]()
            self.assertFalse(p.implemented)
            self.assertFalse(p.verify(["https://x"], [], {"X-Twilio-Signature": "x"}))
            with self.assertRaises(pp.NotImplementedProvider):
                asyncio.run(p.place_call("+1", "+1", "u", "s", False, 60))


# ═══ real server: phone settings, consent + DNC persist across a restart; keys never returned ═══

class PersistHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from harness import IsolatedServer
        cls.srv = IsolatedServer().start(timeout=180)

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def test_settings_contacts_dnc_survive_restart(self):
        s = self.srv
        token = "f" * 32
        r = s.post("/api/phone/config", {"window_start": "09:30", "window_end": "18:15", "owner_tz": "Asia/Kolkata",
                                         "outbound_per_hour": 3, "recording": True,
                                         "account_sid": "AC" + "a" * 32, "auth_token": token})
        self.assertTrue(r["ok"], r)
        self.assertNotIn(token, json.dumps(r))
        bad = s.post("/api/phone/config", {"window_start": "25:99", "outbound_per_hour": 9})
        self.assertFalse(bad["ok"])                                    # all-or-nothing: nothing applied
        c = s.post("/api/phone/contacts", {"name": "Test Person", "number": "+919800000001", "consent": True,
                                           "consent_source": "verbal, test", "dnc": True})
        self.assertTrue(c["ok"], c)
        s.restart()
        snap = s.get("/api/phone")
        cfg = snap["config"]
        self.assertEqual((cfg["window_start"], cfg["window_end"], cfg["outbound_per_hour"], cfg["recording"]),
                         ("09:30", "18:15", 3, True))
        self.assertTrue(cfg["auth_token_set"])
        self.assertNotIn(token, json.dumps(snap))
        ct = next(x for x in s.get("/api/phone/contacts")["contacts"] if x["number"] == "+919800000001")
        self.assertTrue(ct["consent"])
        self.assertTrue(ct["dnc"])
        # DNC still blocks an outbound call after the restart
        p = s.post("/api/phone/call/prepare", {"contact_id": ct["id"], "purpose": "confirm the meeting time"})
        self.assertFalse(p["ok"])
        self.assertTrue(any("do-not-call" in x for x in p["problems"]), p)
        # observer can't change settings
        s.post("/api/role", {"role": "observer"})
        try:
            self.assertIn("OBSERVER", s.post("/api/phone/config", {"window_start": "08:00"})["error"])
            self.assertEqual(s.get("/api/phone")["config"]["window_start"], "09:30")
        finally:
            s.post("/api/role", {"role": "commander"})


if __name__ == "__main__":
    unittest.main()
