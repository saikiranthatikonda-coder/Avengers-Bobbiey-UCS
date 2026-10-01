# Changelog

Meaningful changes to Bobbiey UCS, newest first. Entries before September 2026
were reconstructed from git history. See `git log` for full detail.

## [Unreleased]

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
