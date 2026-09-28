# 💽 Bobbiey UCS — Laptop Migration Guide

Move the whole platform to a new machine **without losing code, data,
configuration, functionality, UI, or the accumulated context** of how it was built.

Nothing here changes the project. It only documents and packages it.

> **Status (2026-09-28):** the project has been restored on the new laptop at
> `C:\Users\Saikiran\Avengers-Bobbiey-UCS` (Python 3.11.9 venv, native Claude
> CLI). `C:\Users\sai\...` paths below describe the **old** machine and are kept
> for reference. Current machine facts live in `CLAUDE.md` §3.

---

## The one thing to understand first

The project splits into **two halves**, and only one of them is on GitHub:

| Half | Where it lives | Travels via |
|---|---|---|
| **Code** — all Python, the dashboard, website, docs, launchers | GitHub (`Avengers-Bobbiey-UCS`) | `git clone` ✅ |
| **Secrets · config · your data** — `.env`, Google tokens, operator memory, notes, audit log, team memory, billing, Web3 state | **gitignored on purpose** | **manual copy** ⚠️ |

A plain `git clone` gives you a working-but-amnesiac platform: no Google
connection, no memory of you, no notes, no audit history. The **private bundle**
carries that second half.

---

## Path A — OneDrive (easiest, if you use it)

This project currently lives inside OneDrive:

```
C:\Users\sai\OneDrive\Bobbiey's Claude\jarvis
```

So it is **already syncing to the cloud, including the gitignored files**. On the
new laptop:

1. Install OneDrive and sign in with the same account.
2. Let `Bobbiey's Claude\jarvis` finish syncing (check the green tick).
3. **Delete the synced `.venv\` folder** — it contains absolute paths from the
   old machine and will not work. You will rebuild it in Step 4 below.
4. Continue at **"Finish setup on the new laptop"**.

> ⚠️ `.venv` should never have been in a synced folder — it is thousands of
> files and machine-specific. Consider adding it to OneDrive's exclusions, or
> keep the project outside OneDrive on the new machine and rely on git + bundle.

---

## Path B — Git + private bundle (clean, recommended)

### 1. Create the bundle (on the OLD laptop — already done)

The bundle sits at:

```
C:\Users\sai\OneDrive\Bobbiey's Claude\bobbiey-migration\
├─ private-data\           ← 19 gitignored secret/config/data files
├─ claude-context\          ← Claude memory + launch.json
├─ ENVIRONMENT.txt          ← exact versions on the old machine
├─ requirements-frozen.txt  ← the exact 71 packages
└─ RESTORE-README.md        ← what everything is
```

Copy that folder to a **USB drive** (or a private cloud folder you control).

> 🔐 It contains real credentials. Do not email it, share it, or put it in a
> public repo. Delete it from the USB once the new laptop is verified.

### 2. Clone the code (on the NEW laptop)

```bash
git clone https://github.com/saikiranthatikonda-coder/Avengers-Bobbiey-UCS.git
cd Avengers-Bobbiey-UCS
```

Confirm you are at the migration commit or newer:

```bash
git log --oneline -1
```

### 3. Restore your private half

**Windows**
```powershell
.\restore-data.ps1 -Bundle "D:\bobbiey-migration" -IncludeClaudeMemory
```

**macOS / Linux**
```bash
chmod +x restore-data.sh
./restore-data.sh /Volumes/USB/bobbiey-migration
```

The script is **non-destructive** — anything it would replace is copied into
`.restore-backup\<timestamp>\` first, and it deletes nothing.

> If the **old laptop will keep running as a fleet node**, add
> `-NewNodeIdentity` (or `--new-node-identity`) so the new machine gets its own
> node id instead of colliding with the old one.

---

## Finish setup on the new laptop

### 4. Rebuild the virtual environment — use Python 3.12

The old venv ran **Python 3.12.9** (the system Python was 3.14, which some
dependencies do not support yet). Use 3.12 to stay faithful. Any 3.10+
works; the current laptop runs 3.11.9 without issues:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If anything misbehaves, install the exact old versions instead:
```powershell
.\.venv\Scripts\python.exe -m pip install -r <bundle>\requirements-frozen.txt
```

### 5. Fix the two machine-specific paths in `.env`

`.env` came from the old laptop, so two values point at old locations:

```ini
CLAUDE_BIN=C:\Users\sai\AppData\Roaming\npm\claude.cmd   # ← update this
JARVIS_HOST=0.0.0.0                                       # ← 127.0.0.1 for local-only
```

> Since 2026-09-28 `CLAUDE_BIN` can simply be left unset: `brain.py` finds the
> CLI on PATH or at the native installer's `~\.local\bin\claude.exe`.

Find the new Claude CLI path:
```powershell
where.exe claude      # Windows
which claude          # macOS / Linux
```
Install it first if missing: `npm i -g @anthropic-ai/claude-code`

### 6. Reinstall the optional local-AI stack (if you want it)

Install [Ollama](https://ollama.com), then pull the model you were using:
```bash
ollama pull qwen3:1.7b
```
`model_pref.json` remembers your brain choice, so it re-selects automatically.

### 7. Launch

```
start-jarvis.cmd          (Windows)
./start-jarvis.sh         (macOS / Linux)
```

---

## Verify the migration (10 checks)

Open `http://localhost:8765` and confirm:

| # | Check | Proves |
|---|---|---|
| 1 | Dashboard loads, no console errors | code intact |
| 2 | CPU/RAM/disk telemetry moving | system monitor |
| 3 | **AGENDA shows real events** (not "connect Google") | `token.json` restored ✅ |
| 4 | **OPERATOR MEMORY lists your facts** | `operator_memory.json` ✅ |
| 5 | **Your notes are in the notes card** | `notes.json` ✅ |
| 6 | **AUDIT TRAIL has old entries** | `audit.log` ✅ |
| 7 | Product Evolution shows **v5.8 · 96%** | roadmap probes |
| 8 | LICENSE card shows your edition | `billing.json` ✅ |
| 9 | Enable Web3 → live block number appears | Web3 subsystem + internet |
| 10 | COMMAND FLEET shows this host as a node | fleet + `node_id.txt` |

If #3–#6 fail, the private bundle did not land — re-run the restore script and
check it reported `restored .env / token.json / operator_memory.json`.

---

## Restoring the conversation context (important)

Our build sessions produced hard-won knowledge — architecture decisions and
non-obvious gotchas that cost real debugging time. Two things carry it:

1. **`PROJECT_CONTEXT.md`** (in this repo) — the durable written record.
   Travels with the code automatically. **Read it first on the new laptop.**
2. **`claude-context/bobbiey-ucs-project.md`** (in the bundle) — Claude Code's
   memory file. To restore it:
   - Launch Claude Code once inside the project on the new laptop (this creates
     `~/.claude/projects/<project-key>/memory/`).
   - Copy `bobbiey-ucs-project.md` and `MEMORY.md` into that `memory\` folder.
   - Restart Claude Code — it will load the project context on session start.

---

## Retiring the old laptop

Only after the 10 checks pass:

1. Stop the server there (close the JARVIS window).
2. If it was a fleet node and you don't want it reporting: stop `node_agent`.
3. **Rotate the shared secrets** if you're disposing of / selling the machine:
   - new NewsAPI key
   - regenerate the Google OAuth client in Google Cloud Console
   - delete `fleet_token.txt` + `auth.json` on the new laptop and re-arm the
     access password (a fresh token/secret is generated on next boot)
4. Securely wipe the old project folder and any leftover bundle copies.

---

## Files that are safe to NOT migrate

| File | Why |
|---|---|
| `.venv\` | machine-specific; rebuild from `requirements.txt` |
| `__pycache__\` | regenerated automatically |
| `vision_frame.jpg` | transient webcam frame |
| `node_token.txt` | only exists on machines that joined as a node; re-pairs |

---

## Quick reference — every environment variable

See `.env.example` for the complete annotated list, including the ones added
later: `JARVIS_FLEET_TOKEN`, `JARVIS_PIN`, `JARVIS_NODE_NAME`, and the Web3
config keys (which live in `web3_config.json`, not `.env`).
