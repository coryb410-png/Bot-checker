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
from core.models import Asset, Chain, NormalizedTx
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
    network_errors: dict[str, str] = field(default_factory=dict)  # "chain:address" -> message
    # NFTs the wallet owns that its history never showed arriving (e.g. Metaplex
    # Core NFTs, which don't move as tokens). Shown in holdings, no cost basis.
    untracked_nfts: list = field(default_factory=list)
    floors: dict = field(default_factory=dict)        # collection key -> Floor
    native_usd: dict = field(default_factory=dict)    # "SOL"/"ETH"/"ADA"/"XRP" -> current USD price
    value_history: list | None = None                 # cached chart points
    nft_owner: dict = field(default_factory=dict)     # untracked NFT key -> owning address
    views: dict = field(default_factory=dict)         # address -> (report it was built from, view)
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
    if chain == Chain.CARDANO:
        from scanners.cardano import CardanoScanner
        return CardanoScanner()
    if chain == Chain.XRP:
        from scanners.xrp import XrpScanner
        return XrpScanner()
    if chain == Chain.DOGECOIN:
        from scanners.dogecoin import DogecoinScanner
        return DogecoinScanner()
    from scanners.solana import SolanaScanner
    return SolanaScanner()


async def run_scan(p: Portfolio, prices: PriceService) -> None:
    try:
        p.status = "scanning"

        p.network_errors = {}

        async def one(chain: Chain, addr: str) -> list[NormalizedTx]:
            out = []
            key = progress_key(chain, addr)
            try:
                async for tx in _scanner(chain).scan(addr, p.cursors.get(key)):
                    out.append(tx)
                    p.progress[key] = len(out)
                    if len(out) >= MAX_TXS_PER_ADDRESS:
                        break
            except Exception as e:
                # One network failing (an outage, a missing key) must not sink
                # the others. Keep what was read and report the problem.
                p.network_errors[key] = str(e)[:300]
            return out

        results = await asyncio.gather(*(one(c, a) for c, a in p.addresses))
        if len(p.network_errors) == len(p.addresses):
            raise RuntimeError(next(iter(p.network_errors.values())))
        new = [t for batch in results for t in batch]
        for (c, a), batch in zip(p.addresses, results):
            if batch:
                p.cursors[progress_key(c, a)] = batch[0].tx_hash

        # Show what was found right away; dollar values fill in afterwards
        p.txs = p.txs + new
        recompute(p)
        p.status = "pricing"

        # Prices (CoinGecko) and owned-NFT lookups (Helius/Alchemy) use
        # different services, so run them side by side
        await asyncio.gather(prices.price_transactions(new), _find_untracked_nfts(p))
        mark_spam(p.txs)
        current = await prices.current(_held_assets(p) + list(NATIVES))
        recompute(p, current)

        p.status = "valuing"
        nft_prices = await _value_nfts(p, current)
        if nft_prices:
            recompute(p, {**current, **nft_prices})
        p.value_history = None
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


async def _find_untracked_nfts(p: Portfolio) -> None:
    held_keys = set()
    for t in p.txs:
        for l in t.legs:
            if l.asset.is_nft:
                held_keys.add(l.asset.key)
    extra, owned_by_key = [], {}

    async def owned_by(chain: Chain, addr: str) -> list[Asset]:
        sc = _scanner(chain)
        if not hasattr(sc, "held_nfts"):
            return []
        try:
            return await sc.held_nfts(addr)
        except Exception as e:
            p.network_errors[progress_key(chain, addr) + ":holdings"] = str(e)[:300]
            return []

    wallets = [(c, a) for c, a in p.addresses if c in (Chain.SOLANA, Chain.ETHEREUM, Chain.CARDANO, Chain.XRP)]
    for (_, addr), owned in zip(wallets, await asyncio.gather(*(owned_by(c, a) for c, a in wallets))):
        owned_by_key.update({a.key: a for a in owned})
        p.nft_owner.update({a.key: addr for a in owned})
        extra += [a for a in owned if a.key not in held_keys and not a.spam]
    # Fill in photos/names the history lookup couldn't get for NFTs still owned
    for t in p.txs:
        for l in t.legs:
            o = owned_by_key.get(l.asset.key) if l.asset.is_nft else None
            if o and o.image and not l.asset.image:
                # A token with no photo had no metadata either, so its name was a guess
                l.asset = replace(l.asset, image=o.image, name=o.name or l.asset.name)
            elif o and o.name and not l.asset.name:
                l.asset = replace(l.asset, name=o.name)
    # Owned NFTs whose history we did read are already counted; keep only the rest
    p.untracked_nfts = list({a.key: a for a in extra}.values())


NATIVE_SOL = Asset(Chain.SOLANA, "SOL", decimals=9, coingecko_id="solana")
NATIVE_ETH = Asset(Chain.ETHEREUM, "ETH", coingecko_id="ethereum")
NATIVE_ADA = Asset(Chain.CARDANO, "ADA", decimals=6, coingecko_id="cardano")
NATIVE_XRP = Asset(Chain.XRP, "XRP", decimals=6, coingecko_id="ripple")
NATIVES = (NATIVE_SOL, NATIVE_ETH, NATIVE_ADA, NATIVE_XRP)
_floor_service = None


async def _value_nfts(p: Portfolio, current: dict) -> dict:
    """Value every NFT held at its collection's floor price (in USD)."""
    from pricing.floors import FloorService, collection_key
    global _floor_service
    _floor_service = _floor_service or FloorService()

    held = [a for k, a in _nft_assets(p).items()
            if p.report and k in p.report.assets and p.report.assets[k].quantity > 0]
    held += list(p.untracked_nfts)
    if not held:
        return {}
    try:
        p.floors = await _floor_service.floors_for(held)
    except Exception:
        p.floors = {}
    p.native_usd = {a.symbol: current.get(a.key) for a in NATIVES}
    out = {}
    for a in held:
        f = p.floors.get(collection_key(a))
        usd = p.native_usd.get(f.currency) if f else None
        if f and usd:
            out[a.key] = f.amount * usd
    return out


def _nft_assets(p: Portfolio) -> dict:
    return {l.asset.key: l.asset for t in p.txs for l in t.legs if l.asset.is_nft and not l.asset.spam}


def _held_assets(p: Portfolio):
    seen = {}
    for t in p.txs:
        for l in t.legs:
            if not l.asset.is_nft and not l.asset.spam:
                seen[l.asset.key] = l.asset
    return list(seen.values())


def same_address(a: str, b: str) -> bool:
    return a == b or (a.startswith("0x") and a.lower() == b.lower())


def wallet_view(p: Portfolio, wallet: str) -> Portfolio:
    """The portfolio as if only `wallet` had been scanned: its own
    transactions, holdings, NFTs and profit/loss. Moves to or from your other
    wallets count as transfers out/in here (the combined view nets them out).
    Built on demand and reused until the portfolio's numbers change."""
    import copy
    addresses = [(c, a) for c, a in p.addresses if same_address(a, wallet)]
    if not addresses:
        raise KeyError(wallet)
    key = addresses[0][1].lower() if wallet.startswith("0x") else addresses[0][1]
    hit = p.views.get(key)
    if hit and hit[0] is p.report:
        return hit[1]

    v = copy.copy(p)
    v.addresses = addresses
    v.txs = [t for t in p.txs if same_address(t.wallet, wallet)]
    v.untracked_nfts = [a for a in p.untracked_nfts if same_address(p.nft_owner.get(a.key, ""), wallet)]
    v.value_history, v.views = None, {}
    if p.report:
        own = {a for _, a in addresses} | {a.lower() for _, a in addresses}
        clean = [replace(t, legs=without_spam(t)) for t in v.txs if not is_spam_tx(t)]
        classify_all(clean, own)   # copies only: the combined view keeps its labels
        prices = {k: a.current_price for k, a in p.report.assets.items() if a.current_price is not None}
        v.report = compute_pnl(clean, p.method, prices, p.manual_costs)
    p.views[key] = (p.report, v)
    return v
