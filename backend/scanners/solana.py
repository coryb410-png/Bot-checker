"""Solana via Helius (Enhanced Transactions API + DAS for token metadata).

Docs: https://www.helius.dev/docs  (Enhanced Transactions: parsed history
for an address; DAS getAssetBatch: token/NFT names and symbols)

Helius has moved hostnames before; both base URLs are configurable.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from urllib.parse import parse_qsl, urlsplit
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from classifiers.spam import looks_like_scam, looks_like_scam_description
from core.models import Asset, Chain, Leg, NormalizedTx, safe_image

from .base import Http, env

LAMPORTS = Decimal(1_000_000_000)
SOL = Asset(Chain.SOLANA, "SOL", decimals=9, coingecko_id="solana")
WSOL = "So11111111111111111111111111111111111111112"
KNOWN_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": ("USDC", 6, "usd-coin"),
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": ("USDT", 6, "tether"),
    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN": ("JUP", 6, "jupiter-exchange-solana"),
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": ("BONK", 5, "bonk"),
}
log = logging.getLogger("chainscope.solana")
# Marketplaces Helius doesn't name on its own, by program address.
# Add an entry when a marketplace's trades show without a name.
MARKETPLACE_PROGRAMS: dict[str, str] = {}
# Programs that appear in most transactions and say nothing about the trade
INFRA_PROGRAMS = {
    "ComputeBudget111111111111111111111111111111", "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL", "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s",
    "auth9SigNpDKz4sJJ1DfCTuZrZNSAgh9sFD3rboVmgg", "Sysvar1nstructions1111111111111111111111111",
    "BGUMAp9Gq7iTEuizy4pqaxsTyUCBK68MDfK752saRPUY", "cmtDvXumGCrqC1Age74AVPhSRVXJMd8PJS91L8KbNCK",
    "noopb9bkMVfRPU8AsbpTUg8AQkHtKwMYZiFUjNRtMmV", "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
}
NFT_STANDARDS = {"NonFungible", "ProgrammableNonFungible", "NonFungibleEdition", "Compressed"}


class SolanaScanner:
    def __init__(self, http: Http | None = None, rpc: Http | None = None):
        self.key = env("HELIUS_API_KEY")
        self.http = http or Http(env("HELIUS_API_BASE", "https://api.helius.xyz/v0"), rate_per_sec=8)
        # The key goes in the query string of every call, NOT in the base URL:
        # the HTTP client appends "/" to a base URL, which corrupted the key
        # ("...?api-key=KEY/") and made every name/image lookup fail.
        rpc_url = env("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com")
        parts = urlsplit(rpc_url)
        self.rpc_params = dict(parse_qsl(parts.query)) or {"api-key": self.key}
        self.rpc = rpc or Http(f"{parts.scheme}://{parts.netloc}{parts.path}", rate_per_sec=8)
        self._meta: dict[str, dict] = {}

    async def scan(self, address: str, since_cursor: str | None = None) -> AsyncIterator[NormalizedTx]:
        before = None
        while True:
            params = {"api-key": self.key, "limit": 100}
            if before:
                params["before"] = before
            page = await self.http.get(f"/addresses/{address}/transactions", params=params)
            if not page:
                return
            mints = {t["mint"] for raw in page for t in raw.get("tokenTransfers") or []}
            mints |= {ev["assetId"] for raw in page for ev in _compressed_events(raw) if ev.get("assetId")}
            await self._resolve_mints(mints)
            for raw in page:
                if raw["signature"] == since_cursor:
                    return
                yield self.normalize(raw, address)
            before = page[-1]["signature"]

    async def _resolve_mints(self, mints: set[str]) -> None:
        todo = [m for m in mints if m not in self._meta and m not in KNOWN_MINTS and m != WSOL]
        for i in range(0, len(todo), 1000):
            try:
                res = await self.rpc.post("", params=self.rpc_params, json={
                    "jsonrpc": "2.0", "id": 1, "method": "getAssetBatch", "params": {"ids": todo[i:i + 1000]}})
            except Exception as e:
                log.warning("Helius getAssetBatch failed: %s", str(e)[:200])
                continue   # names/images are optional; the scan carries on
            if res.get("error"):
                log.warning("Helius getAssetBatch error: %s", str(res["error"])[:200])
            for a in res.get("result") or []:
                if a:
                    self._meta[a["id"]] = parse_das_asset(a)

    def _asset(self, mint: str, standard: str | None) -> Asset:
        if mint == WSOL:
            return SOL   # wrapped SOL behaves as SOL for cost basis
        if mint in KNOWN_MINTS:
            sym, dec, cg = KNOWN_MINTS[mint]
            return Asset(Chain.SOLANA, sym, contract=mint, decimals=dec, coingecko_id=cg)
        m = self._meta.get(mint) or {"symbol": mint[:4] + "…", "decimals": 0, "is_nft": False}
        compressed = bool(m.get("compressed")) or standard == "Compressed"
        is_nft = m["is_nft"] or compressed or (standard in NFT_STANDARDS)
        return Asset(Chain.SOLANA, m["symbol"], contract=mint, decimals=m["decimals"], is_nft=is_nft,
                     token_id=mint if is_nft else None, name=m.get("name"), image=m.get("image"),
                     spam=bool(m.get("spam")), compressed=compressed)

    def normalize(self, raw: dict, me: str) -> NormalizedTx:
        net: dict[str, Decimal] = defaultdict(Decimal)
        assets: dict[str, Asset] = {}
        cps: dict[str, str] = {}

        native = Decimal(0)
        for n in raw.get("nativeTransfers") or []:
            amt = Decimal(n["amount"]) / LAMPORTS
            if n.get("toUserAccount") == me:
                native += amt; cps.setdefault(SOL.key, n.get("fromUserAccount"))
            if n.get("fromUserAccount") == me:
                native -= amt; cps.setdefault(SOL.key, n.get("toUserAccount"))

        for t in raw.get("tokenTransfers") or []:
            a = self._asset(t["mint"], t.get("tokenStandard"))
            amt = Decimal(str(t.get("tokenAmount") or 0))
            if t.get("toUserAccount") == me:
                net[a.key] += amt; cps.setdefault(a.key, t.get("fromUserAccount"))
            if t.get("fromUserAccount") == me:
                net[a.key] -= amt; cps.setdefault(a.key, t.get("toUserAccount"))
            assets[a.key] = a

        # Compressed NFTs (cNFTs) don't move as tokens: Helius reports them as
        # compressed events with the old and new owner of the asset.
        for ev in _compressed_events(raw):
            aid = ev.get("assetId")
            if not aid:
                continue
            new, old = ev.get("newLeafOwner"), ev.get("oldLeafOwner")
            if new == old:
                continue   # e.g. delegate or metadata changes
            a = self._asset(aid, "Compressed")
            if new == me:
                net[a.key] += 1; cps.setdefault(a.key, old)
            elif old == me:
                net[a.key] -= 1; cps.setdefault(a.key, new)
            else:
                continue
            assets[a.key] = a

        paid = raw.get("feePayer") == me
        fee = Decimal(raw.get("fee", 0)) / LAMPORTS if paid else Decimal(0)
        # Tiny SOL movements next to a token transfer are account rent
        # (opening token accounts), not trades: count them as fees.
        if net and abs(native) < Decimal("0.003"):
            fee += max(-native, Decimal(0))
            native = Decimal(0)
        if native:
            net[SOL.key] += native
        if SOL.key in net:
            assets[SOL.key] = SOL
        # Marketplace events last, once SOL and token movements are known
        hint = self._apply_nft_event(raw, me, net, assets, cps)

        legs = [Leg(assets[k], v, counterparty=cps.get(k)) for k, v in net.items() if v != 0]
        return NormalizedTx(
            chain=Chain.SOLANA, tx_hash=raw["signature"],
            timestamp=datetime.fromtimestamp(raw["timestamp"], timezone.utc),
            wallet=me, legs=legs, fee=fee, fee_asset=SOL if fee else None,
            source=(raw.get("source") if raw.get("source") not in (None, "UNKNOWN", "SYSTEM_PROGRAM")
                    else next((MARKETPLACE_PROGRAMS[p] for p in _programs(raw) if p in MARKETPLACE_PROGRAMS), None)),
            interacted_with=next((i.get("programId") for i in raw.get("instructions") or []), None),
            success=raw.get("transactionError") is None,
            hint=hint,
            programs=_programs(raw),
        )

    def _apply_nft_event(self, raw: dict, me: str, net: dict, assets: dict, cps: dict) -> str | None:
        """Use Helius's marketplace event to fix what balance changes alone get
        wrong. Escrow marketplaces take the NFT at listing time, so the sale
        transaction shows only SOL arriving; the listing looks like a giveaway."""
        ev = (raw.get("events") or {}).get("nft") or {}
        kind = (raw.get("type") or ev.get("type") or "").upper()
        mints = [n.get("mint") for n in ev.get("nfts") or [] if n.get("mint")]
        if kind in ("NFT_LISTING", "NFT_CANCEL_LISTING") and mints:
            # Moving the NFT into or out of escrow isn't a sale: it's still yours
            for m in mints:
                a = self._asset(m, "NonFungible")
                net.pop(a.key, None)
                assets.pop(a.key, None)
            return "nft_listing" if kind == "NFT_LISTING" else "nft_cancel_listing"
        if kind in ("NFT_BID", "NFT_GLOBAL_BID"):
            return "nft_bid"
        if kind in ("NFT_BID_CANCELLED", "NFT_GLOBAL_BID_CANCELLED"):
            return "nft_cancel_bid"
        if kind != "NFT_SALE" or not mints:
            return None
        price = Decimal(int(ev.get("amount") or 0)) / LAMPORTS
        seller, buyer = ev.get("seller"), ev.get("buyer")
        for m in mints:
            a = self._asset(m, "NonFungible")
            assets[a.key] = a
            if seller == me and net.get(a.key, 0) >= 0:
                net[a.key] = Decimal(-1)          # escrowed NFT leaves at sale time
                cps.setdefault(a.key, buyer)
            elif buyer == me and net.get(a.key, 0) <= 0:
                net[a.key] = Decimal(1)
                cps.setdefault(a.key, seller)
        # If the payment went through other accounts, use the sale price
        if seller == me and net.get(SOL.key, 0) <= 0 and price:
            net[SOL.key] = price; assets[SOL.key] = SOL
        if buyer == me and net.get(SOL.key, 0) >= 0 and price:
            net[SOL.key] = -price; assets[SOL.key] = SOL
        return None


def _programs(raw: dict) -> list[str]:
    """Programs this transaction called, outer and inner, minus plumbing."""
    seen: list[str] = []
    for ins in raw.get("instructions") or []:
        for p in [ins.get("programId")] + [i.get("programId") for i in ins.get("innerInstructions") or []]:
            if p and p not in INFRA_PROGRAMS and p not in seen:
                seen.append(p)
    return seen


def _compressed_events(raw: dict) -> list[dict]:
    ev = (raw.get("events") or {}).get("compressed") or []
    return ev if isinstance(ev, list) else [ev]


def parse_das_asset(a: dict) -> dict:
    """Symbol, decimals, NFT flag, display name and image from a DAS asset."""
    content = a.get("content") or {}
    md = content.get("metadata") or {}
    ti = a.get("token_info") or {}
    links = content.get("links") or {}
    files = content.get("files") or []
    compressed = bool((a.get("compression") or {}).get("compressed"))
    is_nft = compressed or a.get("interface") in ("V1_NFT", "ProgrammableNFT", "MplCoreAsset", "LEGACY_NFT", "V2_NFT")
    # Prefer Helius's image CDN (fast, phone-friendly), then the original link
    image_files = [f for f in files if (f.get("mime") or "").startswith("image") or f.get("cdn_uri")]
    img = next((f["cdn_uri"] for f in image_files if f.get("cdn_uri")), None) \
        or links.get("image") or next((f.get("uri") for f in image_files if f.get("uri")), None)
    return {
        "symbol": ti.get("symbol") or md.get("symbol") or md.get("name") or a["id"][:4] + "…",
        "decimals": ti.get("decimals") or 0,
        "is_nft": is_nft,
        "name": md.get("name"),
        "image": safe_image(img),
        "compressed": compressed,
        # Scam airdrops (very often cNFTs) advertise links in their name or description
        "spam": looks_like_scam(md.get("name")) or looks_like_scam_description(md.get("description")),
    }
