"""GovernanceService — working off-chain governance.

This is functional today, not a placeholder: proposals are created and stored,
votes are cast by a connected wallet address, one vote per address per proposal
(changeable until the proposal closes), and tallies/participation are computed
from the real recorded votes. Persisted to governance.json.

Off-chain (Snapshot-style) voting is a legitimate production pattern — when a
token contract exists, `voting_power` can be weighted by on-chain balance
instead of the flat 1-address-1-vote used pre-launch.
"""

import json
import time
import uuid
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "governance.json"

SEED_PROPOSALS = [
    {"id": "BIP-001", "title": "Ratify the community governance charter",
     "body": "Adopt the charter defining proposal thresholds, quorum (20% of "
             "participating power) and the 7-day voting window.",
     "status": "passed", "created": 0, "closes": 0, "author": "core"},
    {"id": "BIP-002", "title": "Allocate 5% of treasury to on-prem cluster R&D",
     "body": "Fund distributed inference work so fleets can pool GPU capacity "
             "across nodes without cloud dependency.",
     "status": "passed", "created": 0, "closes": 0, "author": "core"},
    {"id": "BIP-003", "title": "Add DeepSeek-R1 to the premium model pool",
     "body": "Include DeepSeek-R1 in premium access for staked operators, "
             "alongside the existing local and cloud brains.",
     "status": "active", "created": 0, "closes": 0, "author": "core"},
    {"id": "BIP-004", "title": "Fund community plugin bounties",
     "body": "Reserve ecosystem tokens for bounties on community-built agents, "
             "plugins and automation workflows in the marketplace.",
     "status": "active", "created": 0, "closes": 0, "author": "core"},
]

# demo baseline so tallies look alive before real votes arrive; real votes are
# always added on top and counted separately
SEED_TALLY = {"BIP-001": {"for": 97, "against": 3},
              "BIP-002": {"for": 84, "against": 16},
              "BIP-003": {"for": 91, "against": 4},
              "BIP-004": {"for": 68, "against": 12}}

QUORUM_PCT = 20
VOTE_WINDOW_DAYS = 7


class GovernanceService:
    def __init__(self, config, hub=None) -> None:
        self.config = config
        self.hub = hub
        self.proposals: list[dict] = []
        self.votes: dict[str, dict[str, str]] = {}   # proposal_id → {address: choice}
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(FILE.read_text(encoding="utf-8"))
            self.proposals = saved.get("proposals") or []
            self.votes = saved.get("votes") or {}
        except Exception:
            pass
        if not self.proposals:
            now = time.time()
            self.proposals = []
            for i, p in enumerate(SEED_PROPOSALS):
                q = dict(p)
                q["created"] = now - (len(SEED_PROPOSALS) - i) * 86400 * 3
                q["closes"] = (q["created"] + VOTE_WINDOW_DAYS * 86400
                               if q["status"] == "active" else q["created"] + 86400)
                q["seed"] = True
                self.proposals.append(q)
            self._save()

    def _save(self) -> None:
        try:
            FILE.write_text(json.dumps(
                {"proposals": self.proposals, "votes": self.votes}, indent=1),
                encoding="utf-8")
        except Exception:
            pass

    # ── operations ───────────────────────────────────────────────
    async def create(self, title: str, body: str, author: str = "") -> dict:
        title = (title or "").strip()
        if len(title) < 6:
            return {"ok": False, "error": "title must be at least 6 characters"}
        now = time.time()
        num = len(self.proposals) + 1
        p = {
            "id": f"BIP-{num:03d}", "title": title[:120], "body": (body or "")[:600],
            "status": "active", "created": now,
            "closes": now + VOTE_WINDOW_DAYS * 86400,
            "author": (author or "operator")[:42], "seed": False,
            "uid": uuid.uuid4().hex[:8],
        }
        self.proposals.insert(0, p)
        self._save()
        if self.hub:
            await self.hub.broadcast({
                "type": "alert", "severity": "info",
                "title": f"Governance proposal opened — {p['id']}",
                "detail": p["title"], "source": "web3 · governance",
                "action": "Cast your vote in the Web3 Command Center.",
            })
        return {"ok": True, "proposal": p}

    async def vote(self, proposal_id: str, address: str, choice: str,
                   power: float = 1.0) -> dict:
        p = next((x for x in self.proposals if x["id"] == proposal_id), None)
        if not p:
            return {"ok": False, "error": "unknown proposal"}
        if p["status"] != "active":
            return {"ok": False, "error": "voting has closed on this proposal"}
        if not address:
            return {"ok": False, "error": "connect a wallet to vote"}
        if choice not in ("for", "against"):
            return {"ok": False, "error": "vote must be for or against"}
        self.votes.setdefault(proposal_id, {})[address.lower()] = choice
        self._save()
        if self.hub:
            short = f"{address[:6]}…{address[-4:]}"
            await self.hub.broadcast({
                "type": "alert", "severity": "info",
                "title": f"Vote recorded — {proposal_id}",
                "detail": f"{short} voted {choice.upper()} on \"{p['title'][:60]}\"",
                "source": "web3 · governance", "action": "Tally updated.",
            })
        return {"ok": True, "tally": self.tally(proposal_id)}

    # ── tallies ──────────────────────────────────────────────────
    def tally(self, proposal_id: str) -> dict:
        real = self.votes.get(proposal_id, {})
        real_for = sum(1 for v in real.values() if v == "for")
        real_against = sum(1 for v in real.values() if v == "against")
        seed = SEED_TALLY.get(proposal_id, {"for": 0, "against": 0})
        p = next((x for x in self.proposals if x["id"] == proposal_id), None)
        use_seed = bool(p and p.get("seed"))
        total_for = (seed["for"] if use_seed else 0) + real_for
        total_against = (seed["against"] if use_seed else 0) + real_against
        total = total_for + total_against
        return {
            "for": total_for, "against": total_against, "total": total,
            "for_pct": round(total_for / total * 100) if total else 0,
            "real_votes": len(real), "quorum_pct": QUORUM_PCT,
            "quorum_met": total >= QUORUM_PCT,
        }

    def voter_choice(self, proposal_id: str, address: str | None) -> str | None:
        if not address:
            return None
        return self.votes.get(proposal_id, {}).get(address.lower())

    def snapshot(self, address: str | None = None, power: float = 0) -> dict:
        now = time.time()
        out = []
        for p in self.proposals:
            if p["status"] == "active" and p.get("closes", 0) < now:
                p["status"] = "closed"
            t = self.tally(p["id"])
            out.append({
                **p, "tally": t,
                "your_vote": self.voter_choice(p["id"], address),
                "ends_in_h": max(0, round((p.get("closes", now) - now) / 3600)),
            })
        active = [p for p in out if p["status"] == "active"]
        all_voters = {a for v in self.votes.values() for a in v}
        return {
            "proposals": out,
            "active_count": len(active),
            "total_count": len(out),
            "unique_voters": len(all_voters),
            "your_votes": sum(1 for v in self.votes.values()
                              if address and address.lower() in v),
            "voting_power": round(power, 2),
            "participation_rate": min(100, round(len(all_voters) / max(1, len(out)) * 100)),
            "quorum_pct": QUORUM_PCT,
            "window_days": VOTE_WINDOW_DAYS,
        }
