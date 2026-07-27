"""WalletService — connected wallet state with REAL on-chain balances.

The browser owns the keys: connection/signing happen client-side via an
EIP-1193 provider (MetaMask, Rabby, Coinbase, any EIP-6963 wallet). The server
never sees a private key and never signs. It records the address the client
reports, then independently verifies balances over RPC — so the numbers shown
are read from chain, not trusted from the client.
"""

import time

from .blockchain import SEL_BALANCE_OF, SEL_DECIMALS, dec_uint, enc_address


class WalletService:
    def __init__(self, config, chain_svc, analytics=None) -> None:
        self.config = config
        self.chain = chain_svc
        self.analytics = analytics
        self.current: dict | None = None
        self.history: list[dict] = []      # connection log (address, ts)

    async def connect(self, address: str, chain_id=None, label: str = "") -> dict:
        address = (address or "").strip()
        if not address.startswith("0x") or len(address) != 42:
            return {"ok": False, "error": "invalid wallet address"}
        try:
            cid = int(chain_id) if chain_id is not None else self.config.data["default_chain"]
        except Exception:
            cid = self.config.data["default_chain"]
        self.current = {
            "address": address, "chain_id": cid, "label": (label or "")[:32],
            "connected_at": time.time(),
        }
        self.history.insert(0, {"address": address, "chain_id": cid, "ts": time.time()})
        self.history = self.history[:10]
        await self.refresh()
        return {"ok": True, "wallet": self.current}

    def disconnect(self) -> dict:
        self.current = None
        return {"ok": True}

    async def refresh(self) -> dict | None:
        """Re-read live balances for the connected wallet."""
        if not self.current:
            return None
        addr = self.current["address"]
        cid = self.current["chain_id"]
        chain = self.config.chain(cid)

        native = await self.chain.native_balance(addr, chain_id=cid)
        self.current["native_balance"] = native
        self.current["native_symbol"] = chain["symbol"]
        self.current["network"] = chain["name"]
        self.current["explorer"] = chain.get("explorer")

        # platform token balance — real when a contract is configured
        tok = self.config.data["token"]
        contract = (tok.get("contract") or "").strip()
        if contract:
            raw = await self.chain.call(contract, SEL_BALANCE_OF + enc_address(addr), chain_id=cid)
            bal = dec_uint(raw)
            dec_raw = await self.chain.call(contract, SEL_DECIMALS, chain_id=cid)
            decimals = dec_uint(dec_raw) or tok.get("decimals", 18)
            self.current["token_balance"] = (bal / (10 ** decimals)) if bal is not None else None
            self.current["token_live"] = bal is not None
        else:
            self.current["token_balance"] = None
            self.current["token_live"] = False
        self.current["token_symbol"] = tok.get("symbol", "TOKEN")
        self.current["refreshed_at"] = time.time()

        if self.analytics is not None:
            self.analytics.sample_portfolio(self.current)
        return self.current

    async def transactions(self, limit: int = 8) -> dict:
        """Recent transactions.

        Real history needs an indexer: set `explorer_api_key` (Etherscan-
        compatible) in the Web3 config and this returns live transfers.
        Without a key we return an empty list and say so — never fake rows
        dressed up as real ones.
        """
        if not self.current:
            return {"source": "none", "transactions": []}
        key = (self.config.data.get("explorer_api_key") or "").strip()
        if not key:
            return {"source": "unavailable", "transactions": [],
                    "note": "add an Etherscan-compatible explorer_api_key to load real transaction history"}
        import httpx
        chain = self.config.chain(self.current["chain_id"])
        base = (chain.get("explorer") or "").replace("https://", "https://api.")
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as c:
                r = await c.get(f"{base}/api", params={
                    "module": "account", "action": "txlist",
                    "address": self.current["address"], "page": 1, "offset": limit,
                    "sort": "desc", "apikey": key})
                r.raise_for_status()
                data = r.json()
            txs = []
            for t in (data.get("result") or [])[:limit]:
                if not isinstance(t, dict):
                    continue
                txs.append({
                    "hash": t.get("hash", "")[:18] + "…",
                    "full_hash": t.get("hash"),
                    "direction": "out" if (t.get("from", "").lower()
                                           == self.current["address"].lower()) else "in",
                    "value": round(int(t.get("value", 0)) / 1e18, 5),
                    "ts": int(t.get("timeStamp", 0)),
                    "ok": t.get("isError") == "0",
                })
            return {"source": "explorer", "transactions": txs}
        except Exception as e:
            return {"source": "error", "transactions": [], "note": str(e)[:120]}

    def snapshot(self) -> dict:
        return {"wallet": self.current, "history": self.history[:5]}
