# Changelog

Meaningful changes to Bobbiey UCS, newest first. Entries before September 2026
were reconstructed from git history. See `git log` for full detail.

## [Unreleased]

## 2026-10-29 — v7.5.1 · Header layout
- **ui:** the version/update pill and the ⏻ shutdown button moved into the empty space between the AVENGERS title and the UNIFIED COMMAND SYSTEM banner, centred in that gap (measured: 40/42 px clearance at 1366 px, 158/127 px at 1920 px).

## 2026-10-29 — v7.5.0 · One-click shutdown + signed in-app updates
- **feat:** **⏻ shutdown** in the dashboard header (and on the activation screen for locked installs). It confirms, then stops BUCS the same way Ctrl+C does (agents, voice, schedules and TTS shut down through the lifespan), and the launcher window closes by itself. Host machine only.
- **feat:** **in-app updates.** A `vX.Y.Z` pill in the header turns into **UPDATE → vX.Y.Z** when a newer signed release is published (checked at boot and every 12 h, or on demand). **UPDATE & RESTART** downloads the package, verifies the owner's Ed25519 signature and SHA-256, backs up every file it replaces, never touches user data (licence, settings, memory, tokens), and restarts. If the new version fails to start, the launcher **rolls back automatically**.
- **feat:** `tools/release.py` publishes a release: it builds the package from the committed version (`git archive`, app files only, ~440 KB), signs the manifest, and writes `docs/releases.json` + `docs/releases/`. `VERSION` file added (7.5.0); the footer now shows the real version.
- **fix (caught by E2E):** `BUCS_UPDATE_URL` was read before `.env` loaded, so it was ignored. It is now read when the updater starts.
- **verified end to end** with a separate install run by the real `run.ps1`: 7.5.0 → 7.5.1 (back in 6 s, `.env` and licence kept), a deliberately broken 7.5.2 rolled back to 7.5.1 in 3.5 s, and dashboard shutdown exited the launcher with code 99.
- **test:** `tests/test_updater.py` (signature/tamper, version order, package install keeps user data + backs up, checksum and path-traversal refusal, git checkout untouched, rollback disarm, real server shutdown).

## 2026-10-29 — First-customer install guide
- **docs:** one-page A4 **BUCS Install & Activate** guide (`docs/BUCS-Install-Guide.pdf`) with real screenshots: download, Python + PATH, `Start BUCS.cmd` (incl. the SmartScreen "More info → Run anyway" step and the `Avengers-Bobbiey-UCS-main` folder name), install ID, UPI payment, activation, troubleshooting, and what's included. It uses an example install ID, never a real one. Linked from the site's Get Started section; regenerate with `tools/guide/`.

## 2026-10-28 — Privacy: QR-only payments; official India on the world map; alignment
- **privacy:** the website no longer publishes the UPI ID or payee name as text (they were in the public `payments.json` and the page). It now shows only pre-generated, amount-filled QR images per plan (`docs/pay/`, made by `tools/make_upi_qr.py`, which takes the UPI ID on the command line so it is never stored in the repo). The "open UPI app" link is gone because it carried the ID in plain text. A test blocks any UPI ID, payee name or `upi://` link from the site.
- **fix:** the world map drew India from Natural Earth's de-facto line. India is now drawn from the official boundary (same dataset as the India panel, dissolved to lon/lat: `static/geo/india_world.json`, 16 KB), and neighbours are masked underneath so no de-facto border shows through. The map now fits the panel's real shape and the inhabited band (56°S–74°N) instead of a fixed box with empty side bars. Countries show names on hover.
- **fix:** location rows split words mid-word ("SERILINGAMPALL / Y") and cut the network name at 38 characters. Rows now wrap only at spaces, with tighter letter spacing and the full network name (IP on hover). Checked at 290/260/220 px column widths and with an automated whole-page audit for split words and clipped text.
- **fix:** OPEN LLM SWITCH (and other text buttons) spun 120° on hover; only single-glyph icon buttons rotate now.

## 2026-10-28 — Live launch: install path replaces early access
- **site:** early access / waitlist removed (the product is live). New **GET STARTED** section with a download button (GitHub ZIP), requirements (Windows 10/11, Python 3.11+), and five steps from install → activation screen (install ID) → UPI payment → key → ACTIVATE. The hero and nav point to it; the pricing form links back to it for anyone without an install ID.
- **feat:** `Start BUCS.cmd` for customers. The first launch checks for Python 3.11+ (opens python.org if missing), creates the environment, installs dependencies (cleans up and explains on failure), copies `.env`, and adds a **BUCS** desktop shortcut. It no longer stops to ask for a NewsAPI key.
- **fix:** the launcher only opened the browser once `/api/status` answered, which is 402 on a locked customer install, so the activation screen never opened by itself. It now waits on `/api/license` (up to the full first-setup time).
- **fix:** "days left" rounds up, so a key that is still valid never shows "0 days".
- **verified end to end:** a copy built from exactly the downloadable files (no private files) ran as a customer, opened the activation screen with its install ID, accepted a key signed with the owner key and bound to that ID, and unlocked the dashboard and API.

## 2026-10-28 — Two plans, live UPI payments, right-column fit, Web3 hidden
- **pricing:** exactly two plans, ₹149 for 1 month and ₹1,499 for 12 months (billing.py plans = payments.json = website, enforced by a test). The issue form offers the same two terms.
- **payments live:** UPI ID decoded from the operator's PhonePe QR, plus payee name and contact email in `docs/payments.json`. The website draws an amount-filled UPI QR per plan and shows the UPI ID masked (it contains a phone number).
- **fix:** agenda and world feed were squashed to one line when the right column became fixed-height. Each block now has a real share: agenda/news grow from 150 px, inbox 76 px, vision digest 84 px (scrolls), webcam 16:9, schedule summary 2 lines. Measured: it fits 1170 px with real Google data, with no overflow.
- **fix:** the Web3 widgets now stay hidden until the module is enabled (CSS `display:grid` was overriding `[hidden]`).
- **fix:** boot jobs (first Google sync, briefings) no longer get silently skipped when the scheduler starts later than their 6-second slot. Google now syncs within seconds of boot instead of after 5 minutes.

## 2026-10-28 — One paid plan over UPI, accurate maps, new cards, layout fixes
- **feat:** **One plan, pay to use.** BUCS Pro ₹1,499/month (3 mo ₹4,497 · 12 mo ₹17,988). Customer installs stay locked until a key is activated: the API returns 402, pages redirect to a new `/activate` screen showing the install ID, the websocket is refused, and agents/voice/schedules don't start. Activation brings every system online instantly, with no restart.
- **feat:** **Owner-signed licence keys** (Ed25519, verified offline, no third party, no fees). Each key carries the customer, an expiry and an optional install-ID binding. LICENSE → **ISSUE KEY** on the owner machine signs a key, logs it, and offers copy and a pre-filled email. Forged, tampered or expired keys and keys for another install are refused. LemonSqueezy removed.
- **site:** a single plan card plus a **UPI payment panel**: scannable QR for the chosen term, an open-UPI-app link, and a "send payment details" form (email + UTR + install ID → your inbox, 0% fees). It shows "opening soon" until `docs/payments.json` has a UPI ID. The waitlist form falls back to email on GitHub Pages.
- **feat:** **AI BRAIN ROUTER** card (which route answered, share, last latency, one-click provider switch) and **MARKET WATCH** (live BTC/ETH/SOL/POL in USD + INR from CoinGecko, cached 2 min). They fill the empty grid slots beside Web3 Command Center and Community & Events.
- **fix:** India and Telangana maps rebuilt from real boundary data: official India boundary with 36 states/UTs and state lines, and Telangana with its 33 districts. Hover shows names.
- **fix:** the centre column no longer stretches or leaves gaps. It alone sets the row height (side columns scroll internally); the process/traffic row fills between 190 and 280 px; the event stream stays at 190 px.
- **fix:** LLM switch buttons (TEST clipped, pen icon spinning), empty boxes from CSS overriding `hidden`, and truncated map titles. Removed `say "hey <agent>"` from the orb on standby.
- **test:** owner-signed key suite (issue, forge/tamper, binding, expiry, public key matches the owner key), and a customer-mode server (402, /activate, forged key, websocket refused). 136 tests.

## 2026-10-26 — LLM switch, real licensing, interactive sections, website checkout
- **feat:** **LLM SWITCH** (click the LLM row / AI MODEL tile). Choose Auto, Claude, Ollama or **any OpenAI-compatible model** (presets: OpenAI, Anthropic, Gemini, Groq, OpenRouter, DeepSeek, Mistral, Together, LM Studio, custom). Each one gets **SAVE + TEST** (a real round-trip with measured latency), USE, edit and remove. Keys are stored locally and only ever shown masked. Failures fall back to the original chain.
- **feat:** **Licensing** (`licensing.py`). The owner machine (`BUCS_OWNER=1`) has everything unlocked. Customer installs run Community until a LemonSqueezy licence key is activated in **LICENSE → ACTIVATE**. Cloud brains, Google sync and engineering mode are enforced server-side. Validation runs daily with a 7-day offline grace, plus re-verify and release-seat actions. The demo edition switch is now owner-only.
- **feat:** **Every section is interactive.** All 53 section titles open an info panel: what the section is, the live values read from its real endpoint, and the data source. Nothing is invented.
- **site:** the pricing buttons read `docs/payments.json`: a LemonSqueezy checkout URL makes the plan a real BUY button with overlay checkout, and an empty one stays an honest waitlist button. Added how-buying-works steps, a bring-any-model strip, an animated featured card, and removed the "every edition is live" claim.
- **ui:** event stream fixed at 190 px; the process/traffic row takes the remaining height (shows up to 12 processes).
- **test:** `tests/test_licensing_providers.py` (12), site checkout guards; billing smoke test now asserts that customers cannot mint a paid edition.

## 2026-10-23 — Commander Memory (long-form private profile)
- **fix:** operator memory silently truncated profiles: UI input 200 chars, server 240 chars per fact, only 40 facts kept. A ~25 KB memory protocol was cut to ~200 chars.
- **feat:** `commander_memory.py` stores a full profile (paste/edit or upload .md/.txt, up to 300,000 chars) **locally only** (`commander_memory.md`: gitignored, protected from engineering-mode edits). It is split into sections; every agent request gets an always-on core identity plus the most relevant sections (dependency-free BM25-lite, title-aware, **rule sections weighted ×2** so "never reset production data" outranks descriptions). Engineering-mode diagnoses also receive the operator's rules.
- **ui:** COMMANDER PROFILE block in Operator Memory (status, editor with replace/append/clear, upload). Facts now 500 chars × 100.
- **verified:** a real 7,901-char profile became 25 sections. JARVIS answered "how should you address me", refused dropping production tables citing the operator's rules, and listed accelerator startups, all from the profile.
- **test:** `tests/test_commander_memory.py` (synthetic profile).

## 2026-10-23 — Event stream no longer grows the page
- **fix:** the previous fill fix used flex-basis auto, so every new log line enlarged the panel and the page kept extending. The stream now takes only the leftover height (flex-basis 0 + contain: size) and scrolls internally; 260 px fixed on single-column layouts. Verified: +300 lines → panel/page height +0 px.

## 2026-10-23 — Gyroscope alignment, centre-column fill, links card
- **fix:** orb gyroscope rings used three unrelated tilts (one off-axis rotateY), so they wandered around the globe. They are now concentric (measured ≤0.08 px centre offset through the spin), share one tilt, and sit at 0°/60°/120°.
- **fix:** empty frame below Process Monitor / Network Traffic. The centre panel stretches to the tallest column but the event stream was capped at 220 px; it now fills the column (capped only in single-column layouts).
- **ui:** website link moved out of the UNIFIED COMMAND SYSTEM banner into a **BUCS · ON THE WEB** card (website, GitHub Pages mirror, source).

## 2026-10-23 — Google API enablement guidance + header fix
- **fix:** a Google Cloud project with Calendar/Gmail/Tasks switched off produced a raw 403 dump. BUCS now recognises "API not enabled" per service, shows an **Enable Google APIs** panel on G with one-click links for the exact project and a Re-check & Sync button, and clears each issue as soon as that API works.
- **fix:** header items overflowed onto the UNIFIED COMMAND SYSTEM banner below ~1600 px (the right-hand group needs ~650 px). It now wraps into two right-aligned rows (29 px clearance at 1280).

## 2026-10-23 — Engineering mode, Google setup in the dashboard, digest fix, 3D layer
- **feat:** **JARVIS Engineering mode** (`devagent.py`). Say or type an issue, and JARVIS diagnoses BUCS's own code read-only with live diagnostics, proposes a fix, and applies it only on approval (edit tools, **no shell**), runs the tests, then offers Keep / Undo (reverts only its own changes) / Ship (commit + push). Secrets are restored if touched. Commander-only, audited. Dashboard ENGINEERING panel streams what it reads.
- **feat:** **G button guided setup.** Without `credentials.json` it opens a 6-step in-dashboard guide with direct Google Cloud links, accepts the downloaded OAuth client file (validated: rejects service-account keys and junk; stored locally, gitignored), then signs in.
- **fix:** Vision Digest was only written by the 08:30 briefing over the websocket, so it was empty all day and after reloads. Now a continuous real-data synthesis every 3 min, served at `/api/digest`.
- **ui:** removed LIVE COMMANDS and VOICE COMMAND REFERENCE. Added 3D orb gyroscope rings (state-tinted, mic-reactive) and pointer-tilt card depth (no layout shift; off for touch / reduced motion).
- **test:** `tests/test_devagent.py` (approval gate, undo isolation, secret restore, ship gate, routing, client-file validation).

## 2026-10-05 — Orb lights up while listening
- **feat:** the voice orb glows while BUCS hears you. Halo, ring glow and core brightness scale with the **live mic level** (measured: halo 22 → 76 px, brightness 1.10 → 1.64 from quiet to loud speech). A steady cyan **engaged** glow shows when JARVIS is waiting for your follow-up (voice.py broadcasts the window). Thinking and speaking glow in their colours. Respects reduced motion.

## 2026-10-05 — Conversation Mode + voice that hears the operator
- **fix (root cause):** background agent speech (13 utterances / 2 min) kept the mic muted **62% of the time**, discarding the operator's words. **Conversation Mode** (default on, header switch, `/api/conversation`, or by voice) voices only replies to the operator (`tts.say(channel="conversation")`), keeps ambient speech on screen, talks to one agent (JARVIS), and replies in plain spoken style. Markdown is never read aloud.
- **fix:** Whisper hallucinations and other people's chatter. Low-confidence segments are dropped (no-speech / logprob / repetition), temperature re-decodes removed (one decode took 14 s), phantom phrases ("Thank you.") ignored. In Conversation Mode "Jarvis" anywhere starts a turn, follow-ups need no name for 20 s, and unaddressed speech is ignored.
- **fix:** the mic loop no longer waits on transcription (worker queue; stale phrases dropped), which removed stalls and bursty late replies.
- **feat:** barge-in by speaking over JARVIS (loud-speech gate above his learned echo), live mic meter, `/api/voice/diagnostics` (every utterance: text, peak, gain, decode ms, outcome).
- **test:** 10 new (conversation mode, routing, confidence, phantoms, source hygiene). 104 total.

## 2026-10-05 — Real-Time Interaction & Action Engine (foundational)
- **feat:** `realtime.py` is one pipeline for voice, typed, UI, API and clap commands: received → instant ack → fast-path or delegated agent → streamed reasoning → sentence-by-sentence speech → done/failed/cancelled, broadcast live with timings. `/api/command` (non-blocking), `/api/command/cancel`, `/api/engine` (p50/p95 latency). `/api/ask` keeps its contract and now runs on the engine.
- **feat:** `intents.py` fast path, no LLM, 0–2 ms: time/date, CPU/memory/disk/battery, internet speed/latency (+ "run a speed test" with instant ack and progress), weather, news, threats, calendar, inbox, memory, vision, missions, dashboard sections, named websites, and **Windows actions** (`win_actions.py` allow-list: apps, folders, volume/media keys, lock), all audited.
- **feat:** streaming brain. Claude `stream-json` (first token ~1.9 s; stops reading at message end, cutting ~2 s from every call incl. existing ones: 5 s → 3 s) and Ollama SSE. Skips MCP connector start-up (`--strict-mcp-config`).
- **feat:** persistent Windows speaker (~1.4 s vs 2.8 s per utterance), `cancel()` barge-in in ~10 ms, real playback-start metric. New operator commands supersede old speech.
- **feat:** interruption. "stop / cancel / never mind" (no wake word needed while busy), dashboard ■ STOP / Esc / per-command ×, double clap while JARVIS speaks. Cancelling kills the CLI subprocess.
- **feat:** delegation + handoff events (JARVIS → Captain/Stark/Hulk/Widow/Hawkeye/Thor/Vision), shared conversation context across voice and text, LIVE COMMANDS console with stage chips, streaming text, timings and cancel; the summon card streams replies.
- **test:** `tests/test_engine.py` (22) + engine smoke tests. 94 total.

## 2026-10-01 — Truthful command center, real network, open news, voice + clap
- **fix (honesty):** the six header tool cards, their pop-up panels and the orb flanks showed **hardcoded fake data** ("14 SATS · 87% COV", "Project Helios" mission files, invented security logs, "1,247 calls / 1.2M tokens", "AES-256", "SEC-7", "14 SAT · 6 NODE"). All are now bound to live endpoints: AI diagnostics, **Fleet Uplink**, threat engine, **Mission Board** (orchestrator directives), audit trail, network. SHIELD says HIGH RISK, not "BREACH".
- **feat:** `netspeed.py` real network truth. Latency is a TCP round trip (was a full HTTPS timing pinned at "999"), rolling packet loss, and a real internet speed test (Cloudflare, adaptive payload, every 60 min + on-demand button). The NETWORK tile shows measured Mbps instead of the Wi-Fi PHY rate.
- **feat:** `open_news.py` keyless news. BBC World, The Hindu, Al Jazeera, The Hacker News RSS + Hacker News API, merged/deduped/interleaved, every 10 min, with article age. NewsAPI is now optional.
- **feat:** double-clap summons JARVIS (pop-up card, spoken "Yes, sir? I'm listening.", 15 s no-wake-word window).
- **fix (voice):** adaptive trigger level (a fixed 300 RMS clipped quiet speech), auto-gain before Whisper, beam-5 decoding with a short hint + hotwords (the long prompt invited hallucinations), 1.0 s end-of-speech, 12 s conversation mode after answers, and the dashboard now shows transcripts that had no wake word.
- **fix (UI):** orb ring values no longer overlap the rings. Phone layout no longer forces 587 px (map row, bottom strip, tile grid, card subtitles). `prefers-reduced-motion` support. Website link in the header banner.
- **fix:** agents no longer announce "CPU 0%, memory 0%" before the first telemetry sample.
- **test:** `tests/test_realtime.py` (clap detector, conversation mode, network loss/RTT, RSS parsing, news fallback). 69 tests.

## 2026-09-29 — Google one-sign-in upgrade + GitHub Pages
- **feat:** one G sign-in now covers Calendar, Gmail **and Google Tasks**. Sync reads every selected calendar (not just primary) with join links (Meet/Zoom/Teams), organizer, RSVP state, guest count and calendar name. The dashboard shows JOIN buttons, RSVP/calendar tags, declined meetings dimmed, and open/overdue tasks.
- **fix:** clicking G while signed in deleted the saved login and forced a new consent. It now just re-syncs. Tokens refresh with their own granted scopes.
- **ci:** `.github/workflows/pages.yml` publishes `docs/` to GitHub Pages on every push (build-stamped). `.github/workflows/tests.yml` runs the suite on every push.
- **test:** `tests/test_google.py` (8 tests, fake Google API). 57 total.

## 2026-09-29 — Voice, orchestrator fix, website, commercialization
- **fix:** the orchestrator tick crashed on every cycle when no calendar meeting existed (`f"{None:.0f}"` in an inactive rule), so Phase 2 delegation never ran on machines without Google. 3 real-tick tests.
- **feat (setup):** voice restored. faster-whisper 1.1.0 + sounddevice + numpy in the existing venv, `base` model. Wake-word/intent tests added.
- **site:** `docs/` is the canonical website (newer 4-edition pricing, phases 1–5 shipped). `site/` re-synced. Root `netlify.toml` enables Git-based deploys. Removed a "live subscription" overclaim. `tests/test_site.py` guards mirror, prices and claims.
- **docs:** `SAAS_PLAN.md` rewritten as a code-grounded commercialization plan (sellable matrix, billing gap, Web3 real vs demo, risks, path to revenue).

## 2026-09-29 — Local AI tier
- Installed Ollama 0.34.4 + `llama3.2:3b` on the dev laptop (machine setup, no repo change).
- **fix:** Claude-first routing. The latency fast path no longer lets a warm local model pre-empt Claude for agent/voice replies. Local keeps insights and failover. 4 routing tests added (36 total).

## 2026-09-28 — Test foundation + cleanup
- **fix:** switching the demo edition (`POST /api/billing/edition`) crashed with HTTP 500 (`.name` on a dict). The edition saved, but the audit entry and UI response were lost.
- **test:** `tests/test_smoke.py` + `tests/harness.py` boot a real UCS server from an isolated temp copy (no `.env`, no state files, no credentials). 32 tests total.
- **docs:** old-machine references classified. MIGRATION/PROJECT_CONTEXT mark `C:\Users\sai` and "use 3.12" as historical. README no longer claims a "mock feed". The `google_sync.py` setup comment uses the project root.

## 2026-09-28 — Claude brain restored
- **fix:** UCS finds the native Claude CLI at `~\.local\bin` when it isn't on PATH. The brain is live again (`brain_mode: llm`).
- **fix:** CLI calls run from a neutral temp dir, so the developer `CLAUDE.md` no longer leaks into agent replies.
- **fix:** text replies run tool-less (`--tools ""`): no blocked shell attempts, 40 s → ~12 s. Vision is limited to the Read tool.
- **feat:** agents receive a measured live-telemetry line, so they quote real CPU/memory figures instead of guessing.
- **test:** first test suite, `tests/test_brain.py` (stdlib unittest, 12 tests, optional live CLI check).
- **docs:** added `ROADMAP.md` and this `CHANGELOG.md`. `CLAUDE.md` updated with the ship-on-success git policy and the live dev loop.

## 2026-09-28 — Development environment restored
- Added `CLAUDE.md`, the operating manual for Claude Code on this repository (`0eef8e1`).
- Verified the repo on the new Windows machine (`C:\Users\Saikiran`, Python 3.11.9 venv).

## 2026-09-02 — Migration tooling
- Laptop-migration guide (`MIGRATION.md`), `PROJECT_CONTEXT.md` engineering record, `restore-data.ps1/.sh` (`bbc0bac`).

## 2026-07-24 → 07-27 — Phase 6 Ecosystem
- Monetization editions, AI credits, entitlements (demo billing) and the optional modular Web3 Command Center (`df0e540`).
- Web3 turned from mock-up into a working subsystem: real JSON-RPC, on-chain balances, persisted governance (`f2169f1`).
- Accurate Telangana boundary in the regional map (`12119c0`).

## 2026-07-21 — Fleet and launcher fixes
- One-command fleet join scripts (`a03ca45`). Node agent works on Python 3.7+ (`90c1cf5`).
- `run.ps1` honors `JARVIS_HOST` from `.env` (`55dd6cb`). Audio mute and volume control (`f3ab941`).

## 2026-07-09 → 07-15 — Phase 4 and 5 completion
- Multi-site fleet (`7b6601d`), short pairing codes (`864a2a0`), authenticated remote access (`c04367e`).
- On-prem AI clusters and fleet-wide autonomy (`f49e670`). Fixed the `app.js` TDZ bug that blanked half the dashboard (`d739474`).
- Data-accuracy pass: real geo, battery, uplink, throughput (`3737942`).

## 2026-07-08 — Phases 2–5 foundations
- Multi-Agent Intelligence (`22f59bb`, `abd344d`), Computer Vision Operations (`206803d`),
  Enterprise Command (`43f2686`), Autonomous Decision Support (`9f89af1`).
- Google OAuth `refresh_token` fix, persistent brain choice (`ef82112`, `10e9588`).

## 2026-07-06 → 07-07 — Vision, memory, real data
- Claude webcam vision (`c471645`), faster voice path (`9d1f6ad`), operator memory (`64f306e`).
- Removed all mock/seed data. Gmail bridge (`9ec24e7`).

## 2026-06-12 → 06-15 — v0.1.0
- Initial AI command dashboard: 8 agents, voice, vision, threat intel, insights, landing site (`56d8c66`).
- d3 Earth globe, Ollama model manager, cross-platform launchers, GitHub Pages copy (`977ac20`, `5255f9c`).
