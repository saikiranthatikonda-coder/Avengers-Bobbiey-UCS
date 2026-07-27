"""Persisted, operator-editable Web3 configuration.

Everything the Web3 layer needs is configurable here so the platform can adopt
a real token later WITHOUT code changes: set `token.contract` to a deployed
ERC-20 address and the token/wallet services start reading live on-chain data
instead of the configured demo figures.
"""

import copy
import json
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "web3_config.json"

# Public RPC endpoints (no API key). Operators can override any of these.
CHAINS = {
    1:      {"name": "Ethereum",  "symbol": "ETH",   "rpc": "https://ethereum-rpc.publicnode.com",     "explorer": "https://etherscan.io"},
    137:    {"name": "Polygon",   "symbol": "POL",   "rpc": "https://polygon-bor-rpc.publicnode.com",  "explorer": "https://polygonscan.com"},
    8453:   {"name": "Base",      "symbol": "ETH",   "rpc": "https://base-rpc.publicnode.com",         "explorer": "https://basescan.org"},
    42161:  {"name": "Arbitrum",  "symbol": "ETH",   "rpc": "https://arbitrum-one-rpc.publicnode.com", "explorer": "https://arbiscan.io"},
    10:     {"name": "Optimism",  "symbol": "ETH",   "rpc": "https://optimism-rpc.publicnode.com",     "explorer": "https://optimistic.etherscan.io"},
    56:     {"name": "BNB Chain", "symbol": "BNB",   "rpc": "https://bsc-rpc.publicnode.com",          "explorer": "https://bscscan.com"},
    11155111: {"name": "Sepolia", "symbol": "ETH",   "rpc": "https://ethereum-sepolia-rpc.publicnode.com", "explorer": "https://sepolia.etherscan.io"},
}

DEFAULTS = {
    "enabled": False,
    "default_chain": 1,
    # ── token: demo values until a contract is deployed ──────────
    "token": {
        "name": "Bobbiey Command Token",
        "symbol": "BBUCS",
        "decimals": 18,
        "contract": "",            # set to a deployed ERC-20 → live on-chain reads
        "total_supply": 100_000_000,
        "circulating": 24_500_000,
        "burned": 1_250_000,
        "holders": 0,
        "launched": False,
        "allocations": [
            {"label": "Community & Rewards", "pct": 40},
            {"label": "Treasury / DAO",      "pct": 25},
            {"label": "Ecosystem & Grants",  "pct": 15},
            {"label": "Development",         "pct": 15},
            {"label": "Liquidity",           "pct": 5},
        ],
    },
    # ── treasury reserves (pct of the Treasury/DAO allocation) ───
    "treasury": {
        "address": "",             # optional: live native balance is read if set
        "reserves": [
            {"label": "Operational Reserve", "pct": 30, "purpose": "Infrastructure, RPC, hosting, AI credits"},
            {"label": "Development Reserve", "pct": 35, "purpose": "Core platform engineering & audits"},
            {"label": "Community Reserve",   "pct": 20, "purpose": "Contributor rewards & bounties"},
            {"label": "Ecosystem Reserve",   "pct": 15, "purpose": "Grants, integrations, partnerships"},
        ],
    },
    # ── token utility (access & participation, never speculation) ─
    "utilities": [
        {"name": "Community governance",         "stake": 250,  "category": "governance"},
        {"name": "Premium AI models",            "stake": 500,  "category": "access"},
        {"name": "Higher AI-credit limits",      "stake": 750,  "category": "credits"},
        {"name": "Advanced autonomous agents",   "stake": 1000, "category": "access"},
        {"name": "Marketplace participation",    "stake": 300,  "category": "marketplace"},
        {"name": "Plugin & agent incentives",    "stake": 400,  "category": "marketplace"},
        {"name": "Developer rewards program",    "stake": 1500, "category": "developer"},
        {"name": "Early-access programs",        "stake": 200,  "category": "access"},
    ],
    # optional integrations — left blank; the layer degrades gracefully
    "explorer_api_key": "",        # Etherscan-compatible key → real tx history
    "walletconnect_project_id": "",  # WalletConnect Cloud id → QR pairing
    "rpc_overrides": {},           # {"1": "https://your-node"}
}


class ConfigService:
    def __init__(self) -> None:
        self.data = copy.deepcopy(DEFAULTS)
        try:
            saved = json.loads(FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self._merge(self.data, saved)
        except Exception:
            pass

    @staticmethod
    def _merge(base: dict, patch: dict) -> None:
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                ConfigService._merge(base[k], v)
            else:
                base[k] = v

    def save(self) -> None:
        try:
            FILE.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        except Exception:
            pass

    def update(self, patch: dict) -> dict:
        self._merge(self.data, patch or {})
        self.save()
        return self.data

    # ── accessors ────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return bool(self.data.get("enabled"))

    def set_enabled(self, on: bool) -> None:
        self.data["enabled"] = bool(on)
        self.save()

    def chain(self, chain_id: int | None = None) -> dict:
        cid = int(chain_id or self.data.get("default_chain") or 1)
        info = dict(CHAINS.get(cid, {"name": f"Chain {cid}", "symbol": "ETH",
                                     "rpc": "", "explorer": ""}))
        override = (self.data.get("rpc_overrides") or {}).get(str(cid))
        if override:
            info["rpc"] = override
        info["chain_id"] = cid
        return info

    def known_chains(self) -> list[dict]:
        return [{"chain_id": cid, "name": c["name"], "symbol": c["symbol"]}
                for cid, c in CHAINS.items()]
