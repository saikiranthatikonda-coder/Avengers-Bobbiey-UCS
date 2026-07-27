"""Web3 layer for the Bobbiey Command Platform — optional, modular subsystem.

The AI command platform is the product. This package is an independently
maintainable extension that can be enabled or disabled at runtime without
affecting anything else. Import surface is deliberately tiny.

Services (each in its own module, each usable standalone):
    ConfigService      persisted, operator-editable Web3/token configuration
    BlockchainService  live JSON-RPC chain data (block, gas, latency, health)
    WalletService      connected wallet + real native / ERC-20 balances
    TokenService       token metadata & supply (config, or live from contract)
    GovernanceService  proposals + real, persisted, one-per-address voting
    TreasuryService    reserve allocations, movements, optional live balance
    MarketplaceService agent/plugin/workflow listings with persisted installs
    AnalyticsService   portfolio value history + on-chain event log
"""

from .service import Web3Service

__all__ = ["Web3Service"]
