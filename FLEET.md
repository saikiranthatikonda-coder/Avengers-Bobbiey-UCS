# 🛰️ Command Fleet — Multi-Site Nodes

Turn **every machine you own** into a live node in one Bobbiey UCS command
center. Each node reports its own real telemetry — CPU, RAM, disk, network,
GPU, hardware and peripherals — to a single dashboard with a fleet overview
and per-node detail. _(Roadmap Phase 4 · multi-site.)_

```
   ┌───────────────┐        ┌───────────────┐        ┌───────────────┐
   │  Studio-Mac   │        │  GPU-Laptop   │        │   Work-PC     │
   │  node_agent   │        │  node_agent   │        │  node_agent   │
   └───────┬───────┘        └───────┬───────┘        └───────┬───────┘
           │  POST /api/fleet/report (X-Fleet-Token)         │
           └──────────────────────┬──────────────────────────┘
                                   ▼
                     ┌──────────────────────────┐
                     │   COMMAND HOST (this PC)  │
                     │   dashboard · COMMAND     │
                     │   FLEET section           │
                     └──────────────────────────┘
```

The command host registers **itself** as a node automatically, so you always
see at least one machine.

---

## 1 · Prepare the command host (the machine running the dashboard)

To let other machines reach it, bind beyond loopback. In `.env`:

```
JARVIS_HOST=0.0.0.0
JARVIS_PORT=8765
```

Restart with `start-jarvis.cmd`, then allow the port through the firewall once:

```powershell
New-NetFirewallRule -DisplayName "Bobbiey UCS" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765
```

Find this machine's LAN IP with `ipconfig` (Windows) or `ifconfig` (mac/Linux)
— e.g. `192.168.1.20`.

### Get the fleet token
Open **http://127.0.0.1:8765/api/fleet/token** on the host (it only reveals the
token to the host itself), or read **`fleet_token.txt`** in the project folder.
Pin your own instead by setting `JARVIS_FLEET_TOKEN` in `.env`.

> The **ADD ANOTHER LAPTOP** panel in the dashboard's COMMAND FLEET section
> shows the exact command with the token filled in — just copy it.

---

## 2 · Join a node (on each other laptop)

Copy `node_probe.py` and `node_agent.py` to that machine (or clone the repo),
then:

```bash
pip install psutil          # the ONLY dependency the agent needs

# Windows
start-node.cmd http://192.168.1.20:8765 <TOKEN> "Studio-Laptop"

# macOS / Linux
./start-node.sh http://192.168.1.20:8765 <TOKEN> "Studio-Mac"

# or directly:
python node_agent.py --server http://192.168.1.20:8765 --token <TOKEN> --name "Studio-Mac"
```

The node appears in the COMMAND FLEET section within seconds. Leave the agent
running (or add it to Startup / Login Items / a systemd service) so the node
stays live.

---

## Security notes

- The report endpoint is **token-gated** (`X-Fleet-Token`). Rotate the token by
  editing `fleet_token.txt` (or `JARVIS_FLEET_TOKEN`) and restarting.
- The token is only served to **localhost** on the host, never to remote viewers.
- The dashboard is protected by **authenticated remote access**: the loopback
  console is always trusted, but remote requests are **denied by default** until
  you set an access password (REMOTE ACCESS & AUTH panel, host console only).
  Remote operators then log in at `/login` (HMAC-signed, HttpOnly, 12 h sessions);
  scripts use API bearer tokens. 5 failed logins → 5-minute IP lockout. See the
  security panel in the dashboard.
- Nodes push **only telemetry** — no remote code execution, no file access.

---

## Status model

| State   | Meaning                              |
|---------|--------------------------------------|
| ONLINE  | reported within 20 s (green, pulsing)|
| STALE   | 20–90 s since last report (amber)    |
| OFFLINE | >90 s (grey) — asleep / agent stopped|

Offline nodes stay visible for 24 h so you can see a machine that went to sleep;
the local host never expires.
