"""AGENT COMMERCE MARKET RADAR — where real AI agents spend money (read-only).

Sources (public, machine-readable; every figure keeps its source, as-of time,
coverage and verification status):
  agenteconomy   https://agenteconomy.to/data.json        on-chain x402 totals (public Dune queries)
  cdp_bazaar     Coinbase x402 Bazaar discovery API        per-endpoint 30-day calls + unique payers,
                                                            from the Coinbase facilitator's settlements
  nohumans       https://nohumans.directory (REST)          probe reliability, newly verified listings,
                                                            what agents SEARCH for (IPs, not buyers)
  x402list       https://x402-list.com/api/v1/services       catalogue metadata (CC BY 4.0 — attribute)
  payapi         https://payapi.market/mcp (MCP)             settlement-verified listings (their OWN
                                                            verification purchase — never demand)
  onchain        Base mainnet USDC logs (publicnode RPC)     independent check of who pays top sellers

Rules: mainnet only (testnets dropped); no number is summed across sources
(each source measures a different thing); per-endpoint "revenue" is labelled
an ESTIMATE (calls × listed price); a directory's verification purchase is
never counted as customer demand. Nothing here buys anything.
"""

import asyncio
import gzip
import json
import math
import re
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx

HERE = Path(__file__).resolve().parent.parent
SNAP_DIR = HERE / ".update" / "radar"
UA = {"User-Agent": "BUCS-MarketRadar/1.0 (read-only market research; contact via "
                    "https://saikiranthatikonda-coder.github.io/Avengers-Bobbiey-UCS/)",
      "Accept": "application/json, text/event-stream, */*;q=0.5"}
CDP = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources"
BASE_RPC = "https://base-rpc.publicnode.com"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
AUTH_USED = "0x98de503528ee59b575ef0c0a2576a82497bfc029a5685b209e9ec333479b10a5"   # EIP-3009 AuthorizationUsed
MAINNETS = {"eip155:8453": "base", "base": "base", "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp": "solana",
            "solana": "solana", "eip155:137": "polygon", "eip155:1": "ethereum", "eip155:43114": "avalanche"}
TESTNET_HINTS = ("sepolia", "84532", "devnet", "testnet", "fuji", "amoy", "goerli")

# Market niches (keyword rules over name/description/tags), tuned on the Bazaar snapshot of
# 2026-10-30 — ORDER MATTERS: specific niches before the generic ones that share words.
CATEGORIES = [
    ("x402_trust", r"(x402|bazaar|seller|endpoint|payment\s*path).{0,40}(check|reliab\w*|uptime|lint|verif\w*|reputation|safe|debug|recommend\w*|pulse|discover\w*)|\b(api\s*recommendation|preflight)\b"),
    ("wallet_screening", r"\b(sanction\w*|ofac|aml\b|wallet[-\s]*(screen\w*|risk))"),
    ("social_search", r"\b(twitter|tweets?|x\.com|reddit|subreddit|tiktok|instagram\s*(posts?|profile))"),
    ("sec_filings", r"\b(sec\s*(edgar|filing\w*)|edgar|10-k|10-q|8-k|cik\b|us-gaap|annual\s*(facts|report)\s*by)"),
    ("domain_intel", r"\b(whois|rdap|dns\s*(record\w*|lookup)|mx\s*record\w*|spf|dmarc|domain\s*(age|intel\w*|lookup|info|reputation)|email\s*(validat\w*|verif\w*))"),
    ("people_company_enrichment", r"\b(enrich\w*|people[-\s]search|person\s*(lookup|enrich\w*)|contact\s*(data|info|finder)|email\s*finder|firmograph\w*|linkedin|company\s*(profile|lookup|data|info\w*)|lead\s*(gen\w*|data))"),
    ("web_extraction", r"\b(scrap\w*|crawl\w*|web[-\s]*(read|fetch)|fetch\s*(any|one|a|the)?\s*(public\s*)?(url|web\s*page|page)|read\s*(any|a|one)?\s*(live\s*|public\s*)?web\s*page|url\s*to\s*(markdown|text)|clean\s*(markdown|text)|page\s*content|extract\w*\s*(the\s*)?(content|text|data)\s*(from|of)|website\s*(data|content|extract\w*)|readable\s*content|batch\s*page)"),
    ("web_search", r"\b(web\s*search|search\s*(the\s*)?web|serp|neural\s*search|exa\b|search\s*engine|google\s*search|search\s*results)"),
    ("ai_text", r"\b(summar\w*|fact[-\s]?check\w*|translat\w*|sentiment|classif\w*|rewrite|paraphras\w*|proofread\w*)"),
    ("weather_geo", r"\b(weather|forecast|geocod\w*|ip\s*(geo\w*|lookup)|timezone|air\s*quality)"),
    ("ai_inference", r"\b(llm|gpt|claude|inference|chat\s*completion\w*|image\s*gen\w*|text[-\s]to[-\s](image|speech|video)|embedding\w*|generate\s*(an?\s*)?(image|video|audio))"),
    ("financial_data", r"\b(stock\w*|equit\w*|forex|exchange\s*rate|earnings|ticker|options\s*chain|funding\s*rates?|derivativ\w*|polymarket|prediction\s*market)"),
    ("onchain_intel", r"\b(on-?chain|wallet|token\w*|defi|dex\b|nft|crypto\w*|blockchain|solana|ethereum|memecoin|airdrop|altcoin\w*|coin\w*)"),
    ("news", r"\b(news|headline\w*)"),
]

# How well each niche fits what BUCS can deliver itself, from free/public data and its own
# code, without reselling a personal subscription. A judgement, shown next to the score.
BUCS_FIT = {"web_extraction": 1.0, "x402_trust": 0.9, "domain_intel": 0.9, "sec_filings": 0.8, "weather_geo": 0.6,
            "people_company_enrichment": 0.6, "ai_text": 0.6, "news": 0.5, "web_search": 0.4, "wallet_screening": 0.5,
            "financial_data": 0.2, "onchain_intel": 0.2, "ai_inference": 0.2, "social_search": 0.1, "other": 0.1}


FARM_MIN_ENDPOINTS, FARM_MAX_CALLS_PER_EP = 200, 2.5


def bucs_score(category: str, payers: int, calls: int) -> int:
    """0–100: observed independent demand (unique payers) × repeat usage × BUCS fit."""
    demand = min(1.0, math.log10(1 + payers) / math.log10(201))
    repeat = min(1.0, (calls / payers) / 20) if payers else 0.0
    return round(100 * BUCS_FIT.get(category, 0.2) * (0.7 * demand + 0.3 * repeat))


def categorize(*texts) -> str:
    t = " ".join(x for x in texts if x).lower()
    for cat, rx in CATEGORIES:
        if re.search(rx, t):
            return cat
    return "other"


def endpoint_key(url: str) -> str:
    try:
        p = urlsplit(url.strip())
        return (p.hostname or "").lower() + (p.path.rstrip("/") or "/")
    except Exception:
        return url


def _net(n: str) -> str | None:
    n = (n or "").lower()
    if any(h in n for h in TESTNET_HINTS):
        return None
    return MAINNETS.get(n, n if n.startswith("eip155:") or n.startswith("solana") else None)


class Radar:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.c = client
        self.errors: dict[str, str] = {}
        self.payapi_total = None

    async def _get(self, url, **kw):
        for attempt in range(4):                                       # ride out transient network drops / 429 / 5xx
            try:
                r = await self.c.get(url, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt == 3:
                        r.raise_for_status()
                    await asyncio.sleep(min(30.0, float(r.headers.get("retry-after") or 2 * 2 ** attempt)))
                    continue
                r.raise_for_status()
                return r
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
                if attempt == 3:
                    raise
                await asyncio.sleep(2 * 2 ** attempt)

    # ── sources ──────────────────────────────────────────────────
    async def agenteconomy(self) -> dict:
        d = (await self._get("https://agenteconomy.to/data.json")).json()
        x = d["x402"]
        daily = x.get("daily", [])
        last30 = sum(r.get("txs", 0) for r in daily[-30:])
        months = x.get("monthly", [])
        return {
            "source": "agenteconomy.to (public Dune queries: " + ", ".join(str(s.get("queryId")) for s in d.get("sources", [])[:3]) + ")",
            "as_of": x.get("asOf"), "verification": "on-chain (facilitator-registry scope, mainnet chains)",
            "coverage": {"facilitators": x.get("facilitatorsTracked"), "chains": x.get("chainsTracked"),
                         "registry": (x.get("coverage") or {}).get("registryVersion"),
                         "expanded_from": (x.get("coverage") or {}).get("expandedCoverageFrom")},
            "all_time_txs": x.get("totalTxs"), "all_time_volume_usd": x.get("totalVolume"),
            "last_30d_txs": last30, "daily": daily[-60:], "monthly": months[-12:],
            "facilitators_latest_day": x.get("recentFacilitators", [])[:12],
            "usdc_share_30d_base": (x.get("tokenSplit") or {}).get("usdcSharePct"),
            "payments_30d_base": (x.get("tokenSplit") or {}).get("totalPayments"),
            "mpp_tempo": {k: (d.get("tempoMpp") or {}).get(k) for k in ("totalEvents", "uniquePayers", "uniquePayees")},
            "facilitator_signers_base": [a.lower() for a in (x.get("coverage") or {}).get("coinbaseBaseAddresses", [])],
        }

    async def bazaar(self, max_pages: int = 120, page: int = 1000) -> list[dict]:
        out, offset = [], 0
        for _ in range(max_pages):
            d = (await self._get(CDP, params={"limit": page, "offset": offset})).json()
            items = d.get("items", [])
            for it in items:
                acc = next((a for a in it.get("accepts", []) if _net(a.get("network"))), None)
                if not acc:
                    continue                                          # testnet-only → excluded
                q = it.get("quality") or {}
                dec = 6 if (acc.get("asset", "").lower() == USDC_BASE or (acc.get("extra") or {}).get("name") == "USD Coin") else None
                price = (int(acc.get("amount") or acc.get("maxAmountRequired") or 0) / 10 ** dec) if dec else None
                out.append({
                    "key": endpoint_key(it.get("resource") or acc.get("resource", "")),
                    "resource": it.get("resource") or acc.get("resource"), "service": it.get("serviceName") or "",
                    "description": (it.get("description") or "")[:300], "tags": it.get("tags") or [],
                    "network": _net(acc.get("network")), "pay_to": (acc.get("payTo") or "").lower(),
                    "price_usd": price, "calls_30d": q.get("l30DaysTotalCalls") or 0,
                    "payers_30d": q.get("l30DaysUniquePayers") or 0, "last_called": q.get("lastCalledAt"),
                    "category": categorize(it.get("serviceName"), it.get("description"), " ".join(it.get("tags") or [])),
                })
            offset += len(items)
            total = (d.get("pagination") or {}).get("total", 0)
            if not items or offset >= total:
                break
            await asyncio.sleep(0.4)                                  # polite pacing
        return out

    async def nohumans(self, export_pages: int = 30) -> dict:
        stats = (await self._get("https://nohumans.directory/v1/stats")).json()
        demand = (await self._get("https://nohumans.directory/v1/demand")).json()
        cats = (await self._get("https://nohumans.directory/v1/categories")).json()
        listings, cursor = [], None
        for _ in range(export_pages):
            r = await self._get("https://nohumans.directory/v1/export", params={"limit": 500, **({"cursor": cursor} if cursor else {})})
            for line in r.text.splitlines():
                if line.strip():
                    try:
                        listings.append(json.loads(line))
                    except ValueError:
                        pass
            cursor = r.headers.get("x-next-cursor")
            if not cursor:
                break
            await asyncio.sleep(0.4)
        week = int(time.time()) - 7 * 86400
        try:
            ch = (await self._get("https://nohumans.directory/v1/changes", params={"since": week, "limit": 500})).json()
        except Exception:
            ch = {}
        return {"stats": stats, "demand": demand, "categories": cats.get("categories", []), "listings": listings, "changes": ch}

    async def x402list(self, pages: int = 60) -> list[dict]:
        out = []
        for p in range(1, pages + 1):
            d = (await self._get("https://x402-list.com/api/v1/services", params={"page": p})).json()
            out += d.get("data", [])
            if p >= (d.get("meta") or {}).get("total_pages", 1):
                break
            await asyncio.sleep(0.3)
        return out

    async def payapi(self) -> list[dict]:
        """PayAPI caps list_apis at 100 rows and has no offset, so sweep category × network and
        dedupe by id. Coverage (fetched / their total) is recorded, never assumed complete."""
        url, h = "https://payapi.market/mcp", {**UA, "Content-Type": "application/json"}
        r = await self.c.post(url, headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "bucs-radar", "version": "1.0"}}})
        if r.headers.get("mcp-session-id"):
            h["mcp-session-id"] = r.headers["mcp-session-id"]

        async def call(args, i):
            r = await self.c.post(url, headers=h, json={"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                                        "params": {"name": "list_apis", "arguments": {"limit": 100, "verified_only": False, **args}}})
            t = r.text
            if "data:" in t[:40]:
                t = "\n".join(l[5:].strip() for l in t.splitlines() if l.startswith("data:"))
            return json.loads(json.loads(t)["result"]["content"][0]["text"])
        first = await call({}, 2)
        seen = {x["id"]: x for x in first.get("results", [])}
        cats = [c if isinstance(c, str) else (c.get("name") or c.get("category")) for c in first.get("categories") or []]
        i = 3
        for cat in filter(None, cats):
            for net in (None, "base", "solana", "algorand"):
                d = await call({"category": cat, **({"network": net} if net else {})}, i); i += 1
                for x in d.get("results", []):
                    seen.setdefault(x["id"], x)
                await asyncio.sleep(0.3)
                if d.get("total", 0) <= 100:
                    break                                            # that category fits in one page
        self.payapi_total = first.get("total")
        return list(seen.values())

    # ── independent on-chain check (Base mainnet USDC) ───────────
    async def onchain(self, pay_to: str, facilitators: set[str], blocks: int = 43_200) -> dict:
        """Last ~24 h of USDC paid TO this seller: distinct payers, repeats, self-transfers,
        EIP-3009 (x402-style) share, concentration."""
        async def rpc(method, params):
            r = await self.c.post(BASE_RPC, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            d = r.json()
            if "error" in d:
                raise RuntimeError(str(d["error"])[:120])
            return d["result"]
        head = int(await rpc("eth_blockNumber", []), 16)
        topic_to = "0x" + "0" * 24 + pay_to[2:].lower()
        logs = await rpc("eth_getLogs", [{"address": USDC_BASE, "fromBlock": hex(head - blocks), "toBlock": hex(head),
                                          "topics": [TRANSFER, None, topic_to]}])
        payers = ["0x" + l["topics"][1][-40:] for l in logs]
        amounts = [int(l["data"], 16) / 1e6 for l in logs]
        txs = {l["transactionHash"] for l in logs}
        auth_tx: set[str] = set()
        uniq = sorted(set(payers))
        for i in range(0, min(len(uniq), 400), 100):                 # EIP-3009 AuthorizationUsed by those payers
            chunk = ["0x" + "0" * 24 + a[2:] for a in uniq[i:i + 100]]
            try:
                al = await rpc("eth_getLogs", [{"address": USDC_BASE, "fromBlock": hex(head - blocks), "toBlock": hex(head),
                                                "topics": [AUTH_USED, chunk]}])
                auth_tx |= {l["transactionHash"] for l in al}
            except Exception:
                break
        self_tx = sum(1 for p in payers if p == pay_to.lower())
        fac = sum(1 for p in payers if p in facilitators)
        external = [(p, a) for p, a in zip(payers, amounts) if p != pay_to.lower() and p not in facilitators]
        cnt = Counter(p for p, _ in external)
        top_share = (cnt.most_common(1)[0][1] / len(external)) if external else 0
        return {"window_hours": round(blocks * 2 / 3600), "transfers_in": len(logs), "usdc_in": round(sum(amounts), 4),
                "x402_style_share": round(len(txs & auth_tx) / len(txs), 3) if txs else 0,
                "distinct_external_payers": len(cnt), "repeat_payers": sum(1 for v in cnt.values() if v > 1),
                "external_usdc": round(sum(a for _, a in external), 4), "self_transfers": self_tx,
                "from_facilitator_addresses": fac, "top_payer_share": round(top_share, 3),
                "median_payment": round(statistics.median([a for _, a in external]), 6) if external else None,
                "head_block": head}

    # ── full run ─────────────────────────────────────────────────
    async def collect(self, onchain_top: int = 20) -> dict:
        own = self.c is None
        if own:
            self.c = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), headers=UA, follow_redirects=True)
        t0 = time.time()
        res: dict = {"started": t0}
        try:
            for name, fn in (("agenteconomy", self.agenteconomy), ("bazaar", self.bazaar), ("nohumans", self.nohumans),
                             ("x402list", self.x402list), ("payapi", self.payapi)):
                try:
                    res[name] = await fn()
                    res.setdefault("fetched_at", {})[name] = time.time()
                except Exception as e:
                    self.errors[name] = f"{type(e).__name__}: {str(e)[:160]}"
            fac = set((res.get("agenteconomy") or {}).get("facilitator_signers_base", []))
            sellers = defaultdict(lambda: {"calls": 0})
            for it in res.get("bazaar") or []:
                if it["network"] == "base" and it["pay_to"].startswith("0x"):
                    sellers[it["pay_to"]]["calls"] += it["calls_30d"]
            res["onchain"] = {}
            for addr, _ in sorted(sellers.items(), key=lambda kv: -kv[1]["calls"])[:onchain_top]:
                try:
                    res["onchain"][addr] = await self.onchain(addr, fac)
                except Exception as e:
                    res["onchain"][addr] = {"error": str(e)[:120]}
                await asyncio.sleep(0.5)
        finally:
            if own:
                await self.c.aclose()
        res["errors"] = self.errors
        res["payapi_total"] = self.payapi_total
        res["duration_s"] = round(time.time() - t0, 1)
        return res


# ── analysis: turn raw evidence into the radar ────────────────────
def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 6) if xs else None


def analyse(raw: dict, prev: dict | None = None) -> dict:
    now = time.time()
    fa = raw.get("fetched_at", {})
    bz = [b for b in (raw.get("bazaar") or []) if b.get("network")]
    nh = raw.get("nohumans") or {}
    ae = raw.get("agenteconomy") or {}
    oc = raw.get("onchain") or {}
    # dedupe the Bazaar by endpoint key (the same URL can be listed per network/price)
    by_key: dict[str, dict] = {}
    for b in bz:
        cur = by_key.get(b["key"])
        if not cur or b["calls_30d"] > cur["calls_30d"]:
            by_key[b["key"]] = b
    eps = [dict(e, category=categorize(e["service"], e["description"], " ".join(e.get("tags") or []))) for e in by_key.values()]
    # listing farms: one wallet listing hundreds of endpoints that each saw ~1 call — the Bazaar's
    # listing bootstrap, not customers. Their endpoints are reported, never counted as demand.
    per_seller = defaultdict(lambda: [0, 0])
    for e in eps:
        per_seller[e["pay_to"]][0] += 1; per_seller[e["pay_to"]][1] += e["calls_30d"]
    farms = {a: n for a, (n, c) in per_seller.items() if n >= FARM_MIN_ENDPOINTS and c / n <= FARM_MAX_CALLS_PER_EP}
    # independent demand = ≥2 unique paying wallets (a lone payer is often the seller or a directory test)
    demand = [e for e in eps if e["pay_to"] not in farms and e["payers_30d"] >= 2]
    active = [e for e in eps if e["calls_30d"] > 0]
    priced = [e["price_usd"] for e in eps if e["price_usd"]]
    call_w = [(e["price_usd"], e["calls_30d"]) for e in active if e["price_usd"]]
    est_rev = lambda e: (e["price_usd"] or 0) * e["calls_30d"]                         # noqa: E731

    # directory references for each endpoint (other sources) — dedup across sources by endpoint key
    nh_by_key = {endpoint_key(l.get("endpoint_url", "")): l for l in nh.get("listings", [])}
    pa_by_host = defaultdict(list)
    for p in raw.get("payapi") or []:
        pa_by_host[(urlsplit(p.get("base_url") or "").hostname or "").lower()].append(p)
    xl_by_host = {(urlsplit(x.get("base_url") or "").hostname or "").lower(): x for x in raw.get("x402list") or []}

    def evidence(e):
        host = e["key"].split("/")[0]
        nl, pa, xl = nh_by_key.get(e["key"]), pa_by_host.get(host, []), xl_by_host.get(host)
        chain = oc.get(e["pay_to"]) if e["network"] == "base" else None
        return {"nohumans": {"status": nl.get("status"), "score": round(nl.get("score") or 0, 3), "probes": nl.get("probe_count")} if nl else None,
                "payapi": {"payment_verified": any(p.get("payment_verified") for p in pa),
                           "note": "PayAPI's own verification purchase (proves payable, not demand)"} if pa else None,
                "x402list": {"verified": xl.get("verified"), "payment_ready": xl.get("payment_ready")} if xl else None,
                "onchain_24h": chain}

    # niches: demand counted per SELLER (its best endpoint's unique payers), summed over distinct
    # sellers — a wallet buying five endpoints from one seller is not five buyers
    cat = defaultdict(lambda: {"endpoints": 0, "active": 0, "calls_30d": 0, "payer_sum": 0, "est_rev_30d": 0.0,
                               "strong": 0, "prices": [], "sellers": defaultdict(int)})
    for e in eps:
        if e["pay_to"] in farms:
            continue
        c = cat[e["category"]]
        c["endpoints"] += 1
        if e["price_usd"]:
            c["prices"].append(e["price_usd"])
        if e["payers_30d"] >= 2:
            c["active"] += 1; c["calls_30d"] += e["calls_30d"]; c["payer_sum"] += e["payers_30d"]
            c["est_rev_30d"] += est_rev(e)
            c["strong"] += e["payers_30d"] >= 5
            c["sellers"][e["pay_to"]] = max(c["sellers"][e["pay_to"]], e["payers_30d"])
    top_by_cat = defaultdict(list)
    for e in demand:
        top_by_cat[e["category"]].append(e)
    prev_cats = (prev or {}).get("categories_raw", {})
    categories = []
    for k, c in cat.items():
        sellers = c.pop("sellers")
        top = sorted(top_by_cat.get(k, []), key=lambda e: (-e["payers_30d"], -e["calls_30d"]))[:5]
        p0 = prev_cats.get(k, {}).get("calls_30d")
        categories.append({"category": k, **{kk: v for kk, v in c.items() if kk != "prices"},
                           "seller_demand": sum(sellers.values()), "paid_sellers": len(sellers),
                           "sellers_5plus": sum(1 for v in sellers.values() if v >= 5),
                           "est_rev_30d": round(c["est_rev_30d"], 2), "median_price": _median(c["prices"]),
                           "growth_vs_prev": round((c["calls_30d"] - p0) / p0, 3) if p0 else None,
                           "top": [{"service": t["service"], "resource": t["resource"], "calls_30d": t["calls_30d"],
                                    "payers_30d": t["payers_30d"], "price_usd": t["price_usd"]} for t in top]})
    categories.sort(key=lambda c: -c["seller_demand"])

    # demand signal from searches (IPs, not buyers) — mapped to the same categories
    needs = [n for n in (nh.get("demand") or {}).get("top_needs", []) if n.get("kind") == "need"]
    search_by_cat = defaultdict(lambda: {"terms": 0, "buyer_ips": 0, "examples": []})
    for n in needs:
        k = categorize(n.get("q"))
        s = search_by_cat[k]; s["terms"] += 1; s["buyer_ips"] += n.get("buyer_client_ips") or 0
        if len(s["examples"]) < 6:
            s["examples"].append(n.get("q"))
    unmet = [n for n in needs if n.get("catalogue_status") == "search_returned_nothing"]

    # endpoints table rows
    def row(e):
        return {"service": e["service"], "resource": e["resource"], "category": e["category"], "network": e["network"],
                "price_usd": e["price_usd"], "calls_30d": e["calls_30d"], "payers_30d": e["payers_30d"],
                "repeat_ratio": round(e["calls_30d"] / e["payers_30d"], 1) if e["payers_30d"] else None,
                "est_rev_30d_usd": round(est_rev(e), 2), "pay_to": e["pay_to"], "last_called": e["last_called"],
                "bucs_score": bucs_score(e["category"], e["payers_30d"], e["calls_30d"]), "evidence": evidence(e)}
    by_payers = sorted([e for e in demand if e["payers_30d"] >= 5], key=lambda e: (-e["payers_30d"], -e["calls_30d"]))
    top_revenue = sorted([e for e in demand if e["payers_30d"] >= 3], key=lambda e: -est_rev(e))[:20]
    top_repeat = sorted([e for e in demand if e["payers_30d"] >= 5], key=lambda e: -(e["calls_30d"] / e["payers_30d"]))[:20]

    # competitors: one row per seller service (wallet + host + niche) in niches BUCS can serve; the row is
    # its best endpoint, with the service's total calls and endpoint count alongside
    svc = {}
    for e in demand:
        if BUCS_FIT.get(e["category"], 0) < 0.4:
            continue
        k = (e["pay_to"], e["key"].split("/")[0], e["category"])
        cur = svc.setdefault(k, {"best": e, "calls": 0, "eps": 0})
        cur["calls"] += e["calls_30d"]; cur["eps"] += 1
        if e["payers_30d"] > cur["best"]["payers_30d"]:
            cur["best"] = e
    competitors = sorted(svc.values(), key=lambda v: -bucs_score(v["best"]["category"], v["best"]["payers_30d"], v["best"]["calls_30d"]))[:20]
    opportunities = []
    for c in categories:
        sd = search_by_cat.get(c["category"], {})
        opportunities.append({"category": c["category"], "fit": BUCS_FIT.get(c["category"], 0.1), "seller_demand": c["seller_demand"],
                              "payer_sum_30d": c["payer_sum"], "paid_sellers": c["paid_sellers"], "sellers_5plus": c["sellers_5plus"],
                              "calls_30d": c["calls_30d"], "endpoints_with_5plus_payers": c["strong"], "active_competitors": c["active"],
                              "listed": c["endpoints"], "median_price_usd": c["median_price"], "est_rev_30d_usd": c["est_rev_30d"],
                              "search_terms": sd.get("terms", 0), "search_ips": sd.get("buyer_ips", 0), "search_examples": sd.get("examples", []),
                              "growth_vs_prev": c["growth_vs_prev"], "leaders": c["top"][:3]})
    opportunities.sort(key=lambda o: -o["seller_demand"])

    listings = nh.get("listings", [])
    unreliable = sorted([l for l in listings if l.get("status") not in ("verified",) or (l.get("score") or 1) < 0.5],
                        key=lambda l: (l.get("score") or 0))[:30]
    ch_items = (nh.get("changes") or {}).get("changes") or (nh.get("changes") or {}).get("items") or []
    newly = sorted([{k: c.get(k) for k in ("name", "endpoint_url", "from_status", "changed_at", "reason")} for c in ch_items
                    if c.get("to_status") == "verified" and c.get("from_status") != "verified"],
                   key=lambda c: -(c["changed_at"] or 0))[:40]
    sellers = {e["pay_to"] for e in active if e["pay_to"]}
    return {
        "generated_at": raw.get("started") or now, "analysed_at": now,      # data age = when it was COLLECTED
        "sources": {
            "agenteconomy": {"url": "https://agenteconomy.to/data.json", "fetched_at": fa.get("agenteconomy"), "as_of": ae.get("as_of"), "verification": ae.get("verification")},
            "cdp_bazaar": {"url": CDP, "fetched_at": fa.get("bazaar"), "verification": "Coinbase facilitator settlements (per-endpoint 30-day calls & unique payers)",
                           "coverage": "endpoints listed in the Bazaar and settled through the Coinbase facilitator; mainnet only"},
            "nohumans": {"url": "https://nohumans.directory", "fetched_at": fa.get("nohumans"), "verification": "directory probes (402 challenge checks); searches are IPs, not buyers",
                         "license": "CC BY 4.0: Data: nohumans.directory (https://nohumans.directory)"},
            "x402list": {"url": "https://x402-list.com/api/v1/services", "fetched_at": fa.get("x402list"), "license": "CC BY 4.0: Data: x402-list.com"},
            "payapi": {"url": "https://payapi.market/mcp", "fetched_at": fa.get("payapi"), "verification": "PayAPI's own settlement test per listing (proves payable, not demand)"},
            "onchain": {"url": BASE_RPC, "verification": "Base mainnet USDC Transfer + EIP-3009 AuthorizationUsed logs, last ~24 h, top sellers by Bazaar calls"},
        },
        "errors": raw.get("errors", {}),
        "macro": {k: ae.get(k) for k in ("all_time_txs", "all_time_volume_usd", "last_30d_txs", "monthly", "daily", "facilitators_latest_day",
                                         "usdc_share_30d_base", "payments_30d_base", "mpp_tempo", "coverage", "as_of", "source")},
        "endpoints": {"listed_mainnet": len(eps), "active_30d": len(active), "active_sellers_30d": len(sellers),
                      "farm_endpoints": sum(1 for e in eps if e["pay_to"] in farms), "farm_sellers": len(farms),
                      "single_payer_endpoints": sum(1 for e in eps if e["pay_to"] not in farms and e["payers_30d"] == 1),
                      "demand_endpoints": len(demand), "demand_sellers": len({e["pay_to"] for e in demand}),
                      "sellers_5plus": len({e["pay_to"] for e in demand if e["payers_30d"] >= 5}),
                      "sellers_20plus": len({e["pay_to"] for e in demand if e["payers_30d"] >= 20}),
                      "sellers_100plus": len({e["pay_to"] for e in demand if e["payers_30d"] >= 100}),
                      "with_5plus_payers": len(by_payers), "calls_30d_sum": sum(e["calls_30d"] for e in active),
                      "payer_sum_not_deduplicated": sum(e["payers_30d"] for e in active),
                      "max_payers_single_endpoint": max([e["payers_30d"] for e in active], default=0),
                      "price_median_listed": _median(priced), "price_mean_listed": round(sum(priced) / len(priced), 6) if priced else None,
                      "price_median_call_weighted": _weighted_median(call_w)},
        "directory": {"nohumans_listings": (nh.get("stats") or {}).get("listings_total"), "nohumans_verified": (nh.get("stats") or {}).get("verified"),
                      "nohumans_hosts": (nh.get("stats") or {}).get("hosts_total"), "probe_pass_rate_24h": (nh.get("stats") or {}).get("probe_pass_rate_24h"),
                      "x402list_services": len(raw.get("x402list") or []), "payapi_listings": len(raw.get("payapi") or []), "payapi_total": raw.get("payapi_total"),
                      "payapi_payment_verified": sum(1 for p in raw.get("payapi") or [] if p.get("payment_verified"))},
        "categories": categories, "categories_raw": {c["category"]: {"calls_30d": c["calls_30d"]} for c in categories},
        "search_demand": {"window": [(nh.get("demand") or {}).get("window_starts_at_iso"), (nh.get("demand") or {}).get("window_ends_at_iso")],
                          "distinct_client_ips": (nh.get("demand") or {}).get("distinct_client_ips"),
                          "by_category": dict(search_by_cat), "top_needs": [{k: n.get(k) for k in ("q", "searches", "buyer_client_ips", "distinct_days", "catalogue_status")}
                                                                          for n in needs[:40]],
                          "unmet": [n.get("q") for n in unmet][:20]},
        "top_revenue": [row(e) for e in top_revenue], "top_repeat": [row(e) for e in top_repeat],
        "top_payers": [row(e) for e in by_payers[:60]],
        "competitors": [dict(row(v["best"]), service_calls_30d=v["calls"], service_endpoints=v["eps"]) for v in competitors],
        "opportunities": opportunities,
        "listing_farms": [{"pay_to": a, "endpoints": n, "calls_30d": per_seller[a][1]} for a, n in sorted(farms.items(), key=lambda kv: -kv[1])],
        "unreliable": [{k: l.get(k) for k in ("name", "endpoint_url", "status", "score", "probe_count", "category", "price_amount")} for l in unreliable],
        "newly_verified_7d": newly,
        "onchain": oc,
    }


def _weighted_median(pairs):
    pairs = sorted(p for p in pairs if p[0] is not None)
    tot = sum(w for _, w in pairs)
    if not tot:
        return None
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= tot / 2:
            return round(v, 6)


def save_snapshot(raw: dict, radar: dict) -> Path:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    p = SNAP_DIR / f"radar-{stamp}.json.gz"
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump({"radar": radar, "raw": raw}, f)                     # full evidence, so it can be re-analysed
    from atomicio import atomic_write_text
    atomic_write_text(SNAP_DIR / "latest.json", json.dumps(radar))
    return p


def reanalyse(path: Path) -> dict:
    """Re-run the analysis over a stored snapshot's raw evidence (no network)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return analyse(json.load(f)["raw"])


def latest() -> dict | None:
    try:
        return json.loads((SNAP_DIR / "latest.json").read_text(encoding="utf-8"))
    except Exception:
        return None
