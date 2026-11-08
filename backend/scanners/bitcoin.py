"""Bitcoin via the mempool.space REST API (no key needed; self-host for scale).

Docs: https://mempool.space/docs/api/rest
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from core.models import Asset, Chain, Leg, NormalizedTx

from .base import Http, env

BTC = Asset(Chain.BITCOIN, "BTC", decimals=8, coingecko_id="bitcoin")
SATS = Decimal(100_000_000)


class BitcoinScanner:
    """Any Esplora/electrs-style explorer API (Bitcoin here; Dogecoin reuses it)."""
    CHAIN, ASSET = Chain.BITCOIN, BTC

    def __init__(self, http: Http | None = None):
        self.http = http or Http(env("MEMPOOL_BASE_URL", "https://mempool.space/api"), rate_per_sec=4)

    async def scan(self, address: str, since_cursor: str | None = None) -> AsyncIterator[NormalizedTx]:
        last_seen: str | None = None
        while True:
            path = f"/address/{address}/txs/chain" + (f"/{last_seen}" if last_seen else "")
            page = await self.http.get(path)   # 25 confirmed txs per page, newest first
            if not page:
                return
            for raw in page:
                if raw["txid"] == since_cursor:
                    return
                yield self.normalize(raw, address)
            last_seen = page[-1]["txid"]
            if len(page) < 25:
                return

    @classmethod
    def normalize(cls, raw: dict, address: str) -> NormalizedTx:
        sent_sats = sum(v["prevout"]["value"] for v in raw["vin"]
                        if v.get("prevout") and v["prevout"].get("scriptpubkey_address") == address)
        recv_sats = sum(v["value"] for v in raw["vout"] if v.get("scriptpubkey_address") == address)
        paid_fee = sent_sats > 0
        fee_sats = raw.get("fee", 0) if paid_fee else 0
        net = recv_sats - sent_sats + fee_sats   # movement excluding the fee

        if net < 0:
            cp = next((v.get("scriptpubkey_address") for v in raw["vout"]
                       if v.get("scriptpubkey_address") not in (address, None)), None)
        else:
            cp = next((v["prevout"].get("scriptpubkey_address") for v in raw["vin"]
                       if v.get("prevout") and v["prevout"].get("scriptpubkey_address") != address), None)

        ts = raw.get("status", {}).get("block_time")
        legs = [Leg(cls.ASSET, Decimal(net) / SATS, counterparty=cp)] if net else []
        return NormalizedTx(
            chain=cls.CHAIN, tx_hash=raw["txid"],
            timestamp=datetime.fromtimestamp(ts, timezone.utc) if ts else datetime.now(timezone.utc),
            wallet=address, legs=legs,
            fee=Decimal(fee_sats) / SATS, fee_asset=cls.ASSET if paid_fee else None,
        )

    async def balance(self, address: str) -> dict[str, Decimal]:
        d = await self.http.get(f"/address/{address}")
        cs = d["chain_stats"]
        return {self.ASSET.key: Decimal(cs["funded_txo_sum"] - cs["spent_txo_sum"]) / SATS}
