"""Dogecoin. Same coin model as Bitcoin (UTXOs, 1 DOGE = 100,000,000 koinu).

Primary:  electrs-dogecoin, a Dogecoin port of the explorer API the Bitcoin
          scanner already uses (DOGE_ESPLORA_URL, default doge-electrs-demo.qed.me;
          community-run, no key, no rate limit).
Backup:   BlockCypher (BLOCKCYPHER_API_URL; optional free BLOCKCYPHER_TOKEN raises
          its limits from about 100 requests an hour). Used automatically if
          the primary can't be reached.

Doginals (Dogecoin NFTs: inscriptions written onto Dogecoin, like Bitcoin
Ordinals) aren't visible to an ordinary explorer, so the NFTs a wallet holds
come from Maestro's Dogecoin inscription API (MAESTRO_API_KEY, free account;
MAESTRO_DOGE_URL). Images are each inscription's own content, shown from a
public Doginals explorer (DOGINALS_CONTENT_URL, "{id}" is replaced). With no
key set, Doginals are simply skipped.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from core.models import Asset, Chain, Leg, NormalizedTx, safe_image

from .base import Http, ProviderError, env
from .bitcoin import BitcoinScanner

log = logging.getLogger("chainscope.dogecoin")
DOGE = Asset(Chain.DOGECOIN, "DOGE", decimals=8, coingecko_id="dogecoin")
KOINU = Decimal(100_000_000)


class DogecoinScanner(BitcoinScanner):
    CHAIN, ASSET = Chain.DOGECOIN, DOGE

    def __init__(self, http: Http | None = None, backup: Http | None = None):
        super().__init__(http or Http(env("DOGE_ESPLORA_URL", "https://doge-electrs-demo.qed.me"), rate_per_sec=4))
        self._backup = backup

    @property
    def backup(self) -> Http:
        if self._backup is None:
            self._backup = Http(env("BLOCKCYPHER_API_URL", "https://api.blockcypher.com/v1/doge/main"), rate_per_sec=2)
        return self._backup

    async def scan(self, address: str, since_cursor: str | None = None) -> AsyncIterator[NormalizedTx]:
        got_any = False
        try:
            async for tx in super().scan(address, since_cursor):
                got_any = True
                yield tx
            return
        except ProviderError as e:
            if got_any:
                raise   # failed part-way: let the scan report it rather than mix sources
            log.warning("Dogecoin explorer unavailable (%s); using BlockCypher", str(e)[:150])
        async for tx in self._scan_blockcypher(address, since_cursor):
            yield tx

    async def _scan_blockcypher(self, address: str, since_cursor: str | None) -> AsyncIterator[NormalizedTx]:
        token = env("BLOCKCYPHER_TOKEN", "")
        before = None
        for _ in range(200):   # 50 per page
            params = {"limit": 50, "txlimit": 500}
            if before:
                params["before"] = before
            if token:
                params["token"] = token
            page = await self.backup.get(f"/addrs/{address}/full", params=params)
            txs = page.get("txs") or []
            for raw in txs:
                if raw.get("hash") == since_cursor:
                    return
                if raw.get("block_height", -1) < 0:
                    continue   # not confirmed yet
                yield self.normalize_blockcypher(raw, address)
            if not page.get("hasMore") or not txs:
                return
            before = min(t.get("block_height", 0) for t in txs if t.get("block_height", -1) >= 0)

    async def held_nfts(self, owner: str) -> list[Asset]:
        """Doginals this address holds (needs MAESTRO_API_KEY; none without it)."""
        return await DoginalsClient().held(owner)

    @staticmethod
    def normalize_blockcypher(raw: dict, address: str) -> NormalizedTx:
        sent = sum(int(i.get("output_value") or 0) for i in raw.get("inputs") or [] if address in (i.get("addresses") or []))
        recv = sum(int(o.get("value") or 0) for o in raw.get("outputs") or [] if address in (o.get("addresses") or []))
        paid = sent > 0
        fee = int(raw.get("fees") or 0) if paid else 0
        net = recv - sent + fee
        if net < 0:
            cp = next((a for o in raw.get("outputs") or [] for a in (o.get("addresses") or []) if a != address), None)
        else:
            cp = next((a for i in raw.get("inputs") or [] for a in (i.get("addresses") or []) if a != address), None)
        when = raw.get("confirmed") or raw.get("received")
        ts = datetime.fromisoformat(when.replace("Z", "+00:00")) if when else datetime.now(timezone.utc)
        return NormalizedTx(Chain.DOGECOIN, raw["hash"], ts, address,
                            [Leg(DOGE, Decimal(net) / KOINU, counterparty=cp)] if net else [],
                            fee=Decimal(fee) / KOINU, fee_asset=DOGE if paid else None)


# ---------------------------------------------------------------- Doginals
IMAGE_TYPES = ("image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp", "image/avif", "image/svg+xml")


def _rows(res) -> list:
    """Maestro wraps results as {"data": [...]} (sometimes {"data": {"items": [...]}})."""
    d = (res or {}).get("data", res) if isinstance(res, dict) else res
    if isinstance(d, dict):
        d = d.get("items") or d.get("inscriptions") or []
    return d if isinstance(d, list) else []


class DoginalsClient:
    def __init__(self, http: Http | None = None):
        key = env("MAESTRO_API_KEY", "")
        self.enabled = bool(key) or http is not None
        self.http = http or (Http(env("MAESTRO_DOGE_URL", "https://doge-mainnet.gomaestro-api.org/v0"), rate_per_sec=4,
                                  headers={"api-key": key}) if key else None)

    async def held(self, address: str) -> list[Asset]:
        if not self.enabled:
            return []
        items, cursor = [], None
        for _ in range(20):   # 100 per page: up to 2,000 inscriptions
            params = {"count": 100}
            if cursor:
                params["cursor"] = cursor
            res = await self.http.get(f"/addresses/{address}/inscriptions", params=params)
            items += _rows(res)
            cursor = (res or {}).get("next_cursor") if isinstance(res, dict) else None
            if not cursor:
                break
        out = []
        for it in items:
            iid = it.get("inscription_id") or it.get("id") if isinstance(it, dict) else it
            if not iid:
                continue
            ctype = (it.get("content_type") or "") if isinstance(it, dict) else ""
            if not ctype:
                try:   # older listings don't include the type: look it up
                    info = await self.http.get(f"/assets/inscriptions/{iid}")
                    info = (info or {}).get("data", info) or {}
                    ctype = info.get("content_type") or ""
                    if isinstance(it, dict):
                        it = {**info, **it}
                except ProviderError:
                    continue
            if not ctype.lower().startswith(IMAGE_TYPES):
                continue   # text inscriptions (e.g. DRC-20 token transfers) aren't NFTs to show
            coll = it.get("collection") if isinstance(it, dict) else None
            coll_name = (coll.get("name") if isinstance(coll, dict) else coll) or None
            number = it.get("inscription_number") or it.get("number") if isinstance(it, dict) else None
            name = it.get("name") if isinstance(it, dict) else None
            name = name or (f"{coll_name} #{number}" if coll_name and number is not None
                            else f"Doginal #{number}" if number is not None else f"Doginal {iid[:8]}…")
            image = env("DOGINALS_CONTENT_URL", "https://wonky-ord.dogeord.io/content/{id}").replace("{id}", iid)
            collection = (coll.get("id") or coll.get("slug") if isinstance(coll, dict) else None) or coll_name or iid
            out.append(Asset(Chain.DOGECOIN, (coll_name or "DOGINAL")[:16], contract=iid, decimals=0, is_nft=True,
                             token_id=iid, name=name, image=safe_image(image), collection=collection))
        return out

