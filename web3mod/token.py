"""TokenService — token metadata & supply.

Two modes, switched purely by configuration:
  · PRE-LAUNCH  no contract set → the operator-configured figures are shown
                and clearly flagged as `live: false`
  · LIVE        `token.contract` set → name/symbol/decimals/totalSupply are
                read from the ERC-20 contract over RPC on every refresh

That means adopting a real token later is a config change, not a rewrite.
"""

import time

from .blockchain import (SEL_DECIMALS, SEL_NAME, SEL_SYMBOL, SEL_TOTAL_SUPPLY,
                         dec_string, dec_uint)


class TokenService:
    def __init__(self, config, chain_svc) -> None:
        self.config = config
        self.chain = chain_svc
        self._live_cache: tuple[dict, float] | None = None

    async def _read_contract(self) -> dict | None:
        tok = self.config.data["token"]
        contract = (tok.get("contract") or "").strip()
        if not contract:
            return None
        if self._live_cache and time.time() - self._live_cache[1] < 60:
            return self._live_cache[0]
        cid = self.config.data.get("default_chain")
        dec_raw = await self.chain.call(contract, SEL_DECIMALS, chain_id=cid)
        decimals = dec_uint(dec_raw)
        if decimals is None:
            return None                     # contract unreachable → stay configured
        supply_raw = await self.chain.call(contract, SEL_TOTAL_SUPPLY, chain_id=cid)
        supply = dec_uint(supply_raw)
        name = dec_string(await self.chain.call(contract, SEL_NAME, chain_id=cid))
        symbol = dec_string(await self.chain.call(contract, SEL_SYMBOL, chain_id=cid))
        out = {
            "decimals": decimals,
            "total_supply": (supply / (10 ** decimals)) if supply is not None else None,
            "name": name, "symbol": symbol,
        }
        self._live_cache = (out, time.time())
        return out

    async def snapshot(self) -> dict:
        tok = dict(self.config.data["token"])
        live = await self._read_contract()
        if live:
            tok.update({k: v for k, v in live.items() if v is not None})
            tok["live"] = True
            tok["launched"] = True
        else:
            tok["live"] = False

        total = tok.get("total_supply") or 0
        allocations = []
        for a in tok.get("allocations", []):
            allocations.append({**a, "tokens": round(total * a.get("pct", 0) / 100)})
        tok["allocations"] = allocations
        tok["treasury_tokens"] = next(
            (a["tokens"] for a in allocations if "Treasury" in a["label"]), 0)
        tok["community_tokens"] = next(
            (a["tokens"] for a in allocations if "Community" in a["label"]), 0)
        tok["ecosystem_tokens"] = next(
            (a["tokens"] for a in allocations if "Ecosystem" in a["label"]), 0)
        tok["development_tokens"] = next(
            (a["tokens"] for a in allocations if "Development" in a["label"]), 0)
        circ = tok.get("circulating") or 0
        tok["circulating_pct"] = round(circ / total * 100) if total else 0
        tok["utilities"] = self.config.data.get("utilities", [])
        # market data requires a listing — never invented
        tok["market"] = None if not tok["live"] else tok.get("market")
        return tok
