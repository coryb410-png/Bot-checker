"""ChainScope public API.

Run:  uvicorn api.main:app --reload   (from the backend/ folder)
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from classifiers.spam import counts as spam_counts, is_spam_tx
from core.addresses import InvalidAddress, detect_chain, looks_like_secret
from core.names import NameNotFound, NameResolver, is_name
from scanners.base import ProviderError
from core.models import TxType
import secrets

from portfolio.demo import build_demo
from portfolio.highlights import compute_highlights
from portfolio.history import value_history
from pricing.floors import collection_key
from portfolio.pnl import Method
from portfolio.service import Store, progress_key, recompute, run_scan, wallet_view
from pricing.coingecko import PriceService

app = FastAPI(title="ChainScope API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"],
)
store = Store()
prices = PriceService()
names = NameResolver()
_jobs: set[asyncio.Task] = set()


def _start(coro) -> None:
    task = asyncio.create_task(coro)
    _jobs.add(task)                      # keep a reference until it finishes
    task.add_done_callback(_jobs.discard)


def _realized_by_tx(p) -> dict:
    """One trade can dispose of several assets; sum them per transaction.
    None means part of the sale has no known purchase price."""
    out: dict[str, Decimal | None] = {}
    for d in (p.report.disposals if p.report else []):
        prev = out.get(d.tx_hash, Decimal(0))
        out[d.tx_hash] = None if (prev is None or d.realized is None) else prev + d.realized
    return out

# ---- rate limiting: N scans per IP per hour (move to Redis for >1 server)
SCANS_PER_HOUR = int(os.environ.get("SCANS_PER_HOUR", "10"))
_hits: dict[str, deque] = defaultdict(deque)


def _limit(req: Request, bucket: str = "scan", per_hour: int | None = None) -> None:
    ip = (req.headers.get("x-chainscope-client")
          or req.headers.get("x-forwarded-for", req.client.host if req.client else "?")).split(",")[0].strip()
    q, now = _hits[f"{bucket}:{ip}"], time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= (per_hour or SCANS_PER_HOUR):
        raise HTTPException(429, "Too many requests from this network. Try again within the hour.")
    q.append(now)


def s(d: Decimal | None) -> str | None:
    return None if d is None else format(d.quantize(Decimal("0.00000001")).normalize(), "f")


def usd(d: Decimal | None) -> str | None:
    return None if d is None else format(d.quantize(Decimal("0.01")), "f")


def _portfolio(pid: str):
    p = store.get(pid)
    if not p:
        raise HTTPException(404, "Portfolio not found. It may have been removed.")
    return p


# ---------------------------------------------------------------- requests
class ScanRequest(BaseModel):
    addresses: list[str] = Field(min_length=1, max_length=20)   # addresses or name.eth / name.sol
    labels: dict[str, str] = Field(default_factory=dict)       # address -> display name
    method: Method = Method.FIFO


class CostRequest(BaseModel):
    tx_hash: str
    total_cost_usd: Decimal = Field(ge=0)


# ---------------------------------------------------------------- endpoints
async def _resolve(name: str) -> tuple[str, str]:
    try:
        chain, addr = await names.resolve(name)
    except NameNotFound as e:
        raise HTTPException(404, str(e))
    except ProviderError:
        raise HTTPException(502, "Name lookup is unavailable right now. Paste the address instead.")
    return chain.value, addr


@app.get("/api/resolve")
async def resolve(name: str, req: Request):
    """name.eth or name.sol -> {chain, address}"""
    _limit(req, "resolve", 120)
    chain, addr = await _resolve(name)
    return {"name": name.strip().lower(), "chain": chain, "address": addr}


@app.post("/api/wallets/scan", status_code=202)
async def scan(body: ScanRequest, req: Request):
    resolved, labels = [], dict(body.labels)
    for a in body.addresses:
        if is_name(a):
            _, addr = await _resolve(a)
            labels.setdefault(addr, a.strip().lower())
            a = addr
        resolved.append(a)
    body.addresses = resolved
    for a in body.addresses:
        if looks_like_secret(a):
            raise HTTPException(400, "That looks like a seed phrase or private key. Never share it. "
                                     "Enter a public address instead.")
        try:
            detect_chain(a)
        except InvalidAddress as e:
            raise HTTPException(422, f"{a[:12]}…: {e}")
    _limit(req)
    p = store.create(body.addresses, body.method, labels)
    _start(run_scan(p, prices))
    return {"id": p.id, "status": p.status}


@app.post("/api/demo", status_code=201)
async def demo(req: Request, method: Method = Method.FIFO):
    """Create a private copy of the example wallet (no provider calls)."""
    _limit(req, "demo", 60)
    p = store.add(build_demo("demo-" + secrets.token_urlsafe(9), method))
    return {"id": p.id, "status": p.status}


@app.post("/api/wallets/{pid}/refresh", status_code=202)
async def refresh(pid: str, req: Request):
    p = _portfolio(pid)
    if p.demo:
        raise HTTPException(400, "The example wallet uses invented data and can't be rescanned.")
    _limit(req)
    _start(run_scan(p, prices))   # incremental: stops at known cursors
    return {"id": p.id, "status": "queued"}


def _scoped(pid: str, wallet: str | None):
    """The whole portfolio, or one of its addresses when `wallet` is given."""
    p = _portfolio(pid)
    if not wallet:
        return p
    try:
        return wallet_view(p, wallet)
    except KeyError:
        raise HTTPException(404, "That address isn't part of this portfolio.")


@app.get("/api/wallets/{pid}")
async def wallet(pid: str, wallet: str | None = None):
    whole = _portfolio(pid)
    p = _scoped(pid, wallet) if whole.report else whole
    r = p.report
    return {
        "id": p.id, "status": p.status, "error": p.error, "method": p.method, "demo": p.demo,
        "addresses": [{"chain": c.value, "address": a, "label": p.labels.get(a),
                       "scanned": p.progress.get(progress_key(c, a), 0)} for c, a in whole.addresses],
        "wallet": wallet,
        "updated_at": p.updated_at,
        "network_errors": [{"chain": k.split(":", 1)[0], "address": k.split(":", 1)[1], "error": v}
                           for k, v in p.network_errors.items()],
        "summary": None if not r else {
            "portfolio_value": usd(r.portfolio_value + _untracked_value(p)),
            "nft_value": usd(_nft_value(p)),
            "nfts_valued": sum(1 for a in _nft_holdings(p) if a["value"] is not None),
            "nfts_held": len(_nft_holdings(p)),
            "realized_pnl": usd(r.realized_total),
            "pnl_complete": r.complete,
            "transactions": len({t.tx_hash for t in p.txs if not is_spam_tx(t)}),
            "assets_sold": len({d.asset_key for d in r.disposals}),
            "fees_paid": usd(r.standalone_fees + sum((a.fees for a in r.assets.values()), Decimal(0))),
            "warnings": r.warnings[:50],
            "spam_hidden": spam_counts(p.txs),
            "highlights": _highlights_json(p),
            "highlights_by_range": {str(d or "all"): _highlights_json(p, d) for d in HIGHLIGHT_RANGES},
        },
    }


@app.delete("/api/wallets/{pid}", status_code=204)
async def remove(pid: str):
    if not store.delete(pid):
        raise HTTPException(404, "Portfolio not found.")


@app.get("/api/wallets/{pid}/transactions")
async def transactions(pid: str, type: str | None = None, chain: str | None = None,
                       nft: bool = False, include_spam: bool = False, wallet: str | None = None,
                       offset: int = 0, limit: int = Query(100, le=500)):
    p = _scoped(pid, wallet)
    try:
        wanted = {TxType(x.strip()) for x in type.split(",") if x.strip()} if type else set()
    except ValueError:
        raise HTTPException(422, "Unknown transaction type filter.")
    rows, seen = [], set()
    for t in sorted(p.txs, key=lambda t: t.timestamp, reverse=True):
        if t.tx_type == TxType.INTERNAL_TRANSFER:
            if t.tx_hash in seen:
                continue
            seen.add(t.tx_hash)
        if (wanted and t.tx_type not in wanted) or (chain and t.chain.value != chain):
            continue
        if nft and not any(l.asset.is_nft for l in t.legs):
            continue
        if not include_spam and is_spam_tx(t):
            continue
        rows.append(t)
    page = rows[offset:offset + limit]
    realized = _realized_by_tx(p)
    return {"total": len(rows), "items": [_tx_json(t, realized) for t in page]}


@app.get("/api/transactions/{tx_hash}")
async def transaction(tx_hash: str, portfolio: str):
    p = _portfolio(portfolio)
    t = next((t for t in p.txs if t.tx_hash == tx_hash), None)
    if not t:
        raise HTTPException(404, "Transaction not in this portfolio.")
    return _tx_json(t, _realized_by_tx(p))


@app.get("/api/wallets/{pid}/holdings")
async def holdings(pid: str, wallet: str | None = None):
    return _holdings_json(_scoped(pid, wallet))


def _holdings_json(p) -> list[dict]:
    items = [a for a in (p.report.assets.values() if p.report else []) if a.quantity > 0]
    items.sort(key=lambda a: a.market_value or Decimal(0), reverse=True)
    nfts = {l.asset.key: l.asset for t in p.txs for l in t.legs if l.asset.is_nft}
    out = []
    for a in items:
        n = nfts.get(a.asset_key)
        out.append({"asset": a.symbol, "chain": a.chain, "key": a.asset_key, "quantity": s(a.quantity),
                    "nft": n is not None, "name": getattr(n, "name", None), "image": getattr(n, "image", None),
                    "spam": getattr(n, "spam", False), "compressed": getattr(n, "compressed", False),
                    "collection": collection_key(n) if n else None, "floor": _floor_json(p, n) if n else None,
                    "price": usd(a.current_price), "value": usd(a.market_value),
                    "cost_basis": usd(a.cost_basis), "unrealized_pnl": usd(a.unrealized),
                    "unknown_basis_qty": s(a.unknown_qty), "untracked": False})
    # Owned NFTs the history never showed arriving (e.g. Metaplex Core)
    for n in getattr(p, "untracked_nfts", []):
        f = _floor_json(p, n)
        out.append({"asset": n.symbol, "chain": n.chain.value, "key": n.key, "quantity": "1", "nft": True,
                    "name": n.name, "image": n.image, "spam": False, "compressed": n.compressed,
                    "collection": collection_key(n), "floor": f,
                    "price": f["usd"] if f else None, "value": f["usd"] if f else None,
                    "cost_basis": None, "unrealized_pnl": None, "unknown_basis_qty": "1", "untracked": True})
    return out


@app.get("/api/wallets/{pid}/pnl")
async def pnl(pid: str, method: Method | None = None):
    p = _portfolio(pid)
    if method and method != p.method:
        p.method = method
        recompute(p)
    r = p.report
    if not r:
        raise HTTPException(409, "Scan still running.")
    return {
        "method": r.method, "complete": r.complete,
        "realized_total": usd(r.realized_total),
        "assets": [{"asset": a.symbol, "chain": a.chain, "proceeds": usd(a.proceeds),
                    "realized_pnl": usd(a.realized), "unrealized_pnl": usd(a.unrealized),
                    "fees": usd(a.fees), "incomplete": a.incomplete} for a in r.assets.values()],
        "disposals": [{"tx_hash": d.tx_hash, "date": d.timestamp, "asset": d.symbol, "qty": s(d.qty),
                       "proceeds": usd(d.proceeds), "cost_basis": usd(d.cost_basis),
                       "fees": usd(d.fees), "realized": usd(d.realized),
                       "unknown_qty": s(d.unknown_qty)} for d in r.disposals],
        "warnings": r.warnings,
    }


@app.put("/api/wallets/{pid}/cost-basis")
async def set_cost(pid: str, body: CostRequest):
    """Let users fill in what they paid off-chain for an incoming transfer."""
    p = _portfolio(pid)
    p.manual_costs[body.tx_hash] = body.total_cost_usd
    recompute(p)
    return {"ok": True}


@app.get("/api/wallets/{pid}/performance")
async def performance(pid: str, wallet: str | None = None):
    """Daily value of coins and tokens, plus running realized P&L."""
    p = _scoped(pid, wallet)
    if not p.report:
        raise HTTPException(409, "Scan still running.")
    if p.value_history is None:
        try:
            p.value_history = await value_history(p, prices)
        except Exception as e:   # the chart is optional; never break the page
            import logging
            logging.getLogger("chainscope").warning("value history failed: %s", str(e)[:200])
            p.value_history = []
    total, series = Decimal(0), []
    for d in sorted(p.report.disposals, key=lambda d: d.timestamp):
        if d.realized is not None:
            total += d.realized
        series.append({"date": d.timestamp, "cumulative_realized": usd(total)})
    return {"value_over_time": p.value_history,
            "value_basis": "Coins and tokens at each day's price. NFTs are valued at today's floor in the total only.",
            "realized_over_time": series}


@app.get("/api/health")
async def health():
    return {"ok": True}


def _floor_json(p, a) -> dict | None:
    f = getattr(p, "floors", {}).get(collection_key(a))
    if not f:
        return None
    rate = getattr(p, "native_usd", {}).get(f.currency)
    return {"amount": s(f.amount), "currency": f.currency, "source": f.source, "url": f.url,
            "usd": usd(f.amount * rate) if rate else None}


def _untracked_value(p) -> Decimal:
    total = Decimal(0)
    for n in getattr(p, "untracked_nfts", []):
        f = _floor_json(p, n)
        if f and f["usd"]:
            total += Decimal(f["usd"])
    return total


def _nft_holdings(p) -> list[dict]:
    return [h for h in _holdings_json(p) if h["nft"]]


def _nft_value(p) -> Decimal:
    return sum((Decimal(h["value"]) for h in _nft_holdings(p) if h["value"]), Decimal(0))


HIGHLIGHT_RANGES = (30, 90, 366, None)   # the chart's 30D / 90D / 1Y / All buttons


def _highlights_json(p, days: int | None = None) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    h = compute_highlights(p, since)
    money_keys = {"realized", "proceeds", "cost"}

    def fmt(item):
        if not item:
            return None
        return {k: (usd(v) if k in money_keys else v) for k, v in item.items()}

    return {k: (fmt(v) if isinstance(v, dict) or v is None else v) for k, v in h.items()}


def _tx_json(t, realized: dict):
    return {
        "hash": t.tx_hash, "chain": t.chain.value, "wallet": t.wallet, "date": t.timestamp,
        "type": t.tx_type, "label": t.label, "spam": is_spam_tx(t), "programs": t.programs[:6], "source": t.source, "success": t.success,
        "legs": [{"asset": l.asset.symbol, "name": l.asset.name, "nft": l.asset.is_nft,
                  "token_id": l.asset.token_id, "image": l.asset.image, "spam": l.asset.spam,
                  "compressed": l.asset.compressed,
                  "amount": s(l.amount), "usd": usd(l.usd_value), "counterparty": l.counterparty} for l in t.legs],
        "fee": s(t.fee), "fee_usd": usd(t.fee_usd),
        "usd_in": usd(sum((l.usd_value or 0 for l in t.received), Decimal(0))),
        "usd_out": usd(sum((l.usd_value or 0 for l in t.sent), Decimal(0))),
        "has_realized": t.tx_hash in realized,
        "realized_pnl": usd(realized.get(t.tx_hash)),
        "explorer": {
            "bitcoin": f"https://mempool.space/tx/{t.tx_hash}",
            "ethereum": f"https://etherscan.io/tx/{t.tx_hash}",
            "solana": f"https://solscan.io/tx/{t.tx_hash}",
            "robinhood": f"https://robinhoodchain.blockscout.com/tx/{t.tx_hash}",
        }[t.chain.value],
    }
