"""XRP Ledger via its public JSON-RPC servers (no key needed).

Docs: https://xrpl.org/docs/references/http-websocket-apis
Server: XRPL_RPC_URL (default https://xrplcluster.com, a free community cluster)

Every XRP Ledger transaction carries metadata listing each ledger object it
changed with before/after values. Instead of interpreting every transaction
type (payments, DEX offers, AMM deposits, NFT offers, checks, escrows…), the
wallet's own balance changes are read straight from that metadata:

  AccountRoot   → the wallet's XRP balance (includes the fee it paid)
  RippleState   → a token balance (a "trust line" between wallet and issuer)
  NFTokenPage   → the NFTs the wallet holds (XLS-20)

That is exact even for partial payments and multi-hop trades.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from core.addresses import XRP_INDEX
from core.models import Asset, Chain, Leg, NormalizedTx, safe_image

from .base import Http, ProviderError, env

log = logging.getLogger("chainscope.xrp")
DROPS = Decimal(1_000_000)
XRP = Asset(Chain.XRP, "XRP", decimals=6, coingecko_id="ripple")
RIPPLE_EPOCH = 946_684_800          # seconds from 1970 to 2000-01-01
PAGE = 200
MAX_TXS = 20_000
MAX_META_FETCHES = 80               # NFT metadata lookups per scan
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif")


def account_id_hex(address: str) -> str:
    """r-address -> 20-byte account ID (hex), as used inside ledger object IDs."""
    n = 0
    for c in address:
        n = n * 58 + XRP_INDEX[c]
    raw = n.to_bytes(25, "big")
    return raw[1:21].hex().upper()


def currency_symbol(code: str) -> str:
    """3-letter codes are plain; 40-hex codes are ASCII padded with zeros,
    except AMM pool shares (prefix 03), shown as LP."""
    if len(code) != 40:
        return code
    if code.startswith("03"):
        return "LP"
    try:
        s = bytes.fromhex(code).rstrip(b"\x00").decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return code[:8]
    return s if s and s.isprintable() else code[:8]


def _uri(hex_uri: str | None) -> str | None:
    if not hex_uri:
        return None
    try:
        return bytes.fromhex(hex_uri).decode("utf-8").strip() or None
    except (ValueError, UnicodeDecodeError):
        return None


def _gateway(url: str) -> str:
    if url.startswith("ipfs://"):
        return "https://ipfs.io/ipfs/" + url[7:].removeprefix("ipfs/")
    return url


class XrpScanner:
    def __init__(self, http: Http | None = None, meta_http: Http | None = None):
        self.http = http or Http(env("XRPL_RPC_URL", "https://xrplcluster.com"), rate_per_sec=4)
        self._meta_http = meta_http
        self._nft_meta: dict[str, dict] = {}   # NFT ID -> metadata JSON
        self._uris: dict[str, str] = {}        # NFT ID -> hex URI seen on the ledger
        self._fetches = 0

    @property
    def meta_http(self) -> Http:
        if self._meta_http is None:
            self._meta_http = Http("", rate_per_sec=6)
        return self._meta_http

    async def _rpc(self, method: str, **params) -> dict:
        res = await self.http.post("/", json={"method": method, "params": [params]})
        r = (res or {}).get("result") or {}
        if r.get("status") == "error":
            if r.get("error") == "actNotFound":
                return {}   # never funded: nothing to show
            raise ProviderError(f"XRPL {method}: {r.get('error_message') or r.get('error')}")
        return r

    # ------------------------------------------------------------ scanning
    async def scan(self, address: str, since_cursor: str | None = None) -> AsyncIterator[NormalizedTx]:
        me_id = account_id_hex(address)
        marker, seen = None, 0
        while seen < MAX_TXS:
            params = dict(account=address, ledger_index_min=-1, ledger_index_max=-1, limit=PAGE,
                          forward=False, api_version=2)
            if marker:
                params["marker"] = marker
            r = await self._rpc("account_tx", **params)
            rows = r.get("transactions") or []
            batch = []
            for row in rows:
                h = row.get("hash") or (row.get("tx_json") or row.get("tx") or {}).get("hash")
                if since_cursor and h == since_cursor:
                    rows = []
                    marker = None
                    break
                if row.get("validated") is False:
                    continue
                tx = self.normalize(row, address, me_id)
                if tx:
                    batch.append(tx)
            await self._attach_nft_meta(batch)
            for tx in batch:
                yield tx
            seen += len(rows)
            marker = r.get("marker") if rows else None
            if not marker:
                break

    def normalize(self, row: dict, me: str, me_id: str) -> NormalizedTx | None:
        tx = row.get("tx_json") or row.get("tx") or {}
        meta = row.get("meta") or {}
        if not isinstance(meta, dict):
            return None
        h = row.get("hash") or tx.get("hash")
        xrp_delta = Decimal(0)
        tokens: dict[tuple[str, str], Decimal] = defaultdict(Decimal)   # (currency, issuer) -> change
        nfts: dict[str, int] = defaultdict(int)
        uris: dict[str, str] = {}

        for node in meta.get("AffectedNodes") or []:
            kind, n = next(iter(node.items()))
            t = n.get("LedgerEntryType")
            final = n.get("FinalFields") or n.get("NewFields") or {}
            prev = n.get("PreviousFields") or {}
            if t == "AccountRoot" and final.get("Account") == me:
                if kind == "CreatedNode":
                    xrp_delta += Decimal(int(final.get("Balance") or 0))
                elif kind == "DeletedNode":
                    xrp_delta -= Decimal(int(prev.get("Balance") or final.get("Balance") or 0))
                elif "Balance" in prev:
                    xrp_delta += Decimal(int(final["Balance"])) - Decimal(int(prev["Balance"]))
            elif t == "RippleState":
                low = (final.get("LowLimit") or {}).get("issuer")
                high = (final.get("HighLimit") or {}).get("issuer")
                if me not in (low, high):
                    continue
                bal = final.get("Balance") or {}
                after = Decimal(bal.get("value") or 0) if kind != "DeletedNode" else Decimal(0)
                if kind == "CreatedNode":
                    before = Decimal(0)
                elif kind == "DeletedNode":
                    before = Decimal(((prev.get("Balance") or bal).get("value")) or 0)
                elif "Balance" in prev:
                    before = Decimal(prev["Balance"].get("value") or 0)
                else:
                    continue
                change = after - before            # from the low side's point of view
                if me == low:
                    tokens[(bal.get("currency"), high)] += change
                else:
                    tokens[(bal.get("currency"), low)] -= change
            elif t == "NFTokenPage" and (n.get("LedgerIndex") or "")[:40].upper() == me_id:
                before = prev.get("NFTokens") if "NFTokens" in prev else (final.get("NFTokens") if kind != "CreatedNode" else [])
                after = [] if kind == "DeletedNode" else final.get("NFTokens") or []
                if kind == "DeletedNode":
                    before = prev.get("NFTokens") or final.get("NFTokens") or []
                for sign, items in ((-1, before or []), (1, after)):
                    for item in items:
                        tok = item.get("NFToken") or item
                        nid = tok.get("NFTokenID")
                        if nid:
                            nfts[nid] += sign
                            if tok.get("URI"):
                                uris[nid] = tok["URI"]
                                self._uris[nid] = tok["URI"]

        sender = tx.get("Account")
        fee = Decimal(int(tx.get("Fee") or 0)) if sender == me else Decimal(0)
        move = (xrp_delta + fee) / DROPS        # the fee is reported separately
        other = tx.get("Destination") if sender == me else sender

        legs = []
        if move:
            legs.append(Leg(XRP, move, counterparty=other))
        for (cur, issuer), amount in tokens.items():
            if amount:
                sym = currency_symbol(cur or "")
                legs.append(Leg(Asset(Chain.XRP, sym, contract=f"{cur}.{issuer}", decimals=6,
                                      name=sym if sym != "LP" else "AMM pool share"), amount, counterparty=other))
        for nid, qty in nfts.items():
            if qty:
                legs.append(Leg(self.nft_asset(nid, uris.get(nid)), Decimal(qty), counterparty=other))

        if not legs and not fee:
            return None
        if row.get("close_time_iso"):
            ts = datetime.fromisoformat(row["close_time_iso"].replace("Z", "+00:00"))
        else:
            ts = datetime.fromtimestamp(int(tx.get("date") or 0) + RIPPLE_EPOCH, timezone.utc)
        return NormalizedTx(Chain.XRP, h, ts, me, legs, fee=fee / DROPS, fee_asset=XRP if fee else None,
                            success=meta.get("TransactionResult", "tesSUCCESS") == "tesSUCCESS",
                            interacted_with=other)

    # ------------------------------------------------------------ NFTs
    def nft_asset(self, nft_id: str, hex_uri: str | None = None, meta: dict | None = None) -> Asset:
        meta = meta or self._nft_meta.get(nft_id) or {}
        issuer = nft_id[8:48]                   # every NFT ID embeds its issuer's account ID
        serial = int(nft_id[56:64], 16) if len(nft_id) >= 64 else 0
        coll = meta.get("collection")
        coll_name = coll.get("name") if isinstance(coll, dict) else coll if isinstance(coll, str) else None
        name = meta.get("name") or (f"{coll_name} #{serial}" if coll_name else f"XRPL NFT #{serial}")
        image = meta.get("image") or meta.get("image_url")
        uri = _uri(hex_uri)
        if not image and uri and uri.lower().split("?")[0].endswith(IMAGE_EXT):
            image = uri
        return Asset(Chain.XRP, (coll_name or "NFT")[:16], contract=nft_id, decimals=0, is_nft=True, token_id=nft_id,
                     name=name, image=safe_image(_gateway(image)) if isinstance(image, str) else None,
                     collection=issuer)

    async def _fetch_meta(self, nft_id: str, hex_uri: str | None) -> None:
        uri = _uri(hex_uri)
        if nft_id in self._nft_meta or not uri or self._fetches >= MAX_META_FETCHES:
            return
        if uri.lower().split("?")[0].endswith(IMAGE_EXT) or not uri.startswith(("ipfs://", "https://", "http://")):
            self._nft_meta[nft_id] = {}
            return
        self._fetches += 1
        try:
            data = await asyncio.wait_for(self.meta_http.get(_gateway(uri)), 8)
            self._nft_meta[nft_id] = data if isinstance(data, dict) else {}
        except Exception:
            self._nft_meta[nft_id] = {}   # metadata host down: name/photo stay generic

    async def _attach_nft_meta(self, txs: list[NormalizedTx]) -> None:
        """Fill in NFT names and photos from each NFT's metadata (its URI)."""
        ids = {l.asset.token_id for t in txs for l in t.legs if l.asset.is_nft}
        await asyncio.gather(*(self._fetch_meta(i, self._uris.get(i)) for i in ids))
        for t in txs:
            for l in t.legs:
                if l.asset.is_nft and self._nft_meta.get(l.asset.token_id):
                    fresh = self.nft_asset(l.asset.token_id, self._uris.get(l.asset.token_id))
                    l.asset = replace(l.asset, name=fresh.name, image=fresh.image or l.asset.image,
                                      symbol=fresh.symbol)

    async def held_nfts(self, owner: str) -> list[Asset]:
        nfts, marker = [], None
        for _ in range(25):
            params = dict(account=owner, limit=400, ledger_index="validated")
            if marker:
                params["marker"] = marker
            r = await self._rpc("account_nfts", **params)
            nfts += r.get("account_nfts") or []
            marker = r.get("marker")
            if not marker:
                break
        await asyncio.gather(*(self._fetch_meta(n["NFTokenID"], n.get("URI")) for n in nfts))
        return [self.nft_asset(n["NFTokenID"], n.get("URI")) for n in nfts]
