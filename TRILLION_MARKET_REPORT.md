# Project Trillion: Agent Commerce Market Report

**Snapshot:** 30 Oct 2026, 19:08 IST (`.update/radar/radar-20261030-190812.json.gz`). Refresh it from the dashboard: **PROJECT TRILLION → AGENT COMMERCE MARKET RADAR → REFRESH**.

**Method:** read-only collection from public sources. Nothing was bought, submitted or registered.

**Ground rules for every figure:**
- Mainnet only; testnets are excluded.
- Figures are never added across sources, because each source measures something different.
- "EST." means calls × listed price. It is an estimate, not revenue.
- A directory's own verification purchase is never treated as customer demand.

## Sources

| Source | What it measures | Coverage in this snapshot | Verification |
|---|---|---|---|
| agenteconomy.to/data.json | x402 totals on-chain (public Dune queries) | 18 facilitators, 12 chains, as of 30 Oct 03:25 UTC | on-chain |
| Coinbase CDP x402 Bazaar (discovery API) | per endpoint: 30-day settled calls and unique paying wallets | 104,309 resources fetched (mainnet only after filtering) | settled by the Coinbase facilitator; **volume through other facilitators is not seen** |
| nohumans.directory REST (CC BY 4.0) | probe reliability, newly verified listings, **what agents search for** | 11,069 listings; 24,441 searches from 3,130 client IPs (23 Sep–23 Oct) | probes; searches are IPs, **not buyers** |
| x402-list.com API (CC BY 4.0) | catalogue metadata | 1,207 services | directory |
| PayAPI Market MCP | settlement-verified listings | 529 of 2,315 (their API returns at most 100 per call) | **PayAPI's own test purchase**: proves an API is payable, not that anyone wants it |
| Base mainnet USDC logs (publicnode RPC) | independent check: who actually paid the top 20 sellers in the last ~24 h | 20 sellers | on-chain Transfer + EIP-3009 `AuthorizationUsed` (x402-style share ≈ 1.0 for most sellers, which confirms the method) |

## What the market really looks like

**Headline numbers (agenteconomy, on-chain):**
- x402 all time: 195.7 M transactions and $42.3 M.
- **Monthly transactions are falling while value per transaction rises:**

  | Month | Transactions | Volume |
  |---|---|---|
  | Aug 2026 | 17.9 M | $437 k |
  | Sep 2026 | 13.0 M | $417 k |
  | Oct 2026 (to 30th) | 4.8 M | $264 k |

- Coinbase settled about 79% of transactions on 29 Oct (108,498 of 136,873) and PayAI 20%.

**Listing inflation is extreme.**
- **8 wallets list 80,106 of the 104,050 mainnet Bazaar endpoints (77%).** Each of those endpoints saw about 1 call.
- That pattern is listing bootstrap, not customers. One wallet alone (`market.datapackvibe.com`) lists 75,359.
- The radar now detects these farms and excludes them from every demand figure. A further 7,350 endpoints had exactly one payer, which is often the seller testing itself; these are excluded too.

**The genuine buyer market is small and concentrated:**

| Sellers whose best endpoint had… (30 days, farms excluded) | Count |
|---|---|
| ≥2 unique paying wallets | 1,589 |
| ≥5 | 886 |
| **≥20** | **52** |
| ≥100 | 7 |

The on-chain cross-check agrees with the Bazaar wherever both can be compared:

| Seller | Bazaar, 30 d | On-chain, last 24 h |
|---|---|---|
| stableenrich.dev | 161 payers | 40 distinct payers, 26 repeat, $32 |
| agents-trust.com | 55 payers | 27 payers |
| oneshotagent.com | 25 payers | 9 payers, 6 repeat, $0.16 |

So these are real, repeat-buying agents. They are few, and they pay fractions of a cent.

**Prices:**
- Listed median is $0.010; call-weighted median is $0.010.
- Mean is $0.045, skewed by a few $1–$10 listings.
- Web reading and extraction clears at **$0.001–0.002**.

**Implication:** the realistic first goal is not revenue. It is **a handful of genuine agent wallets that buy repeatedly**. The market rewards being cheap, reliable and discoverable where agents already look.

---

## A. Ten opportunities, ranked by observed demand

**How the columns are defined:**
- **Demand:** for each seller, the unique payers on its best endpoint in the niche, summed over distinct sellers (Bazaar, 30 d, farms excluded). It ranks niches; it is not a buyer count, because one agent buying from three sellers counts three times.
- **Repeat:** calls ÷ payers on the leading endpoints.
- **Cost per request:** BUCS's own estimate.

| # | Niche | Demand | Sellers with ≥5 buyers | Leaders (payers / calls / price) | Repeat | Median price | Agent searches (nohumans) | BUCS fit |
|---|---|---|---|---|---|---|---|---|
| 1 | On-chain intelligence | 4,376 | 253 | ax1.vc verdicts 490 / 123k / $0.02; onesource.io balances 444 / 450 / $0.003 | very high (bots) | $0.005 | 46 terms / 74 IPs | **low:** crowded, crypto-native, not BUCS's strength |
| 2 | AI text (summarise, classify, fact-check) | 1,913 | 94 | api-xpay rewrite/sentiment 240 / 371 at a listed $10 (suspicious); asterpay summarise 29 / 40 / $0.006; fact-check 31 / 49 / $0.015 | low | $0.01 | 2 / 2 | medium: needs a **commercial** LLM key (a personal Claude subscription must not be resold) |
| 3 | AI inference (LLM gateway) | 1,426 | 65 | blockrun.ai chat 300 / 86k / $0.002 | very high | $0.01 | 9 / 10 | low: thin-margin resale |
| 4 | Financial data / stock research | 1,372 | 116 | apitoll crypto price 66 / 182 / $0.001 | low | $0.01 | **33 / 105** ("ai stock research", "daily stock picks with thesis") | low: in India, stock picks and theses are regulated investment advice (SEBI). **Avoid.** |
| 5 | **Web extraction (URL → clean content)** | 1,074 | 92 | oneshotagent web-read 25 / 7,282 / $0.002; clawfetch 21 / 2,968 / $0.001; cyberwarex 19 / 375 / $0.001; pocket web-extract 12 / 1,005 / $0.005 | **high (291× and 141×)** | $0.008 (clears at $0.001–0.002) | 4 / 6 ("extract main webpage content as clean markdown or structured json from a url") | **highest: already built** |
| 6 | **x402 trust / seller due diligence** | 979 | 71 | agents-trust.com recommend 55 / 12,428 / $0.01; oblique bazaar-pulse 31 / 36 / $0.002; minifetch preflight 25 / 107 / $0.001 | high (226×) | $0.008 | **21 / 53** ("check an x402 endpoint before paying it", "is this x402 seller safe to pay", "x402 seller reputation unique payers") | **high: the radar is this dataset** |
| 7 | Weather / geo | 897 | 83 | x402tap weather 32 / 49 / $0.01 | low | $0.005 | 10 / 21 ("weather": 230 searches, 9 IPs) | medium: BUCS has a weather module, but Open-Meteo's free tier is non-commercial |
| 8 | Web search | 780 | 29 | stableenrich Exa 161 / 30.6k / $0.01; exa.ai 98 / 8.9k / $0.007 | high | $0.02 | 1 / 1 | low: resale of Exa / Firecrawl; no own index |
| 9 | **Domain intelligence (DNS/MX/SPF/DMARC/RDAP/WHOIS, email auth)** | 599 | 50 | pocket dns-whois-rdap 16 / 2,822 / $0.005; netintel email-auth 14 / 728 / $0.002; quartermaster dns 12 / 69 / $0.001 | high (176×) | $0.006 | **23 / 50** ("dns mx txt a records domain lookup", "domain age rdap whois expiry", "email validation mx spf dmarc") | **high: free public protocols** |
| 10 | Social search (X / Reddit) | 526 | 30 | glim twitter 38 / 679; x402atlas 33 / 563 / $0.005; twit.sh 30 / 3,371 / $0.006 | medium | $0.0075 | — | low: needs paid X / Reddit API access |

**Next niches by demand:**
- **People / company enrichment** (475): stableenrich PDL 46 payers at **$0.28**; FullEnrich 46 payers at $0.15. Agents searched "company enrichment firmographics" and "company identity legal entity enrichment structured json".
- **Wallet screening** (454).
- **SEC filings** (370): edgar.apitoll 29 payers at $0.003.

**Per-opportunity unit economics (BUCS's own estimates):**

| Niche | Technical requirement | Cost per request | Price | Margin | Difficulty | Acquisition channel |
|---|---|---|---|---|---|---|
| Web extraction | built (`commerce/executors.py`) | <$0.0001 compute + bandwidth | $0.002–0.003 | 95%+ before facilitator fee | low | Bazaar, nohumans, PayAPI, MCP |
| x402 trust | radar snapshot + a lookup executor | <$0.0001 (served from the snapshot) | $0.003–0.005 | ~99% | low–medium | Bazaar + nohumans: the searchers are x402 agents |
| Domain intelligence | dnspython + RDAP bootstrap (IANA) | <$0.0001 | $0.002–0.005 | ~99% | low | same channels |
| SEC filings | SEC EDGAR API (free; needs a contact User-Agent; 10 req/s limit) | <$0.0001 | $0.003 | ~99% | low | same |
| Company profile from website | extraction + parsing (no licensed data) | <$0.0002 | $0.01–0.02 (vs $0.15–0.28 for licensed enrichment) | 98% | medium | same, plus "company enrichment" searchers |
| AI text | commercial API key (e.g. Claude Haiku) | ~$0.0005–0.002 | $0.006–0.015 | 60–90% | low | same; crowded |
| Rest of the top 10 | — | — | — | — | — | **not recommended** (fit, licensing or regulation as noted in the table above) |

**Facilitator fees** are the real cost at these prices. The CDP facilitator charges per settlement beyond its free monthly allowance; **check the current CDP pricing before choosing a price**. At $0.002, a fee near $0.001 would halve the margin.

## B. The 20 most relevant competitors

**Scope:**
- Niches BUCS can serve (fit ≥ 0.4).
- One row per seller service, showing its best endpoint.
- ≥2 independent payers, farms excluded.
- Source: CDP Bazaar (30 d, Coinbase-settled). All are on Base; settlement verified by the Coinbase facilitator.
- **BUCS score** (0–100) = demand (log of unique payers) × repeat usage × BUCS fit.
- **Listed in:** nh = nohumans, xl = x402-list, pa = PayAPI.
- **On-chain 24 h:** distinct payers to the seller wallet (all its services).
- "n/a" = not measured (outside the 20 on-chain checks). Nothing is inferred.

| # | Service · endpoint | Niche | Price | Calls 30d | Unique payers 30d | Calls/payer | Listed in | On-chain 24 h | BUCS |
|---|---|---|---|---|---|---|---|---|---|
| 1 | agents-trust.com /recommend | x402 trust | $0.010 | 12,428 | 55 | 226 | nh, xl | 27 | 75 |
| 2 | win.oneshotagent.com /v1/tools/web-read | web extraction | $0.002 | 7,282 | 25 | 291 | nh, xl | 9 | 73 |
| 3 | api.clawfetch.ai /fetch | web extraction | $0.001 | 2,998 | 21 | 141 | nh, xl | n/a | 71 |
| 4 | web.cyberwarex.com /fetch | web extraction | $0.001 | 375 | 19 | 20 | nh, xl | n/a | 69 |
| 5 | agent.pocket.network /agentsearch-web-extract | web extraction | $0.005 | 1,005 | 12 | 84 | nh, xl | 57 | 64 |
| 6 | serp402.com /api/v1/search | web extraction | $0.050 | 374 | 10 | 37 | nh, xl | n/a | 62 |
| 7 | agent.pocket.network /dns-whois-rdap | domain intel | $0.005 | 2,822 | 16 | 176 | nh, xl | 57 | 61 |
| 8 | x402.clashofcoins.com /cheap-health | x402 trust | $0.001 | 301 | 16 | 19 | nh, xl | n/a | 59 |
| 9 | netintel.dev /email-auth | domain intel | $0.002 | 851 | 14 | 61 | nh, pa, xl | n/a | 59 |
| 10 | nanoparse.app /fetch | web extraction | $0.005 | 182 | 7 | 26 | nh, xl | n/a | 57 |
| 11 | 2s.io /api/search/ai | web extraction | $0.045 | 167 | 6 | 28 | nh, xl | n/a | 56 |
| 12 | agi.apify.com x402 prepaid tokens | web extraction | $1.00 | 154 | 40 | 4 | nh, xl | n/a | 55 |
| 13 | x402.browserbase.com /fetch | web extraction | $0.010 | 160 | 5 | 32 | nh, xl | n/a | 54 |
| 14 | stablesocial.dev (Instagram) | web extraction | $0.060 | 248 | 5 | 50 | nh, xl | n/a | 54 |
| 15 | sovereign-x402 /web/scrape | web extraction | $0.010 | 119 | 7 | 17 | nh | n/a | 53 |
| 16 | api.agentstools.dev /search | web extraction | $0.003 | 159 | 10 | 16 | nh, xl | n/a | 52 |
| 17 | agent402.tools /api/render | web extraction | $0.010 | 407 | 7 | 58 | nh, pa, xl | 26 | 50 |
| 18 | stableenrich.dev FullEnrich people-search | enrichment | $0.150 | 2,027 | 46 | 44 | nh, xl | 40 | 48 |
| 19 | agent.pocket.network /sec-holdings | SEC filings | $0.005 | 5,083 | 9 | 565 | nh, xl | 57 | 48 |
| 20 | vaaya.ai Firecrawl scrape | web extraction | $0.010 | 476 | 3 | 159 | nh, xl | n/a | 48 |

**Not available from any source:** competitors' revenue (only EST.), their customer identities, settlement outside the Coinbase facilitator, and refund rates. The live table is in the dashboard; hover a row for the evidence.

## C. The first three BUCS services

1. **Website Extraction**: `website.extract`, already built and live behind x402.
   - **Why:**
     - The niche with BUCS's highest fit.
     - 92 sellers have ≥5 buyers.
     - The leaders show very strong repeat buying (oneshotagent: 291 calls per payer).
     - Agents literally search for "extract main webpage content as clean markdown or structured json from a url".
   - **Position:**
     - **$0.002**, matching the leader. Set it in the dashboard (x402 price field).
     - Structured JSON plus clean text plus a quality score.
     - **A failed extraction is never charged.** Verify comes first and settle only after delivery, while competitors that return a 403 page still charge.
   - **Quality on 17 real sites:**
     - 12 succeeded.
     - 4 refused bots with HTTP 403 (Freshworks, Product Hunt, Infosys, Swiggy). These now return `SITE_REFUSED` and are not charged.
     - 1 was JavaScript-only (`NO_STATIC_CONTENT`, not charged).
     - Median latency 588 ms; about 0.27 s of CPU.
2. **x402 Seller Due Diligence** ("is this endpoint real, reliable and safe to pay?").
   - **Why:**
     - The clearest *unserved-by-BUCS-elsewhere* match.
     - Agents ask exactly this (21 terms, 53 IPs).
     - The leader has 55 payers making 226 calls each.
     - BUCS already computes the evidence nobody else combines: Bazaar unique payers, on-chain payers and repeats, directory probe status, **listing-farm detection** and price sanity.
   - **Build:** a new executor that answers from the latest radar snapshot. Target $0.003–0.005.
   - **Check first:** confirm that the Coinbase CDP terms allow serving derived data. nohumans and x402-list data are CC BY 4.0 and need attribution.
3. **Domain Intelligence** (DNS A/MX/TXT, SPF, DMARC, RDAP registration age, mail-auth grade).
   - **Why:**
     - 50 sellers have ≥5 buyers; the leader has 176 calls per payer.
     - Agents search for it repeatedly (23 terms, 50 IPs).
     - It uses free public protocols and is cheap to run.
   - **Build:** dnspython plus the RDAP bootstrap. Target $0.002–0.005.

**Deliberately not first:**
- Stock research: regulated advice in India.
- Web search and LLM inference: resale with thin margins.
- Enrichment: needs licensed data. The website-derived **company profile** is the fourth candidate, at $0.01–0.02 against $0.15–0.28 for licensed enrichment.

## D. Customer acquisition: reaching agents that already pay

The buyers above find services in **four places**. BUCS should be in all four, in this order:

1. **Coinbase x402 Bazaar.**
   - Every buyer measured in this report was counted there. It is where x402 clients auto-discover services.
   - Listing happens when payments settle through the **CDP facilitator** with discovery metadata, which needs CDP API keys on Base mainnet.
   - This is the single most important channel.
2. **nohumans.directory.**
   - 3,130 client IPs ran 24,441 searches in 30 days.
   - Write the listing text in the **searchers' own words**: "extract main webpage content as clean markdown or structured json from a url" and "clean markdown from any url for ai agents"; for service 3, "dns mx txt a records domain lookup" and "email validation mx spf dmarc".
   - Its probes then mark the listing verified.
3. **PayAPI Market.**
   - Use the dashboard's prepared payload (`GET /api/commerce/admin/listings/payapi-draft`).
   - PayAPI makes one real paid test call. **Label their wallet `marketplace_test`** so that purchase is never counted as a customer.
4. **agents-trust.com.**
   - Agents pay it to *recommend* an API (55 paying agents). Being in its index puts BUCS in front of buyers at the moment they choose.
   - Also list on x402-list.com, which is free.

**How to win existing paying agents without spamming anyone** (there are no contact details for agent wallets, and they should not be sought):
- Match the incumbents' price ($0.002) and beat them on reliability.
- Publish a public quality page with the real success rate and median latency from the radar's own probes.
- State the **"not charged on failure"** guarantee in the 402 terms and in the listing description.
- Offer the MCP server (`/commerce/mcp`), so MCP-native agents get BUCS as a tool.
- Watch the radar weekly:
  - BUCS's own endpoint appears in the Bazaar with unique payers and calls.
  - The ledger shows **external** wallets separately from internal tests, marketplace tests and self-transfers.
  - Repeat buyers are the success metric.

### The concrete path to the first genuine paying customer

1. **Owner:** create a Base wallet you control. Put **only its public address** in `.env` as `X402_PAY_TO`; never a key or seed.
2. **Owner:** choose public hosting. PayAPI refuses tunnels and localhost, so use your own domain on a small server or a named tunnel on your domain. Set `X402_PUBLIC_BASE_URL`.
3. **Test on Base Sepolia** (the default: `X402_NETWORK=eip155:84532`, facilitator x402.org):
   - Buy one extraction with your own test wallet and list that wallet in `X402_INTERNAL_WALLETS`.
   - Watch it land in the ledger as **internal test**, never as a customer.
4. **Owner decision:** switch to mainnet (`eip155:8453`) with a mainnet facilitator (CDP, which puts the service in the Bazaar).
   - First take advice on crypto receipts: in India, VDA income has tax and reporting implications.
5. **Owner approval for each:** submit the nohumans, PayAPI and x402-list listings. Record each with `POST /api/commerce/admin/listings`.
6. **First genuine customer** = the first `external` wallet in the dashboard's x402 panel. To confirm it:
   - It is not your wallet and not a marketplace verifier.
   - Its tx hash is visible on BaseScan.
   - The result was delivered: the order is `delivered`, with a settle performed only after delivery.
7. **Then optimise for repeat:**
   - Watch calls per payer for BUCS's endpoint in the radar.
   - Fix whatever fails, starting with `SITE_REFUSED` sites; a headless-render fallback is the next quality step.
   - Add services 2 and 3.

Razorpay remains **unconfigured, in test mode**. Nothing in this plan changes that.

## E. The smallest technical changes (built in this release)

| Change | Where |
|---|---|
| Market radar collectors: retries, polite pacing, honest user agent, CC BY attribution | `commerce/radar.py` |
| Listing-farm and single-payer filtering; seller-level niche demand; competitor scoring | `commerce/radar.py` `analyse()` |
| Radar section inside the existing Trillion card (no new dashboard) | `static/index.html`, `app.js`, `style.css` |
| Owner-only radar API: latest snapshot; refresh at most once per 30 min, read-only | `GET /api/commerce/admin/radar`, `POST …/radar/refresh` |
| Revenue Opportunity Pipeline: save, edit unit economics (labelled estimates), approve/reject, convert to a **draft** service (never sold until it has a tested executor), track listings | `commerce/pipeline.py`, `/api/commerce/admin/pipeline*`, `/listings` |
| Additive migration with a database backup first (`.update/backups/*-pre-pipeline.db`) | `pipeline.migrate()` |
| x402 seller: verify → deliver → settle only on success; USDC ledger kept apart from INR | `commerce/x402.py`, `/x402/v1/{service}` |
| Payer classes: external, internal_test, marketplace_test, self_transfer. Read-time classification; payments are never rewritten | `ledger.buyer_stats()`, `/api/commerce/admin/x402/wallet-label` |
| Owner-set x402 price | `/api/commerce/admin/x402/price` and the dashboard field |
| PayAPI listing payload + blocker check (never submits) | `/api/commerce/admin/listings/payapi-draft` |
| MCP discovery + `/.well-known/x402` manifest | `/commerce/mcp` |
| Extraction quality: structured error codes, no charge for 4xx block pages or JS-only pages, clean text, quality score | `commerce/executors.py` |
| **DNS-rebinding fix:** the vetted IP is the one connected to | `executors._PinnedBackend` |

**Next smallest changes (not built yet):**
1. Executor for x402 Seller Due Diligence (service 2).
2. Domain Intelligence executor (service 3).
3. Bazaar discovery metadata in the 402 response, needed for CDP listing.
4. A robots.txt check before extraction (good citizenship; `minifetch` sells exactly this).

*Data: nohumans.directory (https://nohumans.directory) and x402-list.com, both CC BY 4.0. On-chain totals: agenteconomy.to (public Dune queries).*
