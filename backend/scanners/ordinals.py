"""Bitcoin Ordinals: the inscriptions an address holds, shown as NFTs.

Two sources, best first:
  • Xverse API (XVERSE_API_KEY, from api-dashboard.xverse.app): every
    inscription with its collection name and floor price. Hiro's Ordinals API,
    the usual free source, was shut down in 2026 and points here.
  • ordinals.com (the public server of `ord`, the reference indexer): no key,
    no collection names or floors. /address/<a> lists the inscription ids,
    POST /inscriptions returns their details in batches, and /content/<id>
    is the inscription itself (used as the picture for images).

Text and JSON inscriptions (BRC-20 transfers, plain text) aren't shown as NFTs.
Read-only; nothing is stored.
"""
from __future__ import annotations

from decimal import Decimal

from core.models import Asset, Chain, safe_image

from .base import Http, ProviderError, env

SATS = Decimal(100_000_000)
SHOWN = ("image/", "text/html", "model/", "video/")   # what counts as an NFT to show
FLOORS: dict[str, Decimal] = {}   # Xverse collection id -> floor in BTC, filled as wallets are read


def _ord_url() -> str:
    return env("ORD_URL", "https://ordinals.com").rstrip("/")


def _picture(iid: str, ctype: str, render: str | None = None) -> str | None:
    if ctype.startswith("image/"):
        return safe_image(f"{_ord_url()}/content/{iid}")
    return safe_image(render) if render else None   # HTML/3D art: only if a rendered image is offered


class OrdinalsClient:
    def __init__(self, xverse: Http | None = None, ord_http: Http | None = None):
        key = env("XVERSE_API_KEY", "")
        self.xverse = xverse or (Http(env("XVERSE_API_URL", "https://api.secretkeylabs.io"), rate_per_sec=4,
                                      headers={"x-api-key": key}) if key else None)
        self.ord = ord_http or Http(_ord_url(), rate_per_sec=4, headers={"Accept": "application/json"})

    async def held(self, address: str) -> list[Asset]:
        if self.xverse is not None:
            try:
                return await self._xverse(address)
            except ProviderError:
                pass   # fall back to the free indexer
        return await self._ord(address)

    async def _xverse(self, address: str) -> list[Asset]:
        res = await self.xverse.get(f"/v1/ordinals/address/{address}/inscriptions")
        out = []
        for it in (res or {}).get("items") or []:
            iid = it.get("id")
            ctype = (it.get("effectiveContentType") or it.get("contentType") or "").lower()
            if not iid or not ctype.startswith(SHOWN):
                continue
            coll_id, coll_name = it.get("collectionId"), it.get("collectionName")
            floor = it.get("collectionFloorPrice")
            sats = floor.get("valueInSats") if isinstance(floor, dict) else floor
            if coll_id and isinstance(sats, (int, float, str)) and str(sats).replace(".", "", 1).isdigit() and Decimal(str(sats)) > 0:
                FLOORS[coll_id] = Decimal(str(sats)) / SATS
            number = it.get("number")
            name = it.get("name") or (f"{coll_name} #{number}" if coll_name and number is not None
                                      else f"Inscription #{number}" if number is not None else f"Inscription {iid[:8]}…")
            out.append(Asset(Chain.BITCOIN, (coll_name or "ORDINAL")[:16], contract=iid, decimals=0, is_nft=True,
                             token_id=iid, name=name, collection=coll_id or "ordinals",
                             image=_picture(iid, ctype, it.get("renderUrl"))))
        return out

    async def _ord(self, address: str) -> list[Asset]:
        res = await self.ord.get(f"/address/{address}")
        ids = [i for i in ((res or {}).get("inscriptions") or []) if isinstance(i, str)][:2000]
        details = []
        for k in range(0, len(ids), 100):
            batch = await self.ord.post("/inscriptions", json=ids[k:k + 100])
            details += batch if isinstance(batch, list) else []
        out = []
        for d in details:
            iid = d.get("id")
            ctype = (d.get("effective_content_type") or d.get("content_type") or "").lower()
            if not iid or not ctype.startswith(SHOWN):
                continue
            parents = d.get("parents") or []
            number = d.get("number")
            out.append(Asset(Chain.BITCOIN, "ORDINAL", contract=iid, decimals=0, is_nft=True, token_id=iid,
                             name=f"Inscription #{number}" if number is not None else f"Inscription {iid[:8]}…",
                             collection=parents[0] if parents else "ordinals", image=_picture(iid, ctype)))
        return out
