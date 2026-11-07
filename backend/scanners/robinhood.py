"""Robinhood Chain (chain ID 4663) via its public Blockscout explorer API.

Robinhood Chain is an Ethereum layer 2 (Arbitrum Orbit) whose gas token is
ETH, so every 0x address a user adds is scanned here as well as on Ethereum.
Blockscout's REST API needs no key.

Docs: https://docs.blockscout.com/devs/apis/rest
Explorer: https://robinhoodchain.blockscout.com (override with ROBINHOOD_BLOCKSCOUT_URL)
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from core.models import Asset, Chain, Leg, NormalizedTx, safe_image

from .base import Http, env
from .ethereum import _merge_legs

ETH = Asset(Chain.ROBINHOOD, "ETH", decimals=18, coingecko_id="ethereum")
WEI = Decimal(10) ** 18
MAX_PAGES = 400   # 50 items per page: up to 20,000 records per list


def _ts(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _hash(party) -> str:
    return ((party or {}).get("hash") or "").lower()


class RobinhoodScanner:
    def __init__(self, http: Http | None = None):
        base = env("ROBINHOOD_BLOCKSCOUT_URL", "https://robinhoodchain.blockscout.com").rstrip("/")
        self.http = http or Http(f"{base}/api/v2", rate_per_sec=4)

    async def _pages(self, path: str, since_hash: str | None, hash_field: str) -> list[dict]:
        """Walk a Blockscout list (newest first) until the last-seen tx hash."""
        out, params = [], {}
        for _ in range(MAX_PAGES):
            res = await self.http.get(path, params=params or None)
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
                              image=safe_image(inst.get("image_url") or (inst.get("metadata") or {}).get("image")),
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
