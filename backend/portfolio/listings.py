"""NFTs the wallets have listed for sale, across marketplaces.

  • OpenSea (API v2, needs OPENSEA_API_KEY): one call per 0x address returns
    its active listings on every network OpenSea supports (Ethereum, Base,
    Arbitrum, Optimism, Polygon, Robinhood Chain…).
  • Magic Eden (Solana): the wallet's tokens with listStatus=listed.

Fetched on demand when someone opens the "Listed" tab, kept in memory for a
few minutes (like everything else here, never written anywhere).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from core import monitor
from core.models import Chain
from scanners.base import Http, env

CACHE_SECONDS = 180
STABLECOINS = {"USDC", "USDT", "USDG", "DAI", "USDC.E", "USDBC", "PYUSD"}
COIN_FOR_CURRENCY = {"ETH": "ETH", "WETH": "ETH", "POL": "POL", "MATIC": "POL", "WPOL": "POL", "SOL": "SOL"}
OPENSEA_CHAIN = {"ethereum": Chain.ETHEREUM, "base": Chain.BASE, "arbitrum": Chain.ARBITRUM,
                 "optimism": Chain.OPTIMISM, "polygon": Chain.POLYGON, "matic": Chain.POLYGON,
                 "robinhood": Chain.ROBINHOOD}
EVM = {Chain.ETHEREUM, Chain.BASE, Chain.ARBITRUM, Chain.OPTIMISM, Chain.POLYGON, Chain.ROBINHOOD}


def _opensea_http() -> Http:
    return Http(env("OPENSEA_API_URL", "https://api.opensea.io/api/v2"), rate_per_sec=3,
                headers={"x-api-key": env("OPENSEA_API_KEY", ""), "accept": "application/json"})


def _me_http() -> Http:
    key = env("MAGICEDEN_API_KEY", "")
    return Http(env("MAGICEDEN_API_URL", "https://api-mainnet.magiceden.dev/v2"), rate_per_sec=2,
                headers={"Authorization": f"Bearer {key}"} if key else None)


def _amount(value, decimals) -> Decimal | None:
    try:
        return Decimal(str(value)) / (Decimal(10) ** int(decimals))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _iso(ts) -> str | None:
    try:
        t = int(ts)
    except (TypeError, ValueError):
        return None
    if t <= 0 or t > 4102444800:   # 0 / "never" sentinels
        return None
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


async def opensea_listings(http: Http, owner: str) -> list[dict]:
    """Raw rows: chain, contract, token_id, price, currency, expires_at, quantity, url."""
    out, params = [], {"limit": 100}
    for _ in range(20):   # up to 2,000 listings
        res = await http.get(f"/account/{owner}/listings", params=params)
        for l in res.get("listings") or []:
            chain = OPENSEA_CHAIN.get((l.get("chain") or "").lower())
            params_ = ((l.get("protocol_data") or {}).get("parameters") or {})
            offer = next((o for o in params_.get("offer") or [] if int(o.get("itemType", 0)) >= 2), None)
            cur = ((l.get("price") or {}).get("current") or {})
            if not chain or not offer:
                continue
            contract, tid = (offer.get("token") or "").lower(), str(offer.get("identifierOrCriteria") or "")
            out.append({
                "marketplace": "OpenSea", "chain": chain, "owner": owner, "contract": contract, "token_id": tid,
                "price": _amount(cur.get("value"), cur.get("decimals", 18)),
                "currency": (cur.get("currency") or "ETH").upper(),
                "quantity": l.get("remaining_quantity") or 1,
                "expires_at": _iso(params_.get("endTime")),
                "url": f"https://opensea.io/item/{l.get('chain')}/{contract}/{tid}",
            })
        nxt = res.get("next")
        if not nxt:
            break
        params = {"limit": 100, "after": nxt}
    return out


async def magiceden_listings(http: Http, owner: str) -> list[dict]:
    out, offset = [], 0
    for _ in range(10):   # 500 per page
        res = await http.get(f"/wallets/{owner}/tokens", params={"listStatus": "listed", "offset": offset, "limit": 500})
        rows = res if isinstance(res, list) else (res.get("results") or [])
        for t in rows:
            mint = t.get("mintAddress") or t.get("tokenMint")
            price = t.get("price") if t.get("price") is not None else t.get("listPrice")
            if not mint or price in (None, 0, "0") or (t.get("listStatus") not in (None, "listed")):
                continue
            out.append({
                "marketplace": "Magic Eden", "chain": Chain.SOLANA, "owner": owner, "contract": mint, "token_id": mint,
                "price": Decimal(str(price)), "currency": "SOL", "quantity": 1, "expires_at": None,
                "url": f"https://magiceden.io/item-details/{mint}",
                "name": t.get("name"), "image": t.get("image"), "collection_name": t.get("collectionName") or t.get("collection"),
            })
        if len(rows) < 500:
            break
        offset += 500
    return out


async def fetch_all(addresses: list[tuple[Chain, str]]) -> tuple[list[dict], list[dict]]:
    """All active listings for the portfolio's addresses, plus which sources were checked."""
    owners_evm = sorted({a.lower() for c, a in addresses if c in EVM})
    owners_sol = sorted({a for c, a in addresses if c == Chain.SOLANA})
    sources, jobs = [], []

    if owners_evm:
        if env("OPENSEA_API_KEY", ""):
            http = _opensea_http()
            jobs += [("OpenSea", o, opensea_listings(http, o)) for o in owners_evm]
        else:
            sources.append({"name": "OpenSea", "ok": False, "error": "OPENSEA_API_KEY isn't set"})
    if owners_sol:
        http = _me_http()
        jobs += [("Magic Eden", o, magiceden_listings(http, o)) for o in owners_sol]

    results = await asyncio.gather(*(j[2] for j in jobs), return_exceptions=True)
    rows, status = [], {}
    for (name, _owner, _), r in zip(jobs, results):
        if isinstance(r, Exception):
            status[name] = monitor.scrub(str(r), 160) or "failed"
            monitor.record("provider_error", f"listings {name}: {r}")
        else:
            rows += r
            status.setdefault(name, None)
    sources += [{"name": n, "ok": err is None, "error": err} for n, err in status.items()]
    return rows, sources


def _usd(amount: Decimal | None, currency: str, native_usd: dict) -> Decimal | None:
    if amount is None:
        return None
    if currency in STABLECOINS:
        return amount
    coin = COIN_FOR_CURRENCY.get(currency)
    px = native_usd.get(coin) if coin else None
    return amount * Decimal(str(px)) if px else None


async def listings_for(p, holdings: list[dict], wallet: str | None, refresh: bool = False) -> dict:
    """Listings for the portfolio (or one wallet in it), joined with what the
    scan knows about each NFT: name, picture, floor and what was paid."""
    cache = getattr(p, "listings_cache", None) or {}
    if refresh or not cache or time.time() - cache.get("at", 0) > CACHE_SECONDS:
        rows, sources = await fetch_all(p.addresses)
        cache = {"at": time.time(), "rows": rows, "sources": sources,
                 "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        p.listings_cache = cache

    by_key = {h["key"].lower(): h for h in holdings if h.get("nft")}
    out = []
    for r in cache["rows"]:
        if wallet and r["owner"].lower() != wallet.lower():
            continue
        chain: Chain = r["chain"]
        key = f"{chain.value}:nft:{r['contract']}:{r['token_id']}"
        h = by_key.get(key.lower()) or {}
        price_usd = _usd(r["price"], r["currency"], p.native_usd or {})
        floor = h.get("floor") or None
        floor_usd = Decimal(floor["usd"]) if floor and floor.get("usd") else None
        paid = Decimal(h["cost_basis"]) if h.get("cost_basis") else None
        out.append({
            "marketplace": r["marketplace"], "chain": chain.value, "owner": r["owner"], "key": key,
            "name": h.get("name") or r.get("name") or f"#{r['token_id'][:12]}",
            "image": h.get("image") or r.get("image"),
            "collection": h.get("collection") or f"{chain.value}:{r['contract']}",
            "collection_name": r.get("collection_name"),
            "price": f"{r['price'].normalize():f}" if r["price"] is not None else None, "currency": r["currency"],
            "price_usd": f"{price_usd:.2f}" if price_usd is not None else None,
            "floor": floor, "paid_usd": f"{paid:.2f}" if paid is not None else None,
            "vs_floor_pct": float(round((price_usd / floor_usd - 1) * 100, 1)) if price_usd and floor_usd else None,
            "quantity": r["quantity"], "expires_at": r["expires_at"], "url": r["url"],
        })
    out.sort(key=lambda x: Decimal(x["price_usd"]) if x["price_usd"] else Decimal(-1), reverse=True)
    total = sum((Decimal(x["price_usd"]) for x in out if x["price_usd"]), Decimal(0))
    return {"listings": out, "count": len(out), "total_usd": f"{total:.2f}",
            "sources": cache["sources"], "checked_at": cache["checked_at"]}
