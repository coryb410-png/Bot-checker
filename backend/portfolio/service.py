"""Scan orchestration: fetch every address, price, classify, compute P&L.

A "portfolio" is the group of addresses a user submits together. The scan
runs as a background job; the API polls its status. The store here is in
memory so the project runs with zero setup. Swap in database/postgres.py
(schema in migrations/001_init.sql) for production.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from dataclasses import replace

from classifiers.classify import classify_all
from classifiers.spam import is_spam_tx, mark_spam, without_spam
from core.addresses import detect_chain, normalize
from core.models import Chain, NormalizedTx
from portfolio.pnl import Method, PnLReport, compute_pnl
from pricing.coingecko import PriceService

MAX_TXS_PER_ADDRESS = 50_000


@dataclass
class Portfolio:
    id: str
    addresses: list[tuple[Chain, str]]
    method: Method = Method.FIFO
    status: str = "queued"           # queued | scanning | pricing | ready | error
    progress: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    txs: list[NormalizedTx] = field(default_factory=list)
    report: PnLReport | None = None
    cursors: dict[str, str] = field(default_factory=dict)   # address -> newest tx hash
    manual_costs: dict[str, Decimal] = field(default_factory=dict)
    demo: bool = False               # example wallet with invented data
    labels: dict[str, str] = field(default_factory=dict)   # address -> name.eth / name.sol
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None


class Store:
    def __init__(self):
        self.portfolios: dict[str, Portfolio] = {}

    def create(self, addresses: list[str], method: Method, labels: dict[str, str] | None = None) -> Portfolio:
        parsed = []
        for a in addresses:
            chain = detect_chain(a)
            parsed.append((chain, normalize(a, chain)))
        # The same 0x address also exists on Robinhood Chain (an Ethereum L2):
        # scan it there too, automatically.
        for chain, addr in list(parsed):
            if chain == Chain.ETHEREUM and (Chain.ROBINHOOD, addr) not in parsed:
                parsed.append((Chain.ROBINHOOD, addr))
        p = Portfolio(id=secrets.token_urlsafe(9), addresses=parsed, method=method)
        for raw in addresses:
            if labels and labels.get(raw):
                chain = detect_chain(raw)
                p.labels[normalize(raw, chain)] = labels[raw]
        self.portfolios[p.id] = p
        return p

    def add(self, p: Portfolio) -> Portfolio:
        self.portfolios[p.id] = p
        return p

    def get(self, pid: str) -> Portfolio | None:
        return self.portfolios.get(pid)

    def delete(self, pid: str) -> bool:
        return self.portfolios.pop(pid, None) is not None


def progress_key(chain: Chain, address: str) -> str:
    """Scan progress and sync cursors are per network: one 0x address is
    scanned on both Ethereum and Robinhood Chain."""
    return f"{chain.value}:{address}"


def _scanner(chain: Chain):
    # Imported lazily so a missing key for one chain doesn't block the others
    if chain == Chain.BITCOIN:
        from scanners.bitcoin import BitcoinScanner
        return BitcoinScanner()
    if chain == Chain.ETHEREUM:
        from scanners.ethereum import EthereumScanner
        return EthereumScanner()
    if chain == Chain.ROBINHOOD:
        from scanners.robinhood import RobinhoodScanner
        return RobinhoodScanner()
    from scanners.solana import SolanaScanner
    return SolanaScanner()


async def run_scan(p: Portfolio, prices: PriceService) -> None:
    try:
        p.status = "scanning"

        async def one(chain: Chain, addr: str) -> list[NormalizedTx]:
            out = []
            key = progress_key(chain, addr)
            async for tx in _scanner(chain).scan(addr, p.cursors.get(key)):
                out.append(tx)
                p.progress[key] = len(out)
                if len(out) >= MAX_TXS_PER_ADDRESS:
                    break
            return out

        results = await asyncio.gather(*(one(c, a) for c, a in p.addresses))
        new = [t for batch in results for t in batch]
        for (c, a), batch in zip(p.addresses, results):
            if batch:
                p.cursors[progress_key(c, a)] = batch[0].tx_hash

        p.status = "pricing"
        await prices.price_transactions(new)
        p.txs = p.txs + new
        mark_spam(p.txs)
        recompute(p, await prices.current(_held_assets(p)))
        p.status = "ready"
        p.updated_at = datetime.now(timezone.utc)
    except Exception as e:  # surfaced to the UI
        p.status, p.error = "error", str(e)


def recompute(p: Portfolio, current_prices: dict[str, Decimal] | None = None) -> None:
    own = {a.lower() for _, a in p.addresses} | {a for _, a in p.addresses}
    # Spam-only transactions are labelled but kept out of the numbers; spam
    # legs inside a real transaction are dropped before classifying it.
    spam_txs = [t for t in p.txs if is_spam_tx(t)]
    classify_all(spam_txs, own)
    real = [t for t in p.txs if not is_spam_tx(t)]
    clean = [replace(t, legs=without_spam(t)) for t in real]
    classify_all(clean, own)
    for orig, c in zip(real, clean):
        orig.tx_type, orig.label = c.tx_type, c.label
    prev = {k: s.current_price for k, s in (p.report.assets.items() if p.report else [])}
    p.report = compute_pnl(clean, p.method, current_prices or prev, p.manual_costs)


def _held_assets(p: Portfolio):
    seen = {}
    for t in p.txs:
        for l in t.legs:
            if not l.asset.is_nft and not l.asset.spam:
                seen[l.asset.key] = l.asset
    return list(seen.values())
