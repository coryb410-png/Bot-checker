"""NFT floor prices: what the cheapest listing in a collection costs now.

  Solana   → Magic Eden public API. An NFT's mint maps to its collection
             symbol (/v2/tokens/{mint}); the symbol has floor stats
             (/v2/collections/{symbol}/stats, floor in lamports). One lookup
             per collection, not per NFT, and results are cached.
  Ethereum → Alchemy NFT API getFloorPrice (OpenSea, then LooksRare), using
             the existing ALCHEMY_API_KEY.

Floors are an estimate of what an NFT would sell for, not a price paid. Any
collection without a floor (unlisted, cNFTs Magic Eden doesn't index, a
provider outage) is simply left unvalued. Never fails a scan.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from core.models import Asset, Chain
from scanners.base import Http, ProviderError, env

log = logging.getLogger("chainscope.floors")
LAMPORTS = Decimal(1_000_000_000)
CACHE_SECONDS = 15 * 60
TIME_BUDGET_SECONDS = 75


@dataclass
class Floor:
    amount: Decimal          # in the chain's native coin
    currency: str            # "SOL" or "ETH"
    source: str              # "Magic Eden", "OpenSea", "LooksRare"
    url: str | None = None
    collection_name: str | None = None


def collection_key(a: Asset) -> str:
    """One key per collection: Solana uses the on-chain collection address
    (falls back to the mint), EVM chains use the contract."""
    return f"{a.chain.value}:{a.collection or a.contract or a.token_id}"


class FloorService:
    def __init__(self, me: Http | None = None, alchemy_nft: Http | None = None):
        self._me = me
        self._alchemy = alchemy_nft
        self._symbols: dict[str, str | None] = {}            # collection key -> ME symbol
        self._floors: dict[str, tuple[float, Floor | None]] = {}   # cache key -> (time, floor)

    @property
    def me(self) -> Http:
        if self._me is None:
            key = env("MAGICEDEN_API_KEY", "")
            self._me = Http(env("MAGICEDEN_API_URL", "https://api-mainnet.magiceden.dev/v2"), rate_per_sec=2,
                            headers={"Authorization": f"Bearer {key}"} if key else None)
        return self._me

    @property
    def alchemy(self) -> Http:
        if self._alchemy is None:
            self._alchemy = Http(f"https://eth-mainnet.g.alchemy.com/nft/v3/{env('ALCHEMY_API_KEY')}", rate_per_sec=5)
        return self._alchemy

    def _cached(self, key: str) -> tuple[bool, Floor | None]:
        hit = self._floors.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return True, hit[1]
        return False, None

    async def floors_for(self, nfts: list[Asset]) -> dict[str, Floor]:
        """Floor per collection key for the given NFTs, best effort."""
        groups: dict[str, Asset] = {}
        for a in nfts:
            if a.is_nft and not a.spam and a.chain in (Chain.SOLANA, Chain.ETHEREUM):
                groups.setdefault(collection_key(a), a)

        out: dict[str, Floor] = {}

        async def one(key: str, sample: Asset) -> None:
            fresh, floor = self._cached(key)
            if not fresh:
                try:
                    floor = await (self._solana(key, sample) if sample.chain == Chain.SOLANA
                                   else self._ethereum(sample))
                except Exception as e:   # one collection failing never matters to the rest
                    log.warning("floor unavailable for %s: %s", key, str(e)[:200])
                    floor = None
                self._floors[key] = (time.time(), floor)
            if floor:
                out[key] = floor

        tasks = [one(k, a) for k, a in groups.items()]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), TIME_BUDGET_SECONDS)
        except asyncio.TimeoutError:
            log.warning("floor lookup hit its time budget; %d of %d collections valued", len(out), len(groups))
        return out

    async def _solana(self, key: str, sample: Asset) -> Floor | None:
        if key not in self._symbols:
            try:
                tok = await self.me.get(f"/tokens/{sample.token_id or sample.contract}")
                self._symbols[key] = tok.get("collection") or None
                name = tok.get("collectionName")
            except ProviderError:
                self._symbols[key] = None   # not on Magic Eden (common for cNFTs)
                name = None
        else:
            name = None
        symbol = self._symbols[key]
        if not symbol:
            return None
        stats = await self.me.get(f"/collections/{symbol}/stats")
        lamports = stats.get("floorPrice")
        if not lamports:
            return None
        return Floor(Decimal(int(lamports)) / LAMPORTS, "SOL", "Magic Eden",
                     f"https://magiceden.io/marketplace/{symbol}", name)

    async def _ethereum(self, sample: Asset) -> Floor | None:
        res = await self.alchemy.get("/getFloorPrice", params={"contractAddress": sample.contract})
        for market, label in (("openSea", "OpenSea"), ("looksRare", "LooksRare")):
            m = res.get(market) or {}
            fp = m.get("floorPrice")
            if isinstance(fp, (int, float)) and fp > 0 and (m.get("priceCurrency") or "ETH").upper() == "ETH":
                return Floor(Decimal(str(fp)), "ETH", label, m.get("collectionUrl"))
        return None
