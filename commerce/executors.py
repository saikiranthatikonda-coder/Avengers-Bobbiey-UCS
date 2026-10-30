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

import httpx

MAX_BYTES = 2_000_000
UA = "BUCS-WebsiteExtraction/1.0 (+https://saikiranthatikonda-coder.github.io/Avengers-Bobbiey-UCS/)"


class ExecutionError(Exception):
    pass


def _public_host(host: str) -> None:
    """Refuse localhost, private, link-local, reserved and metadata addresses."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ExecutionError(f"cannot resolve {host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ExecutionError("that address is not a public website")


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
        raise ExecutionError("url must be a public http(s) address")
    t0 = time.time()
    seen = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False,
                                 headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"}) as c:
        for _hop in range(5):                                    # follow redirects manually, re-checking each host
            await asyncio.to_thread(_public_host, urlsplit(url).hostname)
            async with c.stream("GET", url) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    if urlsplit(url).scheme not in ("http", "https"):
                        raise ExecutionError("redirect to a non-web address")
                    continue
                status, ctype, body = r.status_code, r.headers.get("content-type", ""), b""
                async for chunk in r.aiter_bytes():
                    body += chunk; seen += len(chunk)
                    if seen > MAX_BYTES:
                        break
                break
        else:
            raise ExecutionError("too many redirects")
    if "html" not in ctype.lower():
        raise ExecutionError(f"not an HTML page ({ctype or 'unknown type'})")
    html = body.decode(r.encoding or "utf-8", errors="replace")
    p = _Extract(url)
    p.feed(html)
    text = re.sub(r"\s+", " ", " ".join(p.text)).strip()
    host = urlsplit(url).hostname
    internal = [l for l in p.links if urlsplit(l).hostname == host]
    external = [l for l in p.links if urlsplit(l).hostname not in (host, None) and l.startswith("http")]
    socials = sorted({l for l in external if re.search(r"(linkedin|twitter|x\.com|facebook|instagram|youtube|github)\.", l)})[:12]
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)))[:20]
    return {
        "url": url, "status_code": status, "fetched_at": time.time(), "elapsed_ms": round((time.time() - t0) * 1000),
        "title": p.title.strip()[:300], "description": p.meta.get("description") or p.meta.get("og:description"),
        "language": p.lang, "canonical": p.canonical, "generator": p.meta.get("generator"),
        "og": {k: v for k, v in p.meta.items() if k.startswith("og:")},
        "headings": [{"level": t, "text": re.sub(r"\s+", " ", s).strip()[:200]} for t, s in p.heads if s.strip()][:40],
        "links": {"internal": len(set(internal)), "external": len(set(external)), "external_sample": sorted(set(external))[:15]},
        "social_profiles": socials, "emails": emails, "word_count": len(text.split()),
        "truncated": seen > MAX_BYTES,
    }


EXECUTORS = {"website.extract": website_extract}
