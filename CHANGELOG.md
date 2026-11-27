# Changelog

Meaningful changes to Bobbiey UCS, newest first. Entries before September 2026
were reconstructed from git history. See `git log` for full detail.

## [Unreleased]

## 2026-11-27 — v8.9.0 · DOOM SENTINEL: intrusion watch, code integrity, injection shield, DEFCON
- **feat: DOOM SENTINEL** (`doomsentinel.py`, new DOOM panel). It is a live defence layer on top of the cyber analyst, the Privacy Shield and the DOOM GATE. Everything is measured on this machine.
  - **INTRUSION WATCH:** records every request the perimeter refuses:
    - unauthenticated remote API calls;
    - requests made while remote access is off;
    - cross-site and DNS-rebinding attempts stopped by the origin guard;
    - failed logins and locked-out sources.

    It keeps only the IP, the path without its query string, the reason and the time, in memory. It never keeps headers, cookies, tokens or bodies. It shows a 24-hour hourly chart, the top sources and a breakdown by kind. 12 refusals from one source within 5 minutes raise an **intrusion-burst alert** and an audit entry. The perimeter's decisions are unchanged: `main.py` only reports them through `_perimeter_event`.
  - **CODE INTEGRITY:**
    - The security-perimeter files are SHA-256 fingerprinted at start. A change on disk while BUCS runs is **TAMPER**: a critical alert plus an audit entry.
    - Every 5 minutes, `git status` lists code changed outside a commit and **new untracked executables or scripts** in the app folder.
    - Run it on demand with INTEGRITY CHECK. After reviewing a change, ACCEPT CURRENT CODE (commander only, audited) makes it the new reference.
  - **INJECTION SHIELD:** every block of dashboard facts handed to an agent is screened. Phrases matching any of 8 patterns are replaced with "[instruction removed by DOOM]" and counted per source:
    - override and role-hijack phrases;
    - jailbreak markers;
    - fake system tags;
    - prompt extraction;
    - data exfiltration;
    - piped shell one-liners and encoded PowerShell;
    - tool hijack.

    Only the pattern name and source are recorded, never the text.
  - **DEFCON 5 to 1:** one threat level from tamper, intrusion bursts, critical findings, the cyber-analyst score, code changed outside a commit, the refusal rate, injections and the privacy score. Every reason is listed.
- **DOOM, upgraded:**
  - DOOM's standing brief now starts with the Sentinel.
  - DOOM answers "what's our DEFCON?", "any intrusions?" and "is the code intact?" (the last one runs a live integrity check).
  - A new `sentinel` facts section is readable by every agent.
  - The DOOM GATE now treats `doomsentinel.py` as security-perimeter code: any self-written change to it needs your review.
- **API:** `GET /api/sentinel`, `POST /api/sentinel/scan`, `POST /api/sentinel/rebaseline` (both POSTs are commander only).
- **Website:** DOOM gains INTRUSION WATCH, CODE INTEGRITY, INJECTION SHIELD and DEFCON cards. The rule now reads "Sentinel watches · DOOM scans · BOB signs · you approve".
- **Dashboard:** voice/agent dashboard targets for `sentinel`, `india` and `telangana`.
- **Tests:** `tests/test_v89.py` (9), including an end-to-end check that a cross-site attack on the running server is refused and recorded.

## 2026-11-27 — v8.8.0 · Immersive INDIA, TELANGANA and DISASTER RESPONSE maps (and the first release of v8.7.0)
v8.7.0 below was finished but never published on its own. It ships in this release.
- **feat: immersive INDIA and TELANGANA maps.** The two side maps beside the global activity map are now full MapLibre satellite maps.
  - They have the same nine imagery modes as the global map: SATELLITE, HYBRID, 3D TERRAIN, 3D CITY, NIGHT, TODAY, TOPO, DARK and ◉ LIVE (the newest geostationary frame plus rain radar, each with its capture time).
  - They show the official boundaries: 36 states/UTs and 33 Telangana districts. `tools/geo_lonlat.py` converts the existing projected outlines exactly; the result is `static/geo/india_ts_ll.json`.
  - **Live info points:** every state and district carries the current Open-Meteo weather and CAMS air quality, measured at the polygon's geometric centre and labelled that way. India also has 14 metro cities. The data comes from one batched request, cached for 10 minutes (weather) and 30 minutes (air).
  - **Live choropleth:** colour the map by TEMP, RAIN, AIR, WIND or ALERTS. Hover a region for its value; click it for a live card with weather, air quality and the active alerts inside its border.
  - **Hazards inside the boundary:** USGS, GDACS, NASA EONET and NDMA SACHET alerts, found by point-in-polygon on the official outline, plus events near it. High and critical alerts pulse.
  - **✈ Aircraft overhead** from the OpenSky data the global map already fetches, with no extra API call.
  - **▶ TOUR:** a cinematic fly-through with the live card at each stop. Extremes readout (hottest, coolest, worst air, wettest, alerts) with fly-to buttons. Orbit, tilt and fullscreen.
  - **Fallback:** the classic SVG maps remain when WebGL or the CDN is unavailable.
  - Backend: `regionmap.py`, `GET /api/regionmap/{india|telangana}`.
- **feat: DISASTER RESPONSE tracking map.** The equirectangular SVG map is now an immersive MapLibre map (DARK by default, with eight imagery modes including ◉ LIVE radar over storms).
  - Severity-sized events, with pulsing rings on high and critical ones.
  - **Storm, cyclone and volcano tracks** from NASA EONET (the disaster feed now keeps each event's path).
  - Density **HEAT** map, **6 H / 24 H / 3 D / ALL** time windows, and a near-me radius ring.
  - **◉ FOLLOW NEW** flies to each new high-severity event as it arrives. Also ◎ ME, ⚠ WORST and ▶ TOUR, plus a live tracking line showing totals, tracks, the newest event and the count of new events since the page opened.
  - Clicking a list item flies the map to it. The existing filters dim the map too.
- **feat: voice and agents drive the new maps.**
  - Example commands: "switch the Telangana map to live", "show air quality on the India map", "tour Telangana", "show aircraft over India", "switch the disaster map to night lights".
  - A new `regions` dashboard-context section lets every agent answer "which Telangana district is hottest?" or "how is Warangal?" from the live map data.
- **engine:** `static/immersivemap.js` is one shared map engine for all three maps. It loads lazily when the map scrolls into view.
- **fix:** MapLibre's stylesheet made the map container `position: relative`, collapsing it, and `[hidden]` overlays with `display: flex` stayed visible. Both are now guarded.
- **Website:** new INDIA · TELANGANA live state grids and DISASTER RESPONSE tracking-map cards.
- **Tests:** `tests/test_v88.py` (9).

## 2026-11-27 — v8.7.0 · BOB joins as the 10th agent, DOOM's Privacy Shield and self-development guard, security hardening
- **feat: BOB (Bobbiey), the supreme orchestrator and 10th agent.** BOB is the commander's own AI counterpart and sits above the team.
  - Oversees every agent, the recursive self-development loop and DOOM's security verdicts.
  - The only agent that may sign off a system-level change, and only together with the commander's approval.
  - Wake words: "Hey Bob", "Bobbiey". Questions about approvals, oversight and team status are routed to BOB.
  - Has a standing brief (self-development, cyber analyst, missions), a local measured briefing, a speaking slot and orchestrator patrols.
- **feat: DOOM GATE.** DOOM scans every self-written code change before it can deploy, in both the evolution loop and engineering mode. The scan is static analysis of the exact bytes and only judges the lines being added.
  - **BLOCK:** touching private files, embedding credentials, `eval`/`exec`, `shell=True`/`os.system`, unsafe deserialisation, disabled TLS verification, or recursive deletes.
  - **REVIEW:** security-perimeter code, new outbound hosts, new subprocess calls, or widened CORS.
  - BOB never signs a BLOCK, and signs a REVIEW only with the commander's explicit acknowledgement.
  - In engineering mode, a blocked change is reverted automatically.
  - The evolution panel shows DOOM's verdict and BOB's sign-off.
- **feat: PRIVACY SHIELD**, a new DOOM panel where everything is measured on this PC:
  - Every remote host the BUCS process is connected to, live, labelled by the hostname BUCS itself requested. Unknown hosts are flagged UNLABELLED.
  - Private files on disk (names and sizes only), each checked against git's ignore rules.
  - Where AI prompts go (local or cloud).
  - Remote-access state.
  - A privacy score with each deduction explained.
  - DOOM answers "who is BUCS talking to?" and "privacy report" from this data, and every agent can read it.
- **security:**
  - Upgraded **MapLibre GL to 6.4.1** in both the dashboard and the website (GHSA-jrc7-96c5-q579, attribution XSS). It is now loaded as an ES module, and all nine satellite modes were verified.
  - Upgraded **python-dotenv to 1.2.2** (GHSA-mf9w-mj56-hr94; BUCS only used `load_dotenv`, so it was not exposed).
  - **Pinned** the floating CDN versions (d3, topojson-client, world-atlas) to exact releases, to close a supply-chain risk.
  - Dashboard facts given to the agents are now explicitly marked as **data, not instructions**, as a prompt-injection guard.
  - Integration audit: the loopback-only perimeter, password-gated remote access, and CSRF and DNS-rebinding guards were confirmed for every state-changing endpoint. An OSV audit found 0 known vulnerabilities in the remaining dependencies.
- **Website:**
  - **Ten agents** throughout: BOB in the roster, the hero stats and the command core.
  - DOOM's Privacy Shield and self-development guard cards.
  - "DOOM scans · BOB signs · you approve."
- **CI:** a new `Publish website (Netlify)` workflow deploys `docs/` to avengers-bobbiey.netlify.app on every push, once the `NETLIFY_AUTH_TOKEN` repository secret is added. Until then it skips cleanly.
- **Tests:** `tests/test_v87.py` (15). It caught and fixed a path-normalisation bug that would have let the gate miss `.env` and `.update/`.

## 2026-11-27 — v8.6.0 · Agents see and drive the dashboard, shared memory, voice modes, more map modes
- **Agents see your screen.** The open dashboard reports each panel's title and visible text, plus the map view, mode and selection, the theme and any open dialog.
  - Agents read this through a new "On screen now" section, so "what does the world clocks panel say?" is answered from what's displayed.
  - Every panel title has a **◇ ASK** chip that asks an agent about that panel.
  - The report is bounded and scrubbed, and goes only to this BUCS server.
- **Agents drive the dashboard by voice or chat**, using the same functions as the buttons:
  - "Switch the map to live satellite / today / topo / dark / 3D globe."
  - "Show Tokyo on the map" (geocoding by Open-Meteo) and "show me my location."
  - "Doom theme," "camera full screen" and "scroll down."
- **One shared memory of the commander.** "Remember that I prefer short answers" is stored once and every agent sees it on every reply. "Forget …" removes it for all of them.
- **Voice modes are now three separate switches** in a bar under the orb:
  - **CONVERSATION:** ON means agents listen to the room. OFF means only "Hey Jarvis…" at the start of a sentence reaches them.
  - **HANDS-FREE:** ON means no wake word is needed. OFF means you say an agent's name.
  - **AGENT VOICES:** mute every agent; replies stay on screen.
  - Voice commands: "mute all agents," "unmute agents," "hands-free on/off."
- **fix:** the long voice status no longer spills across the orb. It lives in the new voice bar.
- **Computer Use by voice** now understands natural phrasings ("on my computer, open Notepad and type…", "… on my laptop for me"). A spoken confirm, sensitive-step approvals and STOP still apply.
- **Global Activity Map:**
  - **📍 MY LOCATION** in every view: exact when the browser shares its position, otherwise the dashboard's city, labelled as such.
  - New satellite modes:
    - **TODAY:** NASA's newest daily pass, dated by GIBS, which fades to sharp archive imagery up close.
    - **TOPO:** a topographic map.
    - **DARK:** a dark tactical canvas.
- **Website:**
  - The "YOU" core is redrawn: a solid hexagon with a crisp label and the caption on its own pill, so nothing overlaps.
  - The voice section now covers dictation, conversation and hands-free, screen awareness, dashboard control, shared memory and Computer Use by voice.
  - The map section lists the new modes and the LIVE close-up.
  - The launch section is removed, and the loader says nine agents.
- **Tests:** `tests/test_v86.py` (15 tests).

## 2026-11-27 — v8.5.1 · Conversation ON/OFF, roomier command bar, steady clock, website polish
- **Conversation ON / OFF is back in the header** (replaces the "HANDS-FREE" label):
  - **ON:** just talk, no wake word.
  - **OFF is now strict.** Talk with colleagues in the room is never taken as a task. Only a sentence that *starts* with a wake phrase ("Hey Jarvis…", "Hey Doom…") reaches an agent. Names mentioned mid-sentence, bare names and the old follow-up window after a reply no longer trigger anything. After an explicit summon (a double clap, or just "Hey Jarvis"), your next sentence is still heard without repeating the wake phrase.
- **The command bar no longer overlaps:**
  - The box grows to about five lines as dictated or typed text fills it. Enter sends, Shift+Enter starts a new line.
  - The mic sits inside the box, and ▶ sits beside it.
  - The agent picker ("TO · AUTO") and the 🎙 VOICE settings moved to their own row.
  - In the narrow left panel, the recording strip shows your words on their own full-width line.
- **fix: the top-right clock no longer shifts the header every second.** The display font's digits have different widths, so the clock now reserves the widest time.
- **Website:**
  - The small globe beside "The Interface Between AI and Humans" is replaced by the **Command Core**, a flat illustration of you at the centre with the nine agents. Commands pulse out and answers pulse back, and agents hand work to each other. Hover an agent to see its role.
  - The location panel is gone. The browser now asks for location when the page opens. If you allow it, the background flies to your exact position; if not, it shows a demo location. Your coordinates stay in your browser.
  - The Doomsday countdown has been removed.

## 2026-11-27 — v8.5.0 · Voice dictation in the command bar, LIVE satellite close-up
- **feat: VOICE DICTATION** (ChatGPT-style). A 🎙 mic inside the command bar:
  - Tap it (or press Alt+M) and speak. A recording strip shows a live waveform, a timer, ✕ cancel (Esc) and ✓ done (Enter).
  - Your words land in the box as editable text. Press ▶ / Enter to send. An optional setting sends immediately instead.
  - **Local and private by default.** Whisper runs on this PC (the same model as the voice loop, shared when it is loaded). Audio is transcribed in memory and never stored, logged or sent anywhere else.
  - **Multilingual.** Auto-detects ~99 languages and shows which one it heard, or you can pick one (Hindi, Telugu, Tamil and 30 more in the menu). It can also translate to English.
  - Optional **Browser engine** that shows live words as you speak. It is clearly labelled: audio goes to the browser's speech service.
  - **New agent picker** next to the box (AUTO or any agent). Starting with a name ("Doom, scan my network…") picks that agent automatically, and @name still works.
  - Text already in the box is passed to Whisper as context, so names and context carry over.
  - The hands-free voice loop ignores what it hears while you dictate, so nothing is answered twice.
  - Clear messages explain microphone permission, missing microphones and insecure-page problems.
  - Endpoints: `GET /api/dictate/status`, `POST /api/dictate`, `POST /api/dictate/hold`.
- **feat: GLOBAL ACTIVITY MAP · ◉ LIVE mode** (SATELLITE view). The close-up now shows real live layers, each with its capture time:
  - The newest **geostationary weather-satellite frame** for wherever you are looking, refreshed every 10–15 minutes. Frames typically appear 20–40 minutes after capture.
    - Coverage: GOES-West/East (Americas), Meteosat MTG (Europe/Africa), Meteosat IODC (India and the Indian Ocean) and Himawari (East Asia and Australia).
    - At night it switches to the infrared band automatically.
    - The frame time is read from the provider, never guessed.
  - **RainViewer radar**, refreshed every 10 minutes.
  - **Live public webcams near the view.** They come from the Windy Webcams API, using your own free key, which is stored locally and never shown back.
    - Webcams appear as map markers and in a list showing each camera's image age.
    - A picture-in-picture player shows the live stream or today's timelapse.
  - The ground imagery underneath stays archival and is labelled "not live". No free source offers real-time street-level satellite imagery.
- **Tests:** `tests/test_dictation.py` (17) and `tests/test_globalmap_live.py` (13). Both run offline, with no microphone, no model download and no network.

## 2026-11-27 — v8.4.0 · DOOM, hands-free conversation, satellite map, immersive website
- **feat: DOOM joins the team.** A ninth built-in agent for cyber defense and global awareness:
  - Handles threat hunting, CVEs, exposure, hardening, phishing and scams, and global situations.
  - Cyber requests ("was I hacked?", "any CVEs?", "sitrep") are now routed to DOOM. Research and news stay with Black Widow.
  - DOOM always receives the Cyber Analyst, threat board and disaster facts, and its background reports quote only measured posture.
  - It proposes; you approve. It never claims to have run, blocked or changed anything.
  - Wake phrases: "Hey Doom", "Doctor Doom", "Dr Doom".
- **feat: HANDS-FREE conversation** (replaces Conversation Mode):
  - Just talk, back to back, with no wake word. Every sentence goes to the agent you are talking to.
  - Start a sentence with a name ("Doom, …", "Cap, …") to switch; that agent stays active. A name mid-sentence never switches.
  - "Stop" interrupts. "Go quiet" pauses, and any agent's name resumes.
  - Pauses itself after 10 minutes of silence. The delay can be set from 0 to 240 minutes, where 0 means never.
  - The header button shows who you are talking to, and every agent's card has a 🎙 TALK HANDS-FREE button.
  - Changing these settings requires the commander role.
- **fix:** agent names now match only as whole words. For example, "author" no longer wakes Thor, and "doomsday" no longer wakes Doom.
- **feat: GLOBAL ACTIVITY MAP · SATELLITE view** with five modes:
  - Satellite and Hybrid (Esri World Imagery).
  - 3D Terrain (AWS Terrarium elevation).
  - 3D City (OpenFreeMap extruded buildings).
  - Night (NASA Black Marble).
  - Live layers are plotted on top, alongside ◎ TRACK THIS DASHBOARD (honest IP-based accuracy), orbit, and fullscreen.
  - Imagery is labelled "not a live feed".
- **feat: camera fullscreen.** Use the ⛶ button, double-click, or F. The fullscreen view shows a clock and the camera label; Esc exits.
- **feat: DOOMSDAY theme and Easter eggs:**
  - A fourth theme (⬢ iron & emerald).
  - A countdown on DOOM's card to Dec 18, 2026.
  - Hidden extras to find: try the Konami code, or type "assemble" or "doom".
- **Website:**
  - The background is now an immersive 3D satellite flight that zooms from space toward a demo location, or toward your own after you click "📍 Fly to my location". Your location stays in your browser.
  - New sections: the Doomsday launch countdown, DOOM, hands-free conversation, and a preview of the satellite map.
  - The site now says nine agents, includes Easter eggs, and adds a trademark notice.
- **Tests:** a new test file for DOOM and nine new hands-free conversation tests. The existing tests were updated for the nine-agent roster.

## 2026-11-25 — v8.3.1 · Remaining edit abilities in the feature sections
- **Devices:** you can now edit a device after adding it. Change its name, room/group and connection settings from its detail view (✎ EDIT).
  - A password or token left blank stays as stored; it is only replaced if you type a new one.
  - Changed addresses are re-checked and must still be on your local network.
  - Every edit is audited. Credentials are never shown.
- **Cyber Analyst:** the scan schedule can now be changed (✎ SCHEDULE). Set how often the full scan (1–48 h) and the light check (10–240 min) run, or pause automatic scans. The change applies immediately and survives a restart.
- **Computer Use:** you can save tasks as templates, load a template back into the task box, and delete templates. Clicking a past task re-uses it. Running a task still goes through RUN TASK and every safety gate.
- Already editable before this release: phone contacts (including consent and do-not-call), AI Core memory entries, and accept-risk with undo.
- **Tests:** a new test file covers device editing, the scan schedule and templates, including restarts and observer refusals. One flaky snapshot test was made stable.

## 2026-11-22 — v8.3.0 · Custom agents, Open Intelligence, edit everything
- **feat: CUSTOM AGENTS.** Build your own team members:
  - Set name, 3-letter tag, role, persona, wake phrases, colour and icon, voice, and model (the active brain by default).
  - Choose which dashboard sections each agent may read and which AI Core tools it may use. The tier policy still applies, so destructive tools always need approval per call.
  - Start from templates, use the "test this agent" box, and duplicate, disable, export or import agents.
  - Custom agents join the Avengers roster (marked CUSTOM), answer by name in the command bar or by wake phrase, and receive only the dashboard facts they are allowed.
  - Built-in agents can't be deleted, but their persona can be overridden and reset.
- **feat: OPEN INTELLIGENCE.** A new live deck fed only by free, keyless, openly licensed public data, with every item linked to its source:
  - Air quality at your location with a 24 h forecast (Open-Meteo/CAMS).
  - WHO disease outbreak news.
  - Upcoming rocket launches (Launch Library 2).
  - Near-earth asteroid approaches (NASA/JPL).
  - Live ISS position.
  - Exchange rates (Frankfurter/ECB).
  - New arXiv papers by category.
  - The Hacker News front page.
  - Mauna Loa CO₂ (NOAA GML).
  - Public holidays (Nager.Date).
  - Sun and moon, computed locally.

  How it behaves:
  - Each source has its own cache, rate-limit-safe refresh, status and as-of time.
  - Your location is rounded to 0.1° before it is sent anywhere.
  - Real-data sparklines and a "what changed since you last looked" highlight.
  - Customisable: show, hide and reorder panels, arXiv categories, FX base currency and watch-list, holiday countries, refresh interval.
  - JARVIS answers from it: "what's the air quality?", "any launches this week?", "USD to INR?", "top arXiv papers?".
- **feat: edit the core dashboard.** Everything saves on the server, syncs across tabs and is commander-gated:
  - World clocks: add, remove, reorder.
  - Quick-access buttons: choose which ones appear.
  - Weather location: search and override.
  - World-feed sources.
  - Notes: edit in place, pin, reorder.
  - Local agenda events and tasks: create, edit, complete, delete, reorder.
  - Threat incidents: acknowledge, resolve, re-open, add notes.
  - Decisions: autonomy whitelist, restore dismissed proposals.
  - Operators: rename, change role, remove.
  - Daily deep-work goal.
- **feat: feature sections.**
  - Disaster response: near-me radius setting and mute an event.
  - Global map: saved places.
  - Feed parsers re-verified.

## 2026-11-06 — v8.2.1 · Desktop build fix
- **ci:** the Apple Silicon installer now builds on GitHub's `macos-15` image (the `macos-14` image was retired, so v8.2.0's installers could not be built). The app still targets macOS 11+. No app changes beyond v8.2.0.

## 2026-11-06 — v8.2.0 · Agents see the whole dashboard, every item links to its source
- **feat: JARVIS and every agent can read every dashboard section.**
  - Ask about anything on screen and the answer comes from that section's live data, naming the section. For example: "what did the cyber analyst find?", "any quakes near me?", "what's on the global map near India?", "last phone call summary", "which model are you using?", "dashboard overview" / "sitrep".
  - Coverage: 25 sections, each read server-side with a time budget. A failing section never breaks the others.
  - Agents also get a read-only `dashboard_read` tool.
  - Secrets, keys, IDs and full phone numbers are never included.
  - Observers see only what the dashboard already shows them.
- **feat: Computer Use from chat or voice.** "Jarvis, use the computer to …" asks you to confirm.
  - It runs only for the commander on the host machine, and every Computer Use safety gate still applies: approval before sensitive actions, pause/stop/take-over, privacy guard.
  - "Computer use status" and "stop computer use" work by voice or chat.
  - The dashboard scrolls to the panel when a task starts.
- **feat: every data item links to its original source,** opening in a new tab:
  - News and intel: the articles.
  - Disasters: USGS, GDACS, NASA EONET, NDMA SACHET, CVE/KEV pages on NVD, IODA, and the service status pages.
  - Global map: flights on ADS-B Exchange, satellites on N2YO/CelesTrak, space weather on NOAA SWPC.
  - Weather: Open-Meteo.
  - Your accounts: calendar, tasks and mail open in Google.
  - Cyber findings: OSV and NVD.
  - Evolution research: PyPI.
  - Chat answers: live-fact citations.

  Data from this machine has no web page, so it is labelled with its real source instead of a made-up link. Only http(s) links are allowed, all opened with `noopener noreferrer`.
- **fix: text buttons no longer spin on hover.** RECONNECT NOW, TEST and the edit buttons were rotating 120°; only the ↻ refresh icons rotate now.

## 2026-11-04 — v8.1.0 · Cleaner header: centred UCS title, new system controls, mic modes removed
- **Removed: the mic ON / SLEEP / OFF / TALK section (owner decision).**
  - The microphone always listens for its wake words, as before v7.9.
  - Removed with it: the header buttons, the keyboard shortcuts, the `/api/voice/mic` and `/api/voice/talk` endpoints, and the spoken and typed "mic off / go to sleep / wake up" commands.
  - A previously saved mic choice is no longer read.
- **UNIFIED COMMAND SYSTEM is pinned to the exact centre of the header** at every window width, and glides as the window is resized.
  - On narrower windows it moves to its own centred row instead of disappearing.
  - The header never makes the page scroll sideways.
- **Redesigned system controls:**
  - The version chip has a hex emblem and status light, and shows UP TO DATE, UPDATE (pulsing green) or DEV BUILD.
  - The power core has an orbiting ring and a crisp power icon.
  - Shutting down now asks in an in-theme confirmation instead of the browser's pop-up. It closes with Esc, Cancel or a click outside.
- **Dashboard:** the logo tooltip no longer carries the old "= Avengers" collaboration line.

## 2026-11-04 — v8.0.1 · Every click updates everywhere and is saved
- **fix: LLM CORE badge under the orb now follows the active brain.**
  - It used to show the insights engine's model, and `/api/models` took about 18 s whenever Ollama wasn't running. The model list is now cached briefly and fails fast.
  - The orb tile, the insights MODEL chip, AI DIAGNOSTICS, Local AI, AI BRAIN ROUTER, the service grid and the LLM SWITCH all update within about 0.2 s of a switch made from any panel, tab, voice or typed command.
- **fix: settings changed in one tab now update every open tab**, through one server sync message. This covers:
  - the brain and its providers
  - notes and productivity
  - role and operators
  - decisions and autonomy
  - licence and commander profile
  - fleet code
  - self-heal
  - AI Core routing, memory, agents, workflows and plugins
  - evolution settings
- **fix:** after a lost connection, the dashboard re-reads every shared setting so missed changes aren't lost.
- **fix: forms keep what you are typing.** The phone settings, AI Core routing and folders, the devices MQTT box and the Computer Use model no longer lose unsaved edits on the periodic refresh, and save buttons show a saving state.
- **feat: typed commands.** "Jarvis, switch the brain to ollama" and "mic off / sleep / wake up" now work from the command bar, not only by voice.
- **fix: the mic pill** shows MIC OFF or SLEEP instead of READY.
- **fix: the audio mute/volume and conversation toggles** show the server's real state, even when a request fails.
- **feat: remembered view choices.** The clock popover (stopwatch, timer, last tab), camera choice, disaster filters, map mode and layers are remembered. Theme and clock settings follow across tabs.
- **feat: Evolution LOOP SETTINGS** control (heartbeat, evaluation window, test interval) that takes effect immediately.
- **fix:** critical and high cyber-analyst findings now reach the threat feed and count toward the threat matrix's security score.
- **fix:** SHOW ON MAP from disaster and threat cards now finds the right point.
- **Website:** removed the "Stark Industries × Bobbiey = Avengers: a collaboration…" line.

## 2026-11-04 — v8.0.0 · Desktop apps, Computer Use, Phone agent, Evolution loop, AI Core, Global Activity Map
- **feat: DESKTOP APPS for Windows, macOS (Apple Silicon + Intel) and Linux:**
  - Each installer bundles its own Python 3.11, so customers don't need to install anything first.
  - Windows: per-user installer (no admin) with Start Menu and Desktop shortcuts and a clean uninstall that keeps your data.
  - macOS: `BUCS.app` in a DMG, with a Dock menu (Open Dashboard, Show Logs, Quit).
  - Linux: `BUCS-Linux-x64.AppImage` (glibc 2.35+), which adds a menu entry.
  - Every installer is built and then install-, launch-, update-, rollback- and uninstall-tested on clean GitHub runners for every OS. It is published only when every test passes.
  - The website detects your OS and offers the matching download, with install steps and system requirements.
  - Signing status: the builds are not yet signed with Apple Developer ID or a Windows certificate, so macOS asks for "Open Anyway" once and Windows SmartScreen asks for "Run anyway" once. Signing switches on automatically once the owner's certificates are added.
- **fix: macOS zip package:**
  - `Start BUCS.command` is executable and finds Python 3.10+.
  - It explains clearly when only Apple's Python 3.9 is present.
  - Windows-only tools are no longer called on macOS or Linux.
- **feat: COMPUTER USE agent:**
  - Sees the screen and acts on it, using the Claude CLI, the Anthropic API or a local Ollama vision model.
  - Before any sensitive action (payments, credentials, delete, send/submit, install, settings) it pauses and asks for your approval.
  - Pause, Stop and Take over work at any time. It never types secrets, and private-file windows are never captured.
- **feat: AI PHONE AGENT:**
  - Real inbound and outbound calls through Twilio, with live transcripts, summaries, consent recording, a do-not-call list and calling hours. The AI discloses that it is an AI.
  - Calls are shown only when Twilio reports them.
  - Needs the owner's Twilio account and a public URL.
- **feat: AUTONOMOUS EVOLUTION loop:**
  - Observe → Analyze → Propose → Test in a sandbox → Request approval → Deploy → Evaluate.
  - Nothing is deployed without the commander's explicit approval and a passing sandbox. Deploys run on the owner machine only, and `.update/` is protected.
- **feat: AI CORE:**
  - Provider registry covering Ollama, Claude and OpenAI-compatible providers, with routing that explains its decisions.
  - Tool calling with permission tiers.
  - RAG memory (semantic search after `ollama pull nomic-embed-text`).
  - Agents and workflows.
  - A plugin system; third-party plugins are never auto-enabled.
- **feat: GLOBAL ACTIVITY MAP (BUPIS rebuilt):**
  - Real, live layers: earthquakes, GDACS, NASA EONET, volcanoes, NWS/NDMA alerts, internet outages (IODA), live aircraft (OpenSky), satellites (CelesTrak, with AMSAT as fallback) and space weather (Kp, aurora).
  - Every layer shows its source, time and status.
- **Dashboard:**
  - Every mock or simulated value is removed, and every card opens a detail view backed by real data, with loading, error and empty states.
  - The new header clock opens world clocks, sunrise/sunset and a stopwatch/timer.
  - The header temperature is removed; Weather has its own section.
  - The "= AVENGERS" line is removed from the brand.
- **fix: MIC ON / SLEEP / OFF:**
  - TALK works even without conversation mode.
  - OFF now actually releases the microphone, and ON reopens it.
  - The control shows loading and error states with the real microphone status.
- **Website:**
  - New high-quality 3D Earth hero and 3D system showcase.
  - Downloads for Windows, macOS and Linux.
- **Security:** Twilio webhook signatures are verified, every new endpoint is commander/host-gated, and no endpoint returns a key.

## 2026-11-03 — v7.9.0 · Mic modes, live answers, disaster response, devices, cyber analyst
- **Refocus:** Project Trillion / agent commerce is removed: the code, the dashboard card, its docs and its tests. The old ledger files stay on disk untouched.
- **feat: MIC ON / SLEEP / OFF + TALK:**
  - **SLEEP** takes only deliberate commands: "Jarvis …", an agent wake phrase, a double-clap or the TALK button. Background talk and follow-up windows are ignored.
  - **OFF** releases the microphone.
  - **TALK** (or Ctrl+Space) makes your next sentence a command. Ctrl+Shift+M toggles the mic.
  - Say "Jarvis, go to sleep", "Jarvis, wake up" or "mic off". The choice is remembered.
- **feat: accurate real-time answers:**
  - Every answer knows the real date, time, your location and the latest measured weather.
  - Questions about current events, scores, prices or office-holders are checked against live sources fetched in about 1 s: news from the last 7 days, Wikipedia and CoinGecko.
  - The answer must cite them, or say it couldn't verify live instead of guessing.
- **feat: DISASTER RESPONSE · REAL-TIME EVENTS:**
  - Live earthquakes (USGS), disasters (GDACS, NASA EONET) and Indian alerts (NDMA SACHET) on a world map, marked "near you" within 500 km.
  - Digital events: newly added actively-exploited vulnerabilities (CISA KEV), status of major services, internet outages (IODA).
  - Preparedness guidance and alerts for new severe events.
- **feat: DEVICES & CONNECTIVITY:**
  - Add and control smart devices through local APIs: Home Assistant (covering Matter, Zigbee, Z-Wave and thousands of brands), MQTT, Philips Hue, Shelly, Tasmota, ESPHome, WLED and generic HTTP.
  - mDNS/SSDP discovery.
  - Secure by design: private-network addresses only, tokens masked, commander-only and every action audited.
  - **Node connectivity** detects LAN, Wi-Fi, Tailscale, WireGuard, ZeroTier, Cloudflare Tunnel, SSH, Bluetooth and cellular, with join steps for each.
- **feat: CYBER ANALYST · AUTONOMOUS DEFENCE:**
  - Continuous real scans: dependency vulnerabilities (OSV), CISA KEV, secret hygiene, open ports, firewall/Defender and app config.
  - Keeps its threat intel and finding history up to date.
  - Proposes code fixes to engineering mode; nothing is applied without your approval.
- **security:** acting on the analyst's first scan:
  - The web framework is upgraded (fastapi 0.162, starlette 1.10). This fixes CVE-2026-48710, which CISA lists as actively exploited.
  - certifi is upgraded, removing the compromised emSign roots.
  - urllib3, oauthlib, pyasn1, filelock and setuptools are also upgraded.
  - Posture score went from 21 to 85; no critical or high findings remain.
- **website:**
  - A real-time 3D hero: an arc reactor inside a holographic globe, with the 8 agents in orbit.
  - An interactive agent constellation, capability cards that tilt in 3D, a "New in BUCS" showcase and a command-console demo.
  - Respects reduced-motion settings and works on mobile.
- **clean download:** customers now get `bucs-latest.zip`, which contains only the app in a `BUCS/` folder. No website, tests, owner tools or planning docs (`.gitattributes` export-ignore covers GitHub's zip too).
- **test:** 233 tests (mic modes, live grounding, disaster feeds, device adapters and security, cyber analyst).

## 2026-10-30 — v7.8.0 · Agent Commerce Market Radar, x402 seller, opportunity pipeline
- **feat: AGENT COMMERCE MARKET RADAR** inside the existing Project Trillion card (owner-only, read-only, `commerce/radar.py`):
  - Live evidence from the Coinbase x402 Bazaar (per-endpoint 30-day calls and unique payers), agenteconomy.to (on-chain totals), nohumans.directory (probe reliability, new APIs, what agents search for), x402-list.com, PayAPI Market and Base mainnet USDC logs (independent payer check).
  - Every figure shows its source, time, coverage and verification. Mainnet only; never summed across sources.
  - **Listing farms are detected and excluded:** 8 wallets list 77% of Bazaar endpoints with about 1 call each.
  - Niche opportunities ranked by seller-level demand, a top-20 competitor table with a BUCS score, top repeat-purchase and estimated-revenue lists (labelled EST.), failing listings, sources. Refresh at most every 30 min.
- **feat: Revenue Opportunity Pipeline:** save an opportunity with its evidence, model unit economics (always labelled estimates), approve/reject, convert to a **draft** service (never sold until it has a tested executor), track directory listings. Additive tables with a database backup first.
- **feat: x402 seller** for Website Extraction:
  - Agents pay per request in USDC: verify, then deliver, then settle **only on success**, so a failed request is never charged.
  - Booked in USDC, apart from rupees. MCP server and `/.well-known/x402` manifest for discovery.
  - OFF until the owner sets a wallet address; Base Sepolia test network by default.
  - Owner-set x402 price.
- **feat: genuine customers only:** payments from the owner's test wallets, directories' verification purchases and self-transfers are classified apart and never counted as customers.
- **feat: PayAPI listing draft:** the exact payload plus the blockers still in the way. BUCS never submits listings itself.
- **quality:** Website Extraction returns machine-readable error codes. A site's 403/429 block page or a JavaScript-only page is a failure, not a charged result. Adds clean text and a quality score.
- **security:** DNS-rebinding fix for Website Extraction. The vetted IP is the one connected to (`_PinnedBackend`).
- **docs:** `TRILLION_MARKET_REPORT.md` covers the 10 opportunities, 20 competitors with evidence, first 3 services, a customer-acquisition plan, the path to the first genuine paying agent, and the smallest changes.
- **test:** x402 (terms in both protocol versions, invalid payment, failed delivery never settled, USDC kept apart from INR, repeat buyers, discovery, off until configured, price change, test purchases never counted as customers), pipeline (owner-only, estimates only, backup before migration, never auto-deployed), radar (owner-only, rate limit, dedup, directory purchase labelling), DNS rebinding. 183 tests.
- Razorpay is unchanged: still unconfigured, test mode.

## 2026-10-30 — v7.7.0 · Self-healing core + Project Trillion (phase 1)
- **feat: SELF-HEALING · AUTONOMIC CORE** (`selfheal.py`). Every 30 s BUCS measures itself: event-loop lag, its own CPU and RAM, background tasks, scheduler, and weather/news/Google freshness and AI routing. It **repairs** what is safe: it revives crashed background tasks (now named and restartable), refreshes stale feeds and restarts a stopped scheduler. It asks the connected AI for a **grounded diagnosis** (only measured data in the prompt) and can hand that diagnosis to engineering mode, where a fix still needs your approval. **Self-restart** through the launcher is the last resort: only under the launcher (`BUCS_LAUNCHER=1`), at most once per 30 min, with no restart loops. Dashboard card plus live push; the header status lights jump to it. Real repair log, with toggleable auto-repair.
- **feat: PROJECT TRILLION · AGENT COMMERCE** (owner-only, `commerce/`, see PROJECT_TRILLION.md):
  - Service catalogue with schemas, prices and limits. Only **Website Extraction** is sold; the other 6 are honestly *planned*.
  - Hash-chained append-only ledger with separate test and live books.
  - Razorpay gateway: orders, server-side payment verification, capture, signed webhooks, refunds, settlement recon.
  - Public order/checkout/result API and owner admin: pricing, pause, limits, allowlist, agent API keys, typed-confirmation refunds, settlement sync.
  - Delivery only after the gateway confirms the right order, exact amount, INR and captured. Nothing is automatic for money out.
  - Shows "not configured" with ₹0 until Razorpay keys are added.
- **fix:** connectivity probes killed on timeout (no hung netsh/PowerShell), and `/api/connectivity` shared and cached (was 7.5 s per tab).
- **test:** `tests/test_selfheal.py` (task revival, stale-feed refresh, repair off, restart only under launcher and never looping, grounded diagnosis, live endpoint) and `tests/test_commerce.py` (11: catalogue honesty, forged signature, wrong amount, another order's payment, capture, idempotency/duplicate prevention, webhook signature/replay/re-fetch, failure → owner refund only with confirmation, ledger-only figures, tamper detection, owner-only admin, spending caps, SSRF). 163 tests.

## 2026-10-30 — v7.6.0 · Security hardening, 200× faster process monitor, weather fix
- **fix (root cause of "weather: timeout"):** the PROCESS MONITOR re-scanned every process on every request with psutil, which is O(n²) on Windows (~2 s of CPU per sweep on 358 processes). Each open dashboard polled it every 6 s, so the server sat at 100% of a core and even trivial requests took ~7 s; weather was just the panel that reported it. New `procscan.py` reads all processes in one `NtQuerySystemInformation` call (**10 ms**, verified against psutil), and one shared snapshot serves every viewer (≤1 scan / 4 s). Part of the load came from ~20 leftover headless Chrome instances started by our own screenshot scripts; those were closed.
- **weather:** identical requests share one upstream call; server timeout (8 s) now sits under the dashboard's (15 s); an outage shows the last good reading marked CACHED instead of "timeout"; a newer GPS location queues a refresh instead of being dropped.
- **security (independent review, every finding verified before fixing):**
  - **Browser-origin guard:** web pages open in the owner's browser can no longer act as the local user (CSRF), DNS rebinding is blocked by a Host allowlist, and foreign pages can't read the live WebSocket feed. `JARVIS_ALLOWED_HOSTS` adds extra host names.
  - **Vision:** the Claude CLI may Read only a private copy of the current frame (path-scoped permission rule; `--dangerously-skip-permissions` removed); client-supplied vision prompts are ignored. Verified live: it still describes the frame and refuses any other file.
  - **Engineering mode:** commander-only on every route (API, chat, voice); a fix that touches `tests/`, `.git/`, `.github/` or `tools/` is fully reverted and nothing runs; git hooks disabled for its commits/pushes; deleted protected files are restored; ship/undo no longer block the server.
  - **LLM providers:** a stored API key no longer follows a base-URL change.
  - **Fleet:** node reports rebuilt from a strict schema (stored XSS, crash on bad types, SSRF via the Ollama port); dashboard escapes those fields; constant-time fleet-token check.
  - **PIN:** operator switching can no longer bypass the commander PIN; constant-time compare + lockout after 5 wrong tries.
  - **Browser open:** only http(s) web URLs; a URL can never become a browser switch, local path or custom protocol.
  - **Data integrity:** 14 user-data writes (auth, memory, notes, operators, billing, productivity, team memory, web3…) are now crash-safe (temp file + atomic replace).
  - **Waitlist:** public endpoint size- and rate-capped.
- **test:** `tests/test_security.py` (origin guard on a live server, WebSocket origin, fleet sanitising, provider key, URL opening, weather coalescing/outage, scanner speed) + engineering-mode lock/refusal tests. 147 tests.

## 2026-10-29 — v7.5.4 · Shimmering logo, one-line header at every zoom
- **brand:** STARK INDUSTRIES × BOBBIEY now uses the shimmering gradient motion the AVENGERS title had, slightly larger (0.98rem at 1920 px, 1.1rem on wide screens).
- **fix:** zooming in (a narrower page) wrapped the right-hand header group and pushed the clock onto a second line, making the header taller. The header now stays one 70 px row from 2200 px to 1100 px (measured at 8 widths). It compacts in steps instead of wrapping: CONVERSATION → CONV, a dot-only link pill (status on hover), smaller theme buttons, the header temperature hidden ≤1450 px (the weather card still shows it), the volume slider hidden ≤1300 px (mute stays), and a narrower banner. With the banner hidden (≤1100 px) the header switches to two columns, so the right group stays on the right.

## 2026-10-29 — v7.5.3 · Header row
- **ui:** dashboard logo is a single line, **STARK INDUSTRIES × BOBBIEY** (the "= AVENGERS" row is removed; the positioning stays in the logo's hover text). The system module is now one row that fills the whole gap between the logo and the UNIFIED COMMAND SYSTEM box, at exactly the same height and top edge (40 px): live status on the left, version + ⏻ on the right. Labels adapt by width (full ≥2100 px, SECURE · LIVE at standard widths, dots with hover names on laptops). Measured at 2200/1920/1600/1366/1280 px: equal heights, nothing clipped, ⏻ always inside.

## 2026-10-29 — v7.5.2 · New brand mark + system module
- **brand:** the logo is now the equation **STARK INDUSTRIES × BOBBIEY = AVENGERS**: the collaboration that puts the power of the Avengers AI agents in people's hands, so people and AI work as one team. It is used in the dashboard (hover shows the positioning), on the activation screen, and on the website (nav logo + a positioning line in the hero).
- **ui:** a new HUD module between the logo and the centre banner holds **SECURE CHANNEL** and **LIVE TELEMETRY** as real live indicators (green while the dashboard's live link is connected and telemetry is streaming, amber otherwise), the version/update pill, and ⏻ shutdown. The banner subline is removed; it reads just UNIFIED COMMAND SYSTEM. Compact labels below 1560 px; checked at 1920/1366/1280 with nothing clipped.

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
