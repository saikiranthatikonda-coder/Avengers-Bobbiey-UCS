"""Service executors — the work a buyer pays for.

Only services BUCS can deliver reliably today are implemented. website.extract
fetches a public web page and returns structured facts from it; it refuses
private/internal addresses (SSRF protection), caps size and time, and reports
exactly what it found — nothing is guessed.
"""

import asyncio
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

MAX_BYTES = 2_000_000
UA = "BUCS-WebsiteExtraction/1.0 (+https://saikiranthatikonda-coder.github.io/Avengers-Bobbiey-UCS/)"


class ExecutionError(Exception):
    """A clean, structured failure. `code` is stable for machines; nothing is charged
    for a failed request on rails that settle after delivery (x402)."""

    def __init__(self, message: str, code: str = "EXTRACTION_FAILED", http_status: int | None = None):
        super().__init__(message)
        self.code, self.http_status = code, http_status

    def as_dict(self) -> dict:
        d = {"code": self.code, "message": str(self)}
        if self.http_status is not None:
            d["http_status"] = self.http_status
        return d


def _public_host(host: str) -> str:
    """Refuse localhost, private, link-local, reserved and metadata addresses; return a vetted IP."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ExecutionError(f"cannot resolve {host}", "DNS_FAILED")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ExecutionError("that address is not a public website", "NOT_PUBLIC")
    return infos[0][4][0]


class _PinnedBackend(AutoBackend):
    """Resolve + vet + connect in one step, so a DNS answer can't change between the
    check and the connection (DNS rebinding). TLS still verifies the real hostname."""

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        ip = await asyncio.to_thread(_public_host, host)
        return await super().connect_tcp(ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options)


def _client() -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport()
    pool = transport._pool
    assert isinstance(pool, httpcore.AsyncConnectionPool) and hasattr(pool, "_network_backend")
    pool._network_backend = _PinnedBackend()
    return httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False,
                             headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"})


class _Extract(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.title, self.meta, self.heads, self.links, self.text = base, "", {}, [], [], []
        self._in, self._skip, self.lang, self.canonical = None, 0, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        if tag in ("title", "h1", "h2", "h3"):
            self._in = tag; self.heads.append([tag, ""]) if tag != "title" else None
        if tag == "meta":
            k = (a.get("name") or a.get("property") or "").lower()
            if k and a.get("content"):
                self.meta[k] = a["content"][:500]
        if tag == "link" and "canonical" in (a.get("rel") or "").lower():
            self.canonical = a.get("href")
        if tag == "a" and a.get("href"):
            self.links.append(urljoin(self.base, a["href"]))

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1
        if tag == self._in:
            self._in = None

    def handle_data(self, data):
        if self._skip:
            return
        if self._in == "title":
            self.title += data
        elif self._in in ("h1", "h2", "h3") and self.heads:
            self.heads[-1][1] += data
        self.text.append(data)


async def website_extract(inp: dict) -> dict:
    url = str(inp.get("url", "")).strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ExecutionError("url must be a public http(s) address", "BAD_URL")
    t0 = time.time()
    seen = 0
    async with _client() as c:
        for _hop in range(5):                                    # follow redirects manually, re-checking each host
            await asyncio.to_thread(_public_host, urlsplit(url).hostname)
            async with c.stream("GET", url) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    if urlsplit(url).scheme not in ("http", "https"):
                        raise ExecutionError("redirect to a non-web address", "BAD_REDIRECT")
                    continue
                status, ctype, body = r.status_code, r.headers.get("content-type", ""), b""
                async for chunk in r.aiter_bytes():
                    body += chunk; seen += len(chunk)
                    if seen > MAX_BYTES:
                        break
                break
        else:
            raise ExecutionError("too many redirects", "TOO_MANY_REDIRECTS")
    # a block page / error page is not a result: never deliver (or charge for) it as success
    if status >= 400:
        code = "SITE_REFUSED" if status in (401, 403, 429) else "SITE_ERROR"
        raise ExecutionError(f"the site answered HTTP {status}" + (" (it blocks automated clients)" if code == "SITE_REFUSED" else ""),
                             code, status)
    sniff = body[:512].lstrip().lower()
    if "html" not in ctype.lower() and not (not ctype and (sniff.startswith(b"<!doctype html") or sniff.startswith(b"<html"))):
        raise ExecutionError(f"not an HTML page ({ctype or 'unknown type'})", "NOT_HTML")
    html = body.decode(r.encoding or "utf-8", errors="replace")
    p = _Extract(url)
    p.feed(html)
    text = re.sub(r"\s+", " ", " ".join(p.text)).strip()
    host = urlsplit(url).hostname
    internal = [l for l in p.links if urlsplit(l).hostname == host]
    external = [l for l in p.links if urlsplit(l).hostname not in (host, None) and l.startswith("http")]
    socials = sorted({l for l in external if re.search(r"(linkedin|twitter|x\.com|facebook|instagram|youtube|github)\.", l)})[:12]
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)))[:20]
    words = len(text.split())
    desc = p.meta.get("description") or p.meta.get("og:description")
    if words < 20 and not desc:
        raise ExecutionError("the page has no extractable static content (likely rendered by JavaScript)", "NO_STATIC_CONTENT")
    warnings = []
    if words < 150:
        warnings.append("little static text: the page is probably rendered by JavaScript; only metadata is reliable")
    if seen > MAX_BYTES:
        warnings.append(f"page larger than {MAX_BYTES // 1_000_000} MB: content after that was not read")
    if not desc:
        warnings.append("no meta description")
    score = round(min(1.0, (0.35 if p.title.strip() else 0) + (0.2 if desc else 0) + min(words, 600) / 600 * 0.3
                      + (0.15 if p.heads else 0)), 2)
    return {
        "schema": "bucs.website.extract/v1",
        "quality": {"score": score, "warnings": warnings},
        "text": text[:20_000], "text_chars": len(text), "text_truncated": len(text) > 20_000,
        "url": url, "status_code": status, "fetched_at": time.time(), "elapsed_ms": round((time.time() - t0) * 1000),
        "title": p.title.strip()[:300], "description": p.meta.get("description") or p.meta.get("og:description"),
        "language": p.lang, "canonical": p.canonical, "generator": p.meta.get("generator"),
        "og": {k: v for k, v in p.meta.items() if k.startswith("og:")},
        "headings": [{"level": t, "text": re.sub(r"\s+", " ", s).strip()[:200]} for t, s in p.heads if s.strip()][:40],
        "links": {"internal": len(set(internal)), "external": len(set(external)), "external_sample": sorted(set(external))[:15]},
        "social_profiles": socials, "emails": emails, "word_count": words,
        "truncated": seen > MAX_BYTES,
    }


EXECUTORS = {"website.extract": website_extract}
