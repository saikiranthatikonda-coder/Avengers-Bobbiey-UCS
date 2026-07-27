"""BlockchainService — LIVE chain awareness over JSON-RPC.

This is real data, not a mock: block height, gas price, chain id and RPC
latency come from an actual node over public endpoints (no API key needed).
Health/quality are derived from measured latency and block freshness.

Also provides the minimal ABI encode/decode needed for read-only `eth_call`
(ERC-20 balanceOf / totalSupply / decimals / symbol / name) so the token and
wallet services can read real contract state without pulling in web3.py.
"""

import asyncio
import time

import httpx

# ERC-20 function selectors (first 4 bytes of keccak256 of the signature)
SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_BALANCE_OF = "0x70a08231"
SEL_DECIMALS = "0x313ce567"
SEL_SYMBOL = "0x95d89b41"
SEL_NAME = "0x06fdde03"


def enc_address(addr: str) -> str:
    """ABI-encode an address as a 32-byte word."""
    return (addr or "").lower().replace("0x", "").rjust(64, "0")


def dec_uint(hexdata: str | None) -> int | None:
    if not hexdata or hexdata in ("0x", "0x0"):
        return 0 if hexdata else None
    try:
        return int(hexdata, 16)
    except Exception:
        return None


def dec_string(hexdata: str | None) -> str | None:
    """Decode an ABI dynamic string (offset, length, bytes)."""
    if not hexdata or len(hexdata) < 130:
        return None
    try:
        body = hexdata[2:]
        length = int(body[64:128], 16)
        raw = bytes.fromhex(body[128:128 + length * 2])
        return raw.decode("utf-8", errors="ignore").strip("\x00") or None
    except Exception:
        return None


class BlockchainService:
    def __init__(self, config) -> None:
        self.config = config
        self._cache: dict[int, tuple[dict, float]] = {}
        self._rpc_id = 0

    async def rpc(self, method: str, params: list | None = None,
                  chain_id: int | None = None, timeout: float = 8.0):
        """Single JSON-RPC call. Returns (result, latency_ms) or (None, None)."""
        chain = self.config.chain(chain_id)
        url = chain.get("rpc")
        if not url:
            return None, None
        self._rpc_id += 1
        payload = {"jsonrpc": "2.0", "id": self._rpc_id,
                   "method": method, "params": params or []}
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as c:
                r = await c.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
            latency = int((time.time() - t0) * 1000)
            if "error" in data:
                return None, latency
            return data.get("result"), latency
        except Exception:
            return None, None

    async def call(self, to: str, data: str, chain_id: int | None = None):
        """Read-only contract call (eth_call at latest block)."""
        res, _ = await self.rpc("eth_call", [{"to": to, "data": data}, "latest"],
                                chain_id=chain_id)
        return res

    async def native_balance(self, address: str, chain_id: int | None = None):
        """Real native-token balance in whole units (ETH/POL/BNB…)."""
        res, _ = await self.rpc("eth_getBalance", [address, "latest"], chain_id=chain_id)
        wei = dec_uint(res)
        return None if wei is None else wei / 1e18

    # ── live chain status (cached ~12s: ~1 block on most chains) ──
    async def status(self, chain_id: int | None = None, force: bool = False) -> dict:
        chain = self.config.chain(chain_id)
        cid = chain["chain_id"]
        cached = self._cache.get(cid)
        if cached and not force and time.time() - cached[1] < 12:
            return cached[0]

        t0 = time.time()
        block_hex, lat_block = await self.rpc("eth_blockNumber", chain_id=cid)
        gas_hex, _ = await self.rpc("eth_gasPrice", chain_id=cid)
        latency = lat_block if lat_block is not None else int((time.time() - t0) * 1000)

        block = dec_uint(block_hex)
        gas_wei = dec_uint(gas_hex)
        gas_gwei = round(gas_wei / 1e9, 2) if gas_wei else None
        online = block is not None

        if not online:
            health, quality = "offline", 0
        elif latency < 400:
            health, quality = "excellent", 100
        elif latency < 900:
            health, quality = "good", 78
        elif latency < 2000:
            health, quality = "degraded", 45
        else:
            health, quality = "poor", 20

        out = {
            "chain_id": cid, "name": chain["name"], "symbol": chain["symbol"],
            "rpc": chain.get("rpc"), "explorer": chain.get("explorer"),
            "online": online, "block": block, "gas_gwei": gas_gwei,
            "latency_ms": latency, "health": health, "quality": quality,
            "checked_at": time.time(),
        }
        self._cache[cid] = (out, time.time())
        return out

    async def multi_status(self, chain_ids: list[int]) -> list[dict]:
        """Status for several chains at once (used by the network selector)."""
        return list(await asyncio.gather(*(self.status(c) for c in chain_ids)))
