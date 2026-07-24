"""Web3 Command Center — an OPTIONAL, modular blockchain layer.

Design principle: the AI Command Platform is the product. This layer is off by
default and can be enabled/disabled at runtime without affecting anything else.
Crypto is one monetization/community path among many — never a dependency.

All data here is mock/demo so the UI is ready before any token exists. Wallet
connection itself happens in the browser (MetaMask / injected EIP-1193); the
server only records the connected address/network the client reports. No keys,
no signing, no funds move server-side.

Sub-services (kept separate so each can evolve independently):
  WalletManager · TokenService · GovernanceService · TreasuryService ·
  MarketplaceService · CommunityService
"""

import json
import time
from pathlib import Path

FILE = Path(__file__).parent / "web3_config.json"

# ── demo tokenomics (mock until a real token launches) ────────────
TOKEN = {
    "symbol": "BBUCS", "name": "Bobbiey Command Token", "decimals": 18,
    "total_supply": 100_000_000,
    "circulating": 24_500_000,
    "allocations": [
        {"label": "Community & Rewards", "pct": 40, "tokens": 40_000_000},
        {"label": "Treasury / DAO", "pct": 25, "tokens": 25_000_000},
        {"label": "Ecosystem & Grants", "pct": 15, "tokens": 15_000_000},
        {"label": "Team (vested)", "pct": 15, "tokens": 15_000_000},
        {"label": "Liquidity", "pct": 5, "tokens": 5_000_000},
    ],
    "burned": 1_250_000,
    "holders": 0,            # future — no token yet
    "market": None,          # future — populated when listed
}

# token gates a set of platform capabilities (access, not speculation)
PREMIUM_UNLOCKS = [
    {"name": "Premium AI models", "stake": 500, "unlocked": False},
    {"name": "Advanced autonomous agents", "stake": 1000, "unlocked": False},
    {"name": "Higher AI-credit limits", "stake": 750, "unlocked": False},
    {"name": "Exclusive plugins", "stake": 1500, "unlocked": False},
    {"name": "Beta features", "stake": 250, "unlocked": False},
    {"name": "Enterprise integrations", "stake": 5000, "unlocked": False},
]

GOVERNANCE_PROPOSALS = [
    {"id": "BIP-004", "title": "Fund community plugin bounties (Q-next)",
     "status": "active", "for": 68, "against": 12, "quorum": 80, "ends_in_h": 41},
    {"id": "BIP-003", "title": "Add DeepSeek-R1 to the premium model pool",
     "status": "active", "for": 91, "against": 4, "quorum": 80, "ends_in_h": 12},
    {"id": "BIP-002", "title": "Treasury: 5% to on-prem cluster R&D",
     "status": "passed", "for": 84, "against": 16, "quorum": 80, "ends_in_h": 0},
    {"id": "BIP-001", "title": "Ratify community governance charter",
     "status": "passed", "for": 97, "against": 3, "quorum": 80, "ends_in_h": 0},
]

MARKETPLACE = [
    {"name": "Sentinel — network anomaly agent", "type": "agent", "price": "120 BBUCS", "installs": 0, "status": "coming-soon"},
    {"name": "Standup Synth — meeting summarizer", "type": "plugin", "price": "Free", "installs": 0, "status": "coming-soon"},
    {"name": "Ops Weekly — automation template", "type": "workflow", "price": "40 BBUCS", "installs": 0, "status": "coming-soon"},
    {"name": "Tactical HUD — dashboard theme", "type": "dashboard", "price": "Free", "installs": 0, "status": "coming-soon"},
]

COMMUNITY = {
    "members": 0, "contributors": 0,          # future — no community yet
    "announcements": [
        {"ts_rel": "roadmap", "text": "Web3 layer scaffolded — wallet + token + governance UI ready ahead of any launch."},
        {"ts_rel": "planned", "text": "Plugin marketplace opens to community builders after Team GA."},
        {"ts_rel": "planned", "text": "Governance charter (BIP-001) drafted for community ratification."},
    ],
}


class Web3Service:
    def __init__(self, hub=None) -> None:
        self.hub = hub
        self.enabled = False
        self.wallet = None      # {address, network, chain_id, balance} set by client
        try:
            cfg = json.loads(FILE.read_text(encoding="utf-8"))
            self.enabled = bool(cfg.get("enabled"))
        except Exception:
            pass

    def _save(self) -> None:
        try:
            FILE.write_text(json.dumps({"enabled": self.enabled}), encoding="utf-8")
        except Exception:
            pass

    async def set_enabled(self, on: bool) -> dict:
        self.enabled = bool(on)
        self._save()
        if not self.enabled:
            self.wallet = None
        if self.hub:
            await self.hub.broadcast({"type": "log", "level": "info",
                                      "msg": f"Web3 Command Center {'ENABLED' if on else 'disabled'} (optional module)"})
            await self.hub.broadcast({"type": "web3", "enabled": self.enabled})
        return {"ok": True, "enabled": self.enabled}

    # ── WalletManager: the browser connects; we just record the state ──
    async def connect_wallet(self, address: str, network: str = "", chain_id=None,
                             balance=None) -> dict:
        if not self.enabled:
            return {"ok": False, "error": "Web3 module is disabled"}
        self.wallet = {
            "address": (address or "")[:64], "network": network[:40],
            "chain_id": chain_id, "balance": balance,
            "connected_at": time.time(),
        }
        if self.hub:
            short = f"{address[:6]}…{address[-4:]}" if address and len(address) > 12 else address
            await self.hub.broadcast({
                "type": "alert", "severity": "info",
                "title": "Wallet connected",
                "detail": f"{short} on {network or 'unknown network'}",
                "source": "web3 · wallet", "action": "Web3 Command Center is live.",
            })
        return {"ok": True, "wallet": self.wallet}

    async def disconnect_wallet(self) -> dict:
        self.wallet = None
        return {"ok": True}

    def _token(self) -> dict:
        t = dict(TOKEN)
        t["treasury"] = next((a["tokens"] for a in TOKEN["allocations"]
                              if "Treasury" in a["label"]), 0)
        return t

    def _governance(self) -> dict:
        active = [p for p in GOVERNANCE_PROPOSALS if p["status"] == "active"]
        return {
            "proposals": GOVERNANCE_PROPOSALS,
            "active_count": len(active),
            "voting_power": (round(float(self.wallet["balance"]), 2)
                             if self.wallet and self.wallet.get("balance") else 0),
            "participation_rate": 34,   # demo
        }

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "wallet": self.wallet,
            "token": self._token(),
            "premium": PREMIUM_UNLOCKS,
            "governance": self._governance(),
            "marketplace": MARKETPLACE,
            "community": COMMUNITY,
        }
