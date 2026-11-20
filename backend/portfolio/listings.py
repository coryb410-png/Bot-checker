"""NFTs the wallets have listed for sale, across marketplaces.

  • OpenSea (API v2, needs OPENSEA_API_KEY): one call per 0x address returns
    its active listings on every network OpenSea supports (Ethereum, Base,
    Arbitrum, Optimism, Polygon, Robinhood Chain…).
  • Magic Eden (Solana): the wallet's tokens with listStatus=listed.
  • Every other Solana marketplace Helius recognises (Tensor, Exchange Art,
    Solanart…): the wallet's own listing / cancel / sale transactions, as
    parsed by Helius. A mint whose latest event is a listing is listed.
  • Exclusivo (exclusivo.one): its public API has no per-wallet lookup, so
    each NFT the scan found (Solana, Ethereum, Base, Polygon, Robinhood) is
    looked up one by one (capped) for an active listing, which can be on
    Exclusivo itself ("EXC") or a marketplace it aggregates.
  • XRP Ledger: sell offers live on the ledger itself (account_objects of
    type nft_offer), which covers every XRP marketplace at once.

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
COIN_FOR_CURRENCY = {"ETH": "ETH", "WETH": "ETH", "POL": "POL", "MATIC": "POL", "WPOL": "POL", "SOL": "SOL", "XRP": "XRP"}
OPENSEA_CHAIN = {"ethereum": Chain.ETHEREUM, "base": Chain.BASE, "arbitrum": Chain.ARBITRUM,
                 "optimism": Chain.OPTIMISM, "polygon": Chain.POLYGON, "matic": Chain.POLYGON,
                 "robinhood": Chain.ROBINHOOD}
EXC_CHAINS = {"solana", "ethereum", "polygon", "base", "robinhood"}
EXC_MAX_NFTS = 200          # per check; most valuable NFTs first
EXC_NAMES = {"EXC": "Exclusivo", "magic-eden": "Magic Eden", "opensea": "OpenSea", "tensor": "Tensor"}
RIPPLE_EPOCH = 946684800   # XRPL times count seconds from 2000-01-01
MARKET_NAMES = {"MAGIC_EDEN": "Magic Eden", "TENSOR": "Tensor", "EXCHANGE_ART": "Exchange Art", "SOLANART": "Solanart",
                "HADESWAP": "Hadeswap", "CORAL_CUBE": "Coral Cube", "HYPERSPACE": "Hyperspace", "SNIPER": "Sniper",
                "OPENSEA": "OpenSea", "SOLSEA": "SolSea", "FORM_FUNCTION": "Formfunction", "YAWWW": "Yawww"}
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


def market_name(source: str | None) -> str:
    s = (source or "").upper()
    return MARKET_NAMES.get(s) or (s.replace("_", " ").title() if s and s != "UNKNOWN" else "Other marketplace")


async def helius_listings(http: Http, key: str, owner: str) -> list[dict]:
    """Solana listings on any marketplace Helius parses, from the wallet's own
    listing / cancel / sale history: the latest event per NFT decides."""
    latest: dict[str, tuple[int, dict]] = {}
    for kind in ("NFT_LISTING", "NFT_CANCEL_LISTING", "NFT_SALE"):
        before = None
        for _ in range(5):   # up to 500 events of each kind
            params = {"api-key": key, "type": kind, "limit": 100}
            if before:
                params["before"] = before
            try:
                page = await http.get(f"/addresses/{owner}/transactions", params=params)
            except Exception as e:
                if "search period" in str(e) or " 404" in str(e):   # Helius: nothing more of this kind
                    break
                raise
            if not page:
                break
            for tx in page:
                ev = ((tx.get("events") or {}).get("nft")) or {}
                ts = int(ev.get("timestamp") or tx.get("timestamp") or 0)
                if kind != "NFT_SALE" and (ev.get("seller") or owner) != owner:
                    continue
                if kind == "NFT_SALE" and ev.get("seller") != owner:
                    continue   # buying something doesn't end your own listing
                for n in ev.get("nfts") or []:
                    mint = n.get("mint")
                    if mint and ts >= latest.get(mint, (-1, {}))[0]:
                        latest[mint] = (ts, {"kind": kind, "ev": ev, "tx": tx})
            before = page[-1].get("signature")
            if len(page) < 100:
                break
    out = []
    for mint, (_ts, e) in latest.items():
        if e["kind"] != "NFT_LISTING":
            continue
        ev = e["ev"]
        lamports = ev.get("amount")
        out.append({
            "marketplace": market_name(ev.get("source") or e["tx"].get("source")), "chain": Chain.SOLANA,
            "owner": owner, "contract": mint, "token_id": mint,
            "price": Decimal(int(lamports)) / Decimal(10 ** 9) if lamports else None, "currency": "SOL",
            "quantity": 1, "expires_at": None, "url": f"https://solscan.io/token/{mint}",
            "must_hold": True,   # a listing whose NFT has since left the wallet is stale
        })
    return out


async def xrpl_listings(http: Http, owner: str) -> list[dict]:
    """Sell offers the account has open on the XRP Ledger (any marketplace)."""
    out, marker = [], None
    for _ in range(10):
        params = {"account": owner, "type": "nft_offer", "limit": 400, "ledger_index": "validated"}
        if marker:
            params["marker"] = marker
        res = await http.post("/", json={"method": "account_objects", "params": [params]})
        r = (res or {}).get("result") or {}
        if r.get("status") == "error":
            if r.get("error") == "actNotFound":
                return out
            raise RuntimeError(f"XRPL account_objects: {r.get('error_message') or r.get('error')}")
        for o in r.get("account_objects") or []:
            if not (int(o.get("Flags", 0)) & 1) or not o.get("NFTokenID"):
                continue   # buy offers aren't listings
            amt = o.get("Amount")
            if isinstance(amt, str):
                price, cur = Decimal(amt) / Decimal(10 ** 6), "XRP"
            elif isinstance(amt, dict):
                price, cur = Decimal(str(amt.get("value") or 0)), (amt.get("currency") or "?")[:12]
            else:
                continue
            nid = o["NFTokenID"]
            exp = o.get("Expiration")
            out.append({
                "marketplace": "XRP Ledger", "chain": Chain.XRP, "owner": owner, "contract": nid, "token_id": nid,
                "price": price, "currency": cur, "quantity": 1,
                "expires_at": _iso(int(exp) + RIPPLE_EPOCH) if exp else None,
                "url": f"https://xrpscan.com/nft/{nid}",
            })
        marker = r.get("marker")
        if not marker:
            break
    return out


async def exclusivo_listings(http: Http, nfts: list[dict]) -> tuple[list[dict], str | None]:
    """Active listings for these held NFTs from Exclusivo's public API."""
    sem = asyncio.Semaphore(5)
    failures = 0

    async def one(h: dict) -> dict | None:
        nonlocal failures
        chain = h["chain"]
        address = (h.get("collection") or "").split(":", 1)[-1]
        tid = h["key"].rsplit(":", 1)[-1]
        if not address or not tid:
            return None
        async with sem:
            try:
                res = await http.get(f"/tokens/{chain}/{address}/{tid}")
            except Exception as e:
                if " 404" not in str(e):
                    failures += 1
                return None
        d = res.get("data") if isinstance(res, dict) and "data" in res else res
        l = (d or {}).get("listing") or None
        if not l or (l.get("status") or "active") != "active":
            return None
        price = l.get("price") or {}
        try:
            amount = Decimal(str(price.get("decimal")))
        except (InvalidOperation, TypeError):
            return None
        owner = (d.get("owner") or l.get("seller") or "")
        slug = l.get("marketplace") or "EXC"
        return {"marketplace": EXC_NAMES.get(slug) or slug.replace("-", " ").title(), "chain": Chain(chain),
                "owner": owner.lower() if owner.startswith("0x") else owner,
                "contract": tid if chain == "solana" else address.lower(), "token_id": tid,
                "price": amount, "currency": (price.get("currency") or "").upper() or "?",
                "usd_hint": price.get("usd"), "quantity": 1, "expires_at": l.get("expiresAt"),
                "url": f"https://exclusivo.one/wallet/{owner}" if owner else "https://exclusivo.one"}

    rows = [r for r in await asyncio.gather(*(one(h) for h in nfts)) if r]
    error = f"{failures} of {len(nfts)} NFT lookups failed" if nfts and failures == len(nfts) else None
    return rows, error


async def fetch_all(addresses: list[tuple[Chain, str]]) -> tuple[list[dict], list[dict]]:
    """All active listings for the portfolio's addresses, plus which sources were checked."""
    owners_evm = sorted({a.lower() for c, a in addresses if c in EVM})
    owners_sol = sorted({a for c, a in addresses if c == Chain.SOLANA})
    owners_xrp = sorted({a for c, a in addresses if c == Chain.XRP})
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
        hkey = env("HELIUS_API_KEY", "")
        if hkey:
            hh = Http(env("HELIUS_API_BASE", "https://api.helius.xyz/v0"), rate_per_sec=8)
            jobs += [("Tensor & other Solana marketplaces", o, helius_listings(hh, hkey, o)) for o in owners_sol]
    if owners_xrp:
        xh = Http(env("XRPL_RPC_URL", "https://xrplcluster.com"), rate_per_sec=4)
        jobs += [("XRP Ledger", o, xrpl_listings(xh, o)) for o in owners_xrp]

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
    seen, unique = set(), []
    for r in rows:
        k = (r["chain"], r["contract"], r["token_id"], r["marketplace"])
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique, sources


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

    # Exclusivo needs the NFTs themselves (no per-wallet lookup): check the
    # held ones once the scan has found them, cached like the rest.
    eligible = sorted((h for h in holdings if h.get("nft") and not h.get("spam") and h.get("chain") in EXC_CHAINS
                       and h.get("collection")), key=lambda h: -(float(h.get("value") or 0)))[:EXC_MAX_NFTS]
    exc_keys = sorted(h["key"] for h in eligible)
    exc = cache.get("exc") or {}
    if eligible and (refresh or exc.get("keys") != exc_keys or time.time() - exc.get("at", 0) > CACHE_SECONDS):
        http = Http(env("EXCLUSIVO_API_URL", "https://exclusivo.one/v1"), rate_per_sec=4)
        try:
            rows_, err = await exclusivo_listings(http, eligible)
        except Exception as e:
            rows_, err = [], monitor.scrub(str(e), 160)
        exc = cache["exc"] = {"keys": exc_keys, "at": time.time(), "rows": rows_, "error": err}
    found = {(r["chain"], r["token_id"].lower(), r["marketplace"]) for r in cache["rows"]}
    extra = [r for r in exc.get("rows") or [] if (r["chain"], r["token_id"].lower(), r["marketplace"]) not in found]
    sources = list(cache["sources"]) + ([{"name": "Exclusivo", "ok": not exc.get("error"), "error": exc.get("error")}]
                                        if exc else [])
    out = []
    for r in cache["rows"] + extra:
        if wallet and r["owner"].lower() != wallet.lower():
            continue
        chain: Chain = r["chain"]
        key = f"{chain.value}:nft:{r['contract']}:{r['token_id']}"
        h = by_key.get(key.lower()) or {}
        if r.get("must_hold") and by_key and not h:
            continue   # the NFT has left the wallet since it was listed
        price_usd = _usd(r["price"], r["currency"], p.native_usd or {})
        if price_usd is None and r.get("usd_hint"):
            price_usd = Decimal(str(r["usd_hint"]))
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
            "sources": sources, "checked_at": cache["checked_at"]}
