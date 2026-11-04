"""PHONE AGENT · telephony providers (pluggable).

A provider does four things for phone.py:
  1. verify that an inbound webhook really came from it (request signature),
  2. talk to its REST API (account check, numbers, point a number at BUCS, place / hang up calls),
  3. render the call-control markup for a conversation turn (TwiML for Twilio),
  4. normalise its webhook fields into the shape phone.py uses.

TwilioProvider is the complete implementation. VonageProvider, PlivoProvider and
ExotelProvider are clearly-labelled stubs: they exist so the interface is fixed and
the config UI can list them, but every call says "not implemented" — nothing is faked.

Secrets: the auth token lives only inside the provider object and the gitignored
.update/phone.json. It is never put in an error message, a log line or a response.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from urllib.parse import urlencode, urlsplit, urlunsplit
from xml.sax.saxutils import escape as _xml_escape, quoteattr

import httpx

E164 = re.compile(r"^\+[1-9]\d{6,14}$")
TWILIO_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
TWILIO_TOKEN = re.compile(r"^[A-Za-z0-9]{16,64}$")
CALL_SID = re.compile(r"^CA[0-9a-fA-F]{32}$")
NUMBER_SID = re.compile(r"^PN[0-9a-fA-F]{32}$")
VOICE_RE = re.compile(r"^(?:(?:Polly|Google)\.[A-Za-z0-9-]{2,60}|alice|man|woman)$")
LANG_RE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}$")

TERMINAL_STATUSES = {"completed", "busy", "no-answer", "failed", "canceled"}


class ProviderError(Exception):
    """A provider call failed. The message is safe to show (never contains a secret)."""


class NotImplementedProvider(ProviderError):
    pass


def mask(secret: str | None, keep: int = 4) -> str:
    """'••••ab12' — never the whole value."""
    s = secret or ""
    if not s:
        return ""
    if len(s) <= keep * 2:
        return "•" * 8
    return "•" * 8 + s[-keep:]


def mask_sid(sid: str | None) -> str:
    s = sid or ""
    return (s[:4] + "…" + s[-4:]) if len(s) > 10 else ("•" * len(s))


# ═══ request signatures ══════════════════════════════════════════════════════

def twilio_signature(token: str, url: str, params: list[tuple[str, str]]) -> str:
    """Twilio's X-Twilio-Signature: base64(HMAC-SHA1(token, url + sorted key+value pairs))."""
    data = url + "".join(k + v for k, v in sorted(params))
    digest = hmac.new(token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _port_variants(url: str) -> list[str]:
    """Twilio may sign with or without the default port; try both forms."""
    out = [url]
    try:
        p = urlsplit(url)
    except ValueError:
        return out
    default = 443 if p.scheme == "https" else 80
    host = p.hostname or ""
    if p.port is None:
        out.append(urlunsplit((p.scheme, f"{host}:{default}", p.path, p.query, "")))
    elif p.port == default:
        out.append(urlunsplit((p.scheme, host, p.path, p.query, "")))
    return out


def public_url_candidates(headers: dict, scheme: str, path: str, query: str,
                          configured_base: str | None) -> list[str]:
    """Every URL the provider may have signed for this request.

    BUCS sits behind a tunnel (cloudflared → 127.0.0.1), so the URL uvicorn sees
    (http://127.0.0.1:8765/...) is NOT what the provider called. Reconstruct it from
    X-Forwarded-Proto / X-Forwarded-Host (or Host), and also from the configured public
    base URL. The HMAC check still needs the secret token, so trying candidates does not
    weaken the check.
    """
    h = {k.lower(): v for k, v in headers.items()}
    qs = ("?" + query) if query else ""
    cands: list[str] = []
    proto = (h.get("x-forwarded-proto") or scheme or "https").split(",")[0].strip().lower()
    host = (h.get("x-forwarded-host") or h.get("host") or "").split(",")[0].strip()
    if proto in ("http", "https") and host and re.fullmatch(r"[A-Za-z0-9.\-]+(?::\d{1,5})?", host):
        cands.append(f"{proto}://{host}{path}{qs}")
    if configured_base:
        cands.append(configured_base.rstrip("/") + path + qs)
    out: list[str] = []
    for c in cands:
        for v in _port_variants(c):
            if v not in out:
                out.append(v)
    return out


# ═══ TwiML ════════════════════════════════════════════════════════════════════

def _say(text: str, voice: str, language: str) -> str:
    return f"<Say voice={quoteattr(voice)} language={quoteattr(language)}>{_xml_escape(text[:1500])}</Say>"


def twiml(*parts: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(parts) + "</Response>"


def twiml_gather(say_text: str, action: str, voice: str, language: str,
                 reprompt_redirect: str | None = None, timeout_s: int = 6) -> str:
    """Speak, then listen. bargeIn lets the caller interrupt the agent mid-sentence;
    actionOnEmptyResult makes silence come back to us (so we can re-prompt or end)."""
    g = (f'<Gather input="speech" speechTimeout="auto" timeout="{int(timeout_s)}" bargeIn="true" '
         f'actionOnEmptyResult="true" method="POST" action={quoteattr(action)} '
         f'language={quoteattr(language)}>{_say(say_text, voice, language) if say_text else ""}</Gather>')
    tail = f'<Redirect method="POST">{_xml_escape(reprompt_redirect)}</Redirect>' if reprompt_redirect else ""
    return twiml(g, tail)


def twiml_goodbye(text: str, voice: str, language: str) -> str:
    return twiml(_say(text, voice, language), "<Hangup/>")


def twiml_wait(redirect: str, pause_s: int = 1, filler: str | None = None,
               voice: str = "alice", language: str = "en-US") -> str:
    """The reply is still being generated: keep the line alive and come back."""
    return twiml(_say(filler, voice, language) if filler else "", f'<Pause length="{int(pause_s)}"/>',
                 f'<Redirect method="POST">{_xml_escape(redirect)}</Redirect>')


def twiml_dial(text: str, number: str, action: str, voice: str, language: str, timeout_s: int = 20) -> str:
    return twiml(_say(text, voice, language),
                 f'<Dial timeout="{int(timeout_s)}" action={quoteattr(action)} method="POST">'
                 f"<Number>{_xml_escape(number)}</Number></Dial>")


def twiml_reject() -> str:
    return twiml('<Reject reason="rejected"/>')


# ═══ provider interface ═══════════════════════════════════════════════════════

class Provider:
    """Interface every telephony provider implements. Methods are async where they hit the network."""
    name = "base"
    label = "Provider"
    implemented = False

    @property
    def configured(self) -> bool:
        return False

    def verify(self, candidates: list[str], params: list[tuple[str, str]], headers: dict) -> bool:
        raise NotImplementedProvider(f"{self.label} webhooks are not implemented yet")

    async def account(self) -> dict:
        raise NotImplementedProvider(f"{self.label} is not implemented yet")

    async def list_numbers(self) -> list[dict]:
        raise NotImplementedProvider(f"{self.label} is not implemented yet")

    async def point_number(self, number_id: str, voice_url: str, status_url: str) -> dict:
        raise NotImplementedProvider(f"{self.label} is not implemented yet")

    async def place_call(self, to: str, from_: str, url: str, status_url: str,
                         record: bool, time_limit: int) -> dict:
        raise NotImplementedProvider(f"{self.label} is not implemented yet")

    async def hangup(self, call_id: str) -> dict:
        raise NotImplementedProvider(f"{self.label} is not implemented yet")

    async def start_recording(self, call_id: str) -> dict:
        raise NotImplementedProvider(f"{self.label} is not implemented yet")

    async def fetch_call(self, call_id: str) -> dict:
        raise NotImplementedProvider(f"{self.label} is not implemented yet")


class TwilioProvider(Provider):
    name = "twilio"
    label = "Twilio Programmable Voice"
    implemented = True
    API = "https://api.twilio.com/2010-04-01"

    def __init__(self, sid: str, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.sid = sid or ""
        self._token = token or ""
        self._transport = transport

    def __repr__(self) -> str:                 # never leak the token through repr/logging
        return f"<TwilioProvider sid={mask_sid(self.sid)} token={'set' if self._token else 'unset'}>"

    @property
    def configured(self) -> bool:
        return bool(TWILIO_SID.match(self.sid) and self._token)

    def verify(self, candidates: list[str], params: list[tuple[str, str]], headers: dict) -> bool:
        sig = ""
        for k, v in headers.items():
            if k.lower() == "x-twilio-signature":
                sig = v.strip()
        if not sig or not self._token:
            return False
        ok = False
        for url in candidates:                     # check all (no early exit timing signal)
            if hmac.compare_digest(twilio_signature(self._token, url, params).encode(), sig.encode()):
                ok = True
        return ok

    async def _req(self, method: str, path: str, data: list[tuple[str, str]] | None = None,
                   params: dict | None = None) -> dict:
        if not self.configured:
            raise ProviderError("Twilio is not configured — add your Account SID and Auth Token")
        url = f"{self.API}/Accounts/{self.sid}{path}"
        try:
            async with httpx.AsyncClient(auth=(self.sid, self._token), timeout=15.0,
                                         transport=self._transport) as c:
                if data is not None:           # repeated keys (StatusCallbackEvent) → encode ourselves
                    r = await c.request(method, url, params=params, content=urlencode(data).encode("ascii"),
                                        headers={"Content-Type": "application/x-www-form-urlencoded"})
                else:
                    r = await c.request(method, url, params=params)
        except httpx.HTTPError as e:
            raise ProviderError(f"Twilio unreachable: {type(e).__name__}") from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400:
            msg = str(body.get("message") or f"HTTP {r.status_code}")[:240]
            if self._token and self._token in msg:      # paranoia: never echo the secret
                msg = msg.replace(self._token, "••••")
            code = body.get("code")
            raise ProviderError(f"Twilio error{' ' + str(code) if code else ''}: {msg}")
        return body

    async def account(self) -> dict:
        b = await self._req("GET", ".json")
        return {"name": b.get("friendly_name") or "", "status": b.get("status") or "",
                "type": b.get("type") or ""}

    async def list_numbers(self) -> list[dict]:
        b = await self._req("GET", "/IncomingPhoneNumbers.json", params={"PageSize": 50})
        out = []
        for n in b.get("incoming_phone_numbers") or []:
            caps = n.get("capabilities") or {}
            out.append({"id": str(n.get("sid") or ""), "number": str(n.get("phone_number") or ""),
                        "name": str(n.get("friendly_name") or "")[:80],
                        "voice": bool(caps.get("voice", True)),
                        "voice_url": str(n.get("voice_url") or ""),
                        "status_callback": str(n.get("status_callback") or "")})
        return out

    async def point_number(self, number_id: str, voice_url: str, status_url: str) -> dict:
        if not NUMBER_SID.match(number_id or ""):
            raise ProviderError("invalid phone number id")
        b = await self._req("POST", f"/IncomingPhoneNumbers/{number_id}.json", data=[
            ("VoiceUrl", voice_url), ("VoiceMethod", "POST"),
            ("StatusCallback", status_url), ("StatusCallbackMethod", "POST")])
        return {"id": b.get("sid"), "number": b.get("phone_number"),
                "voice_url": b.get("voice_url"), "status_callback": b.get("status_callback")}

    async def place_call(self, to: str, from_: str, url: str, status_url: str,
                         record: bool, time_limit: int) -> dict:
        data = [("To", to), ("From", from_), ("Url", url), ("Method", "POST"),
                ("StatusCallback", status_url), ("StatusCallbackMethod", "POST"),
                ("StatusCallbackEvent", "initiated"), ("StatusCallbackEvent", "ringing"),
                ("StatusCallbackEvent", "answered"), ("StatusCallbackEvent", "completed"),
                ("Timeout", "25"), ("TimeLimit", str(int(time_limit)))]
        if record:
            data.append(("Record", "true"))
        b = await self._req("POST", "/Calls.json", data=data)
        return {"call_id": b.get("sid"), "status": b.get("status") or "queued"}

    async def hangup(self, call_id: str) -> dict:
        if not CALL_SID.match(call_id or ""):
            raise ProviderError("invalid call id")
        b = await self._req("POST", f"/Calls/{call_id}.json", data=[("Status", "completed")])
        return {"call_id": b.get("sid"), "status": b.get("status")}

    async def start_recording(self, call_id: str) -> dict:
        if not CALL_SID.match(call_id or ""):
            raise ProviderError("invalid call id")
        b = await self._req("POST", f"/Calls/{call_id}/Recordings.json", data=[])
        return {"recording_id": b.get("sid")}

    async def fetch_call(self, call_id: str) -> dict:
        if not CALL_SID.match(call_id or ""):
            raise ProviderError("invalid call id")
        b = await self._req("GET", f"/Calls/{call_id}.json")
        return {"status": b.get("status"), "duration": b.get("duration"),
                "price": b.get("price"), "price_unit": b.get("price_unit")}


class _Stub(Provider):
    """NOT IMPLEMENTED — placeholder so the provider interface is fixed. Every method refuses."""
    implemented = False

    def __init__(self, *a, **k) -> None:
        pass

    def verify(self, candidates, params, headers) -> bool:
        return False                                # a stub never accepts a webhook


class VonageProvider(_Stub):
    name = "vonage"
    label = "Vonage Voice (not implemented)"


class PlivoProvider(_Stub):
    name = "plivo"
    label = "Plivo Voice (not implemented)"


class ExotelProvider(_Stub):
    name = "exotel"
    label = "Exotel (India · not implemented)"


PROVIDERS = {"twilio": TwilioProvider, "vonage": VonageProvider, "plivo": PlivoProvider,
             "exotel": ExotelProvider}


def provider_catalog() -> list[dict]:
    return [{"name": k, "label": v.label, "implemented": v.implemented} for k, v in PROVIDERS.items()]
