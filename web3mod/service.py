"""Web3Service — the facade that wires the sub-services together.

Kept deliberately thin: it owns lifecycle (enable/disable) and composes one
snapshot for the dashboard. Each sub-service stays independently testable and
replaceable. When the module is disabled, the platform behaves exactly as if
this package did not exist.
"""

from .analytics import AnalyticsService
from .blockchain import BlockchainService
from .config import ConfigService
from .governance import GovernanceService
from .marketplace import MarketplaceService
from .token import TokenService
from .treasury import TreasuryService
from .wallet import WalletService


class Web3Service:
    def __init__(self, hub=None) -> None:
        self.hub = hub
        self.config = ConfigService()
        self.analytics = AnalyticsService()
        self.chain = BlockchainService(self.config)
        self.wallet = WalletService(self.config, self.chain, self.analytics)
        self.token = TokenService(self.config, self.chain)
        self.governance = GovernanceService(self.config, hub=hub)
        self.treasury = TreasuryService(self.config, self.chain, hub=hub)
        self.marketplace = MarketplaceService(self.config, hub=hub)

    # ── lifecycle ────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return self.config.enabled

    async def set_enabled(self, on: bool) -> dict:
        self.config.set_enabled(on)
        if not on:
            self.wallet.disconnect()
        self.analytics.log("module", f"Web3 module {'enabled' if on else 'disabled'}")
        if self.hub:
            await self.hub.broadcast({
                "type": "log", "level": "info",
                "msg": f"Web3 Command Center {'ENABLED' if on else 'disabled'} (optional module)"})
            await self.hub.broadcast({"type": "web3", "enabled": on})
        return {"ok": True, "enabled": on}

    # ── wallet ───────────────────────────────────────────────────
    async def connect_wallet(self, address: str, chain_id=None, label: str = "") -> dict:
        res = await self.wallet.connect(address, chain_id, label)
        if res.get("ok"):
            w = res["wallet"]
            short = f"{address[:6]}…{address[-4:]}"
            self.analytics.log("wallet", f"{short} connected on {w.get('network')}")
            if self.hub:
                await self.hub.broadcast({
                    "type": "alert", "severity": "info",
                    "title": "Wallet connected",
                    "detail": f"{short} · {w.get('network')} · "
                              f"{(w.get('native_balance') or 0):.4f} {w.get('native_symbol')}",
                    "source": "web3 · wallet",
                    "action": "Web3 Command Center is live.",
                })
        return res

    async def disconnect_wallet(self) -> dict:
        self.analytics.log("wallet", "wallet disconnected")
        return self.wallet.disconnect()

    def _voting_power(self) -> float:
        """Pre-launch: 1 address = 1 vote. Post-launch: weighted by token balance."""
        w = self.wallet.current
        if not w:
            return 0.0
        if w.get("token_balance"):
            return float(w["token_balance"])
        return 1.0

    # ── composed snapshot for the dashboard ──────────────────────
    async def snapshot(self) -> dict:
        addr = (self.wallet.current or {}).get("address")
        token = await self.token.snapshot()
        chain_id = (self.wallet.current or {}).get("chain_id")
        return {
            "enabled": self.enabled,
            "chain": await self.chain.status(chain_id),
            "chains": self.config.known_chains(),
            "wallet": self.wallet.current,
            "wallet_history": self.wallet.history[:5],
            "token": token,
            "governance": self.governance.snapshot(addr, self._voting_power()),
            "treasury": await self.treasury.snapshot(
                token.get("treasury_tokens", 0), token.get("symbol", "TOKEN")),
            "marketplace": self.marketplace.snapshot(token.get("symbol", "TOKEN")),
            "analytics": self.analytics.snapshot(self.wallet.current),
            "config": {
                "default_chain": self.config.data.get("default_chain"),
                "has_contract": bool((self.config.data["token"].get("contract") or "").strip()),
                "has_explorer_key": bool((self.config.data.get("explorer_api_key") or "").strip()),
                "has_walletconnect": bool((self.config.data.get("walletconnect_project_id") or "").strip()),
            },
        }
