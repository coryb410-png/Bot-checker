"""NFT floor prices: what the cheapest listing in a collection costs now.

  Solana   → Magic Eden public API. An NFT's mint maps to its collection
             symbol (/v2/tokens/{mint}); the symbol has floor stats
             (/v2/collections/{symbol}/stats, floor in lamports). One lookup
             per collection, not per NFT, and results are cached.
  Ethereum → OpenSea API v2 when OPENSEA_API_KEY is set, otherwise (or if
             OpenSea has no floor) Alchemy's getFloorPrice (OpenSea, then
             LooksRare) with the existing ALCHEMY_API_KEY.
  Cardano  → OpenCNFT (no key; their terms ask for a credit link, which the
             gallery shows next to each floor). jpg.store closed its API in 2026.
  Robinhood Chain, Base, Arbitrum, Optimism, Polygon → OpenSea API v2
             (needs OPENSEA_API_KEY): an NFT's contract + token id gives its
             collection slug, and the slug's stats give the floor.
  XRP      → XRP Ledger Services collection stats (no key), per issuer and
             collection number (taxon); floors are given in drops.

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

# OpenSea's names for the networks it prices for us
OPENSEA_CHAINS = {Chain.ETHEREUM: "ethereum", Chain.ROBINHOOD: "robinhood", Chain.BASE: "base", Chain.ARBITRUM: "arbitrum",
                  Chain.OPTIMISM: "optimism", Chain.POLYGON: "polygon"}
# Floor currencies we can turn into dollars (wrapped coins price like the coin)
FLOOR_CURRENCY = {"ETH": "ETH", "WETH": "ETH", "POL": "POL", "MATIC": "POL", "WPOL": "POL", "WMATIC": "POL"}
LAMPORTS = Decimal(1_000_000_000)   # per SOL
LOVELACE = Decimal(1_000_000)       # per ADA
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
    def __init__(self, me: Http | None = None, alchemy_nft: Http | None = None,
                 opencnft: Http | None = None, xrpldata: Http | None = None, opensea: Http | None = None):
        self._me = me
        self._alchemy = alchemy_nft
        self._opencnft = opencnft
        self._xrpldata = xrpldata
        self._opensea = opensea
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

    @property
    def opencnft(self) -> Http:
        if self._opencnft is None:
            self._opencnft = Http(env("OPENCNFT_API_URL", "https://api.opencnft.io/1"), rate_per_sec=2)
        return self._opencnft

    @property
    def opensea(self) -> Http:
        if self._opensea is None:
            self._opensea = Http(env("OPENSEA_API_URL", "https://api.opensea.io/api/v2"), rate_per_sec=3,
                                 headers={"x-api-key": env("OPENSEA_API_KEY", ""), "accept": "application/json"})
        return self._opensea

    @property
    def xrpldata(self) -> Http:
        if self._xrpldata is None:
            self._xrpldata = Http(env("XRPLDATA_API_URL", "https://api.xrpldata.com/api/v1"), rate_per_sec=2)
        return self._xrpldata

    def _cached(self, key: str) -> tuple[bool, Floor | None]:
        hit = self._floors.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return True, hit[1]
        return False, None

    async def floors_for(self, nfts: list[Asset]) -> dict[str, Floor]:
        """Floor per collection key for the given NFTs, best effort."""
        groups: dict[str, Asset] = {}
        opensea_on = bool(env("OPENSEA_API_KEY", "")) or self._opensea is not None
        for a in nfts:
            if a.is_nft and not a.spam and (a.chain in (Chain.SOLANA, Chain.ETHEREUM, Chain.CARDANO, Chain.XRP)
                                             or (a.chain in OPENSEA_CHAINS and opensea_on)):
                groups.setdefault(collection_key(a), a)

        out: dict[str, Floor] = {}

        async def one(key: str, sample: Asset) -> None:
            fresh, floor = self._cached(key)
            if not fresh:
                try:
                    floor = await {Chain.SOLANA: lambda: self._solana(key, sample),
                                   Chain.ETHEREUM: lambda: self._ethereum_any(key, sample, opensea_on),
                                   Chain.CARDANO: lambda: self._cardano(sample),
                                   Chain.XRP: lambda: self._xrp(sample)}.get(
                                       sample.chain, lambda: self._opensea_floor(key, sample))()
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

    async def _opensea_floor(self, key: str, sample: Asset) -> Floor | None:
        chain = OPENSEA_CHAINS.get(sample.chain)
        if not chain or not sample.contract or sample.token_id is None:
            return None
        if key not in self._symbols:   # collection slug, looked up once per collection
            try:
                res = await self.opensea.get(f"/chain/{chain}/contract/{sample.contract}/nfts/{sample.token_id}")
                self._symbols[key] = ((res.get("nft") or {}).get("collection")) or None
            except ProviderError:
                self._symbols[key] = None   # not on OpenSea
        slug = self._symbols[key]
        if not slug:
            return None
        stats = await self.opensea.get(f"/collections/{slug}/stats")
        total = stats.get("total") or {}
        fp, sym = total.get("floor_price"), (total.get("floor_price_symbol") or "ETH").upper()
        currency = FLOOR_CURRENCY.get(sym)
        if not isinstance(fp, (int, float)) or fp <= 0 or not currency:
            return None
        return Floor(Decimal(str(fp)), currency, "OpenSea", f"https://opensea.io/collection/{slug}")

    async def _ethereum_any(self, key: str, sample: Asset, opensea_on: bool) -> Floor | None:
        if opensea_on:
            try:
                floor = await self._opensea_floor(key, sample)
                if floor:
                    return floor
            except ProviderError as e:
                log.warning("OpenSea floor unavailable for %s, trying Alchemy: %s", key, str(e)[:200])
        return await self._ethereum(sample)

    async def _cardano(self, sample: Asset) -> Floor | None:
        policy = sample.collection or (sample.contract or "")[:56]
        res = await self.opencnft.get(f"/policy/{policy}/floor_price")
        raw = res.get("floor_price", res.get("floor")) if isinstance(res, dict) else None
        if not isinstance(raw, (int, float, str)) or not Decimal(str(raw)) > 0:
            return None
        value = Decimal(str(raw))
        # Documented without units: lovelace in practice. Anything this large
        # can't be an ADA floor (100,000 ADA), so treat it as lovelace.
        ada = value / LOVELACE if value >= 100_000 else value
        return Floor(ada, "ADA", "OpenCNFT", "https://opencnft.io")   # credit link their terms ask for

    async def _xrp(self, sample: Asset) -> Floor | None:
        issuer, _, taxon = (sample.collection or "").partition(":")
        if not issuer:
            return None
        path = f"/xls20-nfts/stats/issuer/{issuer}" + (f"/taxon/{taxon}" if taxon else "")
        res = await self.xrpldata.get(path)
        info = ((res or {}).get("data") or {}).get("collection_info") or {}
        for f in info.get("floor") or []:
            if (f or {}).get("currency") == "XRP" and f.get("amount"):
                drops = Decimal(str(f["amount"]))
                if drops > 0:
                    return Floor(drops / Decimal(1_000_000), "XRP", "XRP Ledger Services", "https://xrpldata.com")
        return None
