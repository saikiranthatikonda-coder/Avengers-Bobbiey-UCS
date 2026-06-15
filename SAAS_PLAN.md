# BUCS → SaaS: Productization Plan

Status: v0.1.0 committed · landing live at https://avengers-bobbiey.netlify.app (pricing published)

## Positioning
"JARVIS-class AI command center for operators" — local-first, privacy-first,
cinematic. The local install IS the moat: competitors are cloud dashboards;
BUCS runs the AI on the customer's machine.

## Pricing (published)
| Tier | Price | Gets |
|---|---|---|
| OPERATOR | Free (beta) | full local console, 8 agents, voice, local AI |
| COMMANDER | $29/mo · $290/yr (founding, locked) | + Google integrations, Threat Intel, briefings, priority support |
| ENTERPRISE | Custom annual | multi-operator, on-prem AI cluster, SSO/audit, success engineer |

## Ship sequence
### Now → 30 days (private alpha)
- [x] Version control (git, v0.1.0)
- [x] Public site + waitlist + pricing
- [ ] Push repo to private GitHub (`gh repo create bobbiey/bucs --private`)
- [ ] Onboard 5–10 waitlist users manually (zip + start-jarvis.cmd works today)
- [ ] Add in-app feedback button → /api/feedback → jsonl
- [ ] Weekly founder email to waitlist (build-in-public)

### 30–90 days (paid beta)
- [ ] One-click installer: PyInstaller or Inno Setup bundling venv + models
- [ ] License keys: LemonSqueezy or Gumroad (handles VAT/GST; Stripe later)
- [ ] License check at boot (offline-tolerant, 7-day grace)
- [ ] Auto-update channel (simple version check endpoint + zip swap)
- [ ] Convert founding members at $29/mo locked

### 90–180 days (Phase 2 per ARCHITECTURE.md)
- [ ] Postgres + Redis behind existing seams; Next.js client off the WS contract
- [ ] Team accounts (Google OAuth login — consent screen → production status)
- [ ] LangGraph agent delegation (task_queue fields already shipped)

## Revenue checkpoints
10 paying = $290 MRR → validates · 100 = $2.9k MRR → ramen · 1,000 = $29k MRR → company

## Before charging money (legal/ops)
- Privacy policy + ToS pages on the site (camera/voice data stays local — say it loudly)
- Google OAuth verification (publish consent screen past "testing" mode)
- Business entity + payment rails (LemonSqueezy = merchant of record, easiest from India)
- Support channel: Discord (free) or shared inbox

## Metrics that matter
Waitlist→install rate · weekly active operators · voice commands/day ·
insight engagement · beta→paid conversion · churn
