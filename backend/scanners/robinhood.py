"""Robinhood Chain (chain ID 4663) via its public Blockscout explorer API.

Robinhood Chain is an Ethereum layer 2 (Arbitrum Orbit) whose gas token is
ETH, so every 0x address a user adds is scanned here as well as on Ethereum.

With BLOCKSCOUT_API_KEY set (free at dev.blockscout.com) requests go through
Blockscout's keyed API (api.blockscout.com/4663/api/v2). Without it they go
to the public explorer, which may block automated requests (403).

Docs: https://docs.blockscout.com/devs/apis/rest
Explorer: https://robinhoodchain.blockscout.com (override with ROBINHOOD_BLOCKSCOUT_URL)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from core.models import Asset, Chain, Leg, NormalizedTx, safe_image

from .base import Http, ProviderError, env
from .ethereum import _merge_legs

ETH = Asset(Chain.ROBINHOOD, "ETH", decimals=18, coingecko_id="ethereum")
WEI = Decimal(10) ** 18
MAX_PAGES = 400   # 50 items per page: up to 20,000 records per list
CHAIN_ID = 4663


def _ts(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _hash(party) -> str:
    return ((party or {}).get("hash") or "").lower()


class RobinhoodScanner:
    def __init__(self, http: Http | None = None):
        self.keyed = bool(env("BLOCKSCOUT_API_KEY", ""))
        if http is not None:
            self.http = http
        elif self.keyed:
            self.http = Http(f"https://api.blockscout.com/{CHAIN_ID}/api/v2", rate_per_sec=4,
                             params={"apikey": env("BLOCKSCOUT_API_KEY")})
        else:
            base = env("ROBINHOOD_BLOCKSCOUT_URL", "https://robinhoodchain.blockscout.com").rstrip("/")
            self.http = Http(f"{base}/api/v2", rate_per_sec=4)

    async def get(self, path: str, params: dict | None = None) -> dict:
        try:
            return await self.http.get(path, params=params)
        except ProviderError as e:
            if not self.keyed and (" 403" in str(e) or " 401" in str(e)):
                raise ProviderError("Robinhood Chain's explorer blocked the request. Add a free Blockscout API key "
                                    "(BLOCKSCOUT_API_KEY, from dev.blockscout.com) in Render.") from e
            raise

    async def ping(self) -> dict:
        return await self.get("/stats")

    async def _pages(self, path: str, since_hash: str | None, hash_field: str) -> list[dict]:
        """Walk a Blockscout list (newest first) until the last-seen tx hash."""
        out, params = [], {}
        for _ in range(MAX_PAGES):
            res = await self.get(path, params=params or None)
            for item in res.get("items") or []:
                if since_hash and (item.get(hash_field) or "").lower() == since_hash.lower():
                    return out
                out.append(item)
            nxt = res.get("next_page_params")
            if not nxt:
                return out
            params = nxt
        return out

    async def scan(self, address: str, since_cursor: str | None = None) -> AsyncIterator[NormalizedTx]:
        me = address.lower()
        base = f"/addresses/{address}"
        txs = await self._pages(f"{base}/transactions", since_cursor, "hash")
        internal = await self._pages(f"{base}/internal-transactions", since_cursor, "transaction_hash")
        tokens = await self._pages(f"{base}/token-transfers", since_cursor, "transaction_hash")
        for tx in self.normalize(me, txs, internal, tokens):
            yield tx

    async def held_nfts(self, owner: str) -> list[Asset]:
        """Every NFT the wallet owns on Robinhood Chain right now, with photos.
        Catches NFTs the transfer history missed (mints, airdrops, very old
        activity) the same way Alchemy's lookup does on the other networks."""
        out, params = [], {"type": "ERC-721,ERC-404,ERC-1155"}
        for _ in range(100):   # 50 per page: up to 5,000 NFTs
            res = await self.get(f"/addresses/{owner}/nft", params=params)
            for it in res.get("items") or []:
                a = held_asset(it)
                if a:
                    out.append(a)
            nxt = res.get("next_page_params")
            if not nxt:
                break
            params = {"type": params["type"], **nxt}
        return out

    @staticmethod
    def normalize(me: str, txs: list[dict], internal: list[dict], tokens: list[dict]) -> list[NormalizedTx]:
        legs: dict[str, list[Leg]] = defaultdict(list)
        when: dict[str, datetime] = {}
        meta: dict[str, dict] = {}

        for t in txs:
            h = t["hash"]
            when[h] = _ts(t.get("timestamp"))
            sender, receiver = _hash(t.get("from")), _hash(t.get("to"))
            value = Decimal(int(t.get("value") or 0)) / WEI
            ok = t.get("status", "ok") == "ok"
            if value and ok:
                if sender == me:
                    legs[h].append(Leg(ETH, -value, counterparty=receiver or None))
                if receiver == me:
                    legs[h].append(Leg(ETH, value, counterparty=sender or None))
            to = t.get("to") or {}
            meta[h] = {
                "fee": Decimal(int(((t.get("fee") or {}).get("value")) or 0)) / WEI if sender == me else Decimal(0),
                "success": ok,
                "interacted_with": receiver or None,
                "source": (to.get("name") or None) if to.get("is_contract") else None,
            }

        for it in internal:
            if it.get("success") is False:
                continue
            h = it["transaction_hash"]
            when.setdefault(h, _ts(it.get("timestamp")))
            sender, receiver = _hash(it.get("from")), _hash(it.get("to"))
            value = Decimal(int(it.get("value") or 0)) / WEI
            if not value:
                continue
            if sender == me:
                legs[h].append(Leg(ETH, -value, counterparty=receiver or None))
            if receiver == me:
                legs[h].append(Leg(ETH, value, counterparty=sender or None))

        for tt in tokens:
            h = tt["transaction_hash"]
            when.setdefault(h, _ts(tt.get("timestamp")))
            sender, receiver = _hash(tt.get("from")), _hash(tt.get("to"))
            if me not in (sender, receiver):
                continue
            sign = Decimal(1) if receiver == me else Decimal(-1)
            other = sender if sign > 0 else receiver
            tok = tt.get("token") or {}
            contract = (tok.get("address_hash") or tok.get("address") or "").lower()
            total = tt.get("total") or {}
            kind = (tok.get("type") or "").upper()
            spam = tok.get("reputation") == "scam" or bool(tok.get("is_scam"))
            if kind in ("ERC-721", "ERC-1155", "ERC-404") and total.get("token_id") is not None:
                inst = total.get("token_instance") or {}
                name = ((inst.get("metadata") or {}).get("name")) or f"{tok.get('name') or tok.get('symbol') or 'NFT'} #{total['token_id']}"
                asset = Asset(Chain.ROBINHOOD, tok.get("symbol") or "NFT", contract=contract, decimals=0, is_nft=True,
                              token_id=str(total["token_id"]), name=name, collection=contract,
                              image=safe_image((inst.get("metadata") or {}).get("image")) or safe_image(inst.get("image_url")),
                              spam=spam)
                qty = Decimal(int(total.get("value") or 1)) if kind != "ERC-721" else Decimal(1)
            else:
                dec = int(total.get("decimals") or tok.get("decimals") or 18)
                asset = Asset(Chain.ROBINHOOD, tok.get("symbol") or "UNKNOWN", contract=contract, decimals=dec,
                              name=tok.get("name"), image=safe_image(tok.get("icon_url")), spam=spam)
                qty = Decimal(int(total.get("value") or 0)) / (Decimal(10) ** dec)
            if qty:
                legs[h].append(Leg(asset, sign * qty, counterparty=other or None))

        out = []
        for h, ts in when.items():
            m = meta.get(h, {})
            fee = m.get("fee", Decimal(0))
            merged = _merge_legs(legs.get(h, []))
            if not merged and not fee:
                continue   # nothing involving this wallet's balances
            out.append(NormalizedTx(
                chain=Chain.ROBINHOOD, tx_hash=h, timestamp=ts, wallet=me, legs=merged,
                fee=fee, fee_asset=ETH if fee else None,
                source=m.get("source"), interacted_with=m.get("interacted_with"),
                success=m.get("success", True),
            ))
        out.sort(key=lambda t: t.timestamp, reverse=True)
        return out


def held_asset(item: dict) -> Asset | None:
    """One entry of Blockscout's /addresses/{a}/nft list as an NFT asset."""
    tok = item.get("token") or {}
    contract = (tok.get("address_hash") or tok.get("address") or "").lower()
    tid = str(item.get("id") if item.get("id") is not None else "")
    if not contract or not tid:
        return None
    meta = item.get("metadata") or {}
    collection = tok.get("name") or ""
    if not collection or collection.lower().startswith("0x"):   # some contracts report no real name
        collection = tok.get("symbol") or "NFT"
    name = (meta.get("name") or "").strip().rstrip("#").strip() or collection
    if tid not in name and len(tid) <= 12:
        name = f"{name} #{tid}"
    thumbs = item.get("thumbnails") or {}
    # The token's own link first (ipfs:// becomes the ipfs.io gateway); Blockscout's
    # image_url points at dweb.link, the slowest public gateway
    image = (thumbs.get("500x500") or thumbs.get("250x250") or safe_image(meta.get("image"))
             or item.get("image_url") or meta.get("image_url"))
    return Asset(Chain.ROBINHOOD, tok.get("symbol") or "NFT", contract=contract, decimals=0, is_nft=True,
                 token_id=tid, collection=contract, name=name, image=safe_image(image),
                 spam=tok.get("reputation") == "scam" or bool(tok.get("is_scam")))
