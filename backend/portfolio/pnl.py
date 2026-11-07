"""Cost-basis and profit/loss engine.

Works across every wallet in a portfolio at once, in chronological order, so
moving coins between your own wallets never looks like a sale and the
original purchase price follows the coins.

Principle: never invent numbers. Coins that arrived with no visible purchase
(exchange withdrawals, gifts, airdrops) get an UNKNOWN cost basis. Any sale
that consumes unknown-basis coins reports that portion's P&L as unknown and
the asset is flagged as incomplete, until the user supplies a cost.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from classifiers.classify import STABLECOINS
from core.models import Asset, NormalizedTx, TxType

ZERO = Decimal(0)
# A trade disposes of everything sent and acquires everything received.
# Buying ETH with USDC is a USDC disposal (near-zero P&L) plus an ETH lot.
TRADES = {TxType.BUY, TxType.SELL, TxType.SWAP, TxType.NFT_PURCHASE, TxType.NFT_SALE}


class Method(str, Enum):
    FIFO = "fifo"
    AVERAGE = "average"


@dataclass
class Lot:
    qty: Decimal
    unit_cost: Decimal | None      # None = unknown basis
    acquired_at: datetime
    tx_hash: str


@dataclass
class Disposal:
    tx_hash: str
    timestamp: datetime
    asset_key: str
    symbol: str
    qty: Decimal
    proceeds: Decimal
    cost_basis: Decimal            # of the known-basis portion only
    fees: Decimal
    unknown_qty: Decimal           # qty sold that had no known cost
    realized: Decimal | None       # None when any portion is unknown
    acquired_at: datetime | None = None   # when the oldest unit sold was acquired
    acquired_tx: str | None = None        # the transaction that acquired it

    @property
    def complete(self) -> bool:
        return self.unknown_qty == 0


@dataclass
class AssetSummary:
    asset_key: str
    symbol: str
    chain: str
    quantity: Decimal = ZERO
    unknown_qty: Decimal = ZERO
    cost_basis: Decimal = ZERO      # remaining, known portion
    proceeds: Decimal = ZERO
    realized: Decimal = ZERO        # from complete disposals + known parts
    fees: Decimal = ZERO
    current_price: Decimal | None = None
    incomplete: bool = False

    @property
    def market_value(self) -> Decimal | None:
        return None if self.current_price is None else self.quantity * self.current_price

    @property
    def unrealized(self) -> Decimal | None:
        if self.current_price is None:
            return None
        known_qty = self.quantity - self.unknown_qty
        return known_qty * self.current_price - self.cost_basis


@dataclass
class PnLReport:
    method: Method
    assets: dict[str, AssetSummary] = field(default_factory=dict)
    disposals: list[Disposal] = field(default_factory=list)
    standalone_fees: Decimal = ZERO
    warnings: list[str] = field(default_factory=list)

    @property
    def realized_total(self) -> Decimal:
        return sum((a.realized for a in self.assets.values()), ZERO)

    @property
    def portfolio_value(self) -> Decimal:
        return sum((a.market_value or ZERO for a in self.assets.values()), ZERO)

    @property
    def complete(self) -> bool:
        return not any(a.incomplete for a in self.assets.values())


class LotPool:
    def __init__(self, method: Method):
        self.method = method
        self.lots: deque[Lot] = deque()

    @property
    def qty(self) -> Decimal:
        return sum((l.qty for l in self.lots), ZERO)

    @property
    def unknown_qty(self) -> Decimal:
        return sum((l.qty for l in self.lots if l.unit_cost is None), ZERO)

    @property
    def known_cost(self) -> Decimal:
        return sum((l.qty * l.unit_cost for l in self.lots if l.unit_cost is not None), ZERO)

    def add(self, lot: Lot) -> None:
        if lot.qty > 0:
            self.lots.append(lot)

    def take(self, qty: Decimal) -> tuple[list[Lot], Decimal]:
        """Remove qty. Returns (lots taken, shortfall qty never seen acquired)."""
        if self.method == Method.AVERAGE:
            return self._take_average(qty)
        taken: list[Lot] = []
        remaining = qty
        while remaining > 0 and self.lots:
            lot = self.lots[0]
            use = min(lot.qty, remaining)
            taken.append(Lot(use, lot.unit_cost, lot.acquired_at, lot.tx_hash))
            lot.qty -= use
            remaining -= use
            if lot.qty == 0:
                self.lots.popleft()
        return taken, max(remaining, ZERO)

    def _take_average(self, qty: Decimal) -> tuple[list[Lot], Decimal]:
        total = self.qty
        if total == 0:
            return [], qty
        use = min(qty, total)
        unknown = self.unknown_qty
        known_qty = total - unknown
        avg = self.known_cost / known_qty if known_qty else ZERO
        frac = use / total
        taken = []
        src = self.lots[0].tx_hash if len(self.lots) == 1 else "avg"   # keep the source for single lots (NFTs)
        if known_qty:
            taken.append(Lot(known_qty * frac, avg, self.lots[0].acquired_at, src))
        if unknown:
            taken.append(Lot(unknown * frac, None, self.lots[0].acquired_at, src))
        # Re-pool what is left as at most two lots
        first_at = self.lots[0].acquired_at
        self.lots.clear()
        if known_qty * (1 - frac) > 0:
            self.lots.append(Lot(known_qty * (1 - frac), avg, first_at, "avg"))
        if unknown * (1 - frac) > 0:
            self.lots.append(Lot(unknown * (1 - frac), None, first_at, "avg"))
        return taken, qty - use


def _usd(leg_value: Decimal | None) -> Decimal | None:
    return abs(leg_value) if leg_value is not None else None


def compute_pnl(
    txs: list[NormalizedTx],
    method: Method = Method.FIFO,
    current_prices: dict[str, Decimal] | None = None,
    manual_costs: dict[str, Decimal] | None = None,
) -> PnLReport:
    """manual_costs maps tx_hash -> total USD cost the user entered for a
    transfer-in, so off-chain purchases can complete the picture."""
    report = PnLReport(method=method)
    pools: dict[str, LotPool] = defaultdict(lambda: LotPool(method))
    transit: dict[str, list[Lot]] = defaultdict(list)   # bridged, keyed by symbol
    meta: dict[str, Asset] = {}
    manual_costs = manual_costs or {}
    seen_internal: set[str] = set()

    for tx in sorted(txs, key=lambda t: (t.timestamp, t.tx_hash)):
        t = tx.tx_type
        fee_usd = tx.fee_usd or ZERO

        # The same internal transfer shows up once per wallet; count it once.
        if t == TxType.INTERNAL_TRANSFER:
            if tx.tx_hash in seen_internal:
                continue
            seen_internal.add(tx.tx_hash)

        # Gas is paid in the native coin: remove that quantity from holdings.
        if tx.fee and tx.fee_asset:
            meta.setdefault(tx.fee_asset.key, tx.fee_asset)
            pools[tx.fee_asset.key].take(tx.fee)

        for leg in tx.legs:
            meta.setdefault(leg.asset.key, leg.asset)

        sent = [l for l in tx.sent if l.amount != 0]
        recv = [l for l in tx.received if l.amount != 0]

        # ---- Disposals -------------------------------------------------
        if t in TRADES:
            recv_usd = sum((_usd(l.usd_value) or ZERO for l in recv), ZERO)
            sent_usd = [(_usd(l.usd_value) or ZERO) for l in sent]
            sent_total = sum(sent_usd, ZERO)
            for leg, s_usd in zip(sent, sent_usd):
                share = (s_usd / sent_total) if sent_total else Decimal(1) / len(sent)
                # Proceeds = fair value of what came back (fallback: value sent)
                proceeds = (recv_usd or sent_total) * share
                fee_share = fee_usd * share
                _dispose(report, pools, tx, leg, proceeds, fee_share)

        # ---- Acquisitions ----------------------------------------------
        # Trading fees are charged against the disposal side above, so the
        # new lot's basis is the fair value received.
        if t in TRADES:
            for leg in recv:
                v = _usd(leg.usd_value)
                unit = None if v is None else v / leg.amount
                pools[leg.asset.key].add(Lot(leg.amount, unit, tx.timestamp, tx.tx_hash))

        elif t == TxType.TRANSFER_IN:
            for leg in recv:
                cost = manual_costs.get(tx.tx_hash)
                if cost is None and _stable(leg.asset) and leg.usd_value is not None:
                    cost = abs(leg.usd_value)   # a dollar is a dollar
                unit = (cost / leg.amount) if cost is not None else None
                pools[leg.asset.key].add(Lot(leg.amount, unit, tx.timestamp, tx.tx_hash))

        elif t == TxType.TRANSFER_OUT:
            # Leaving the portfolio (gift, payment, exchange deposit): remove
            # at cost. We do not guess whether it was a taxable sale.
            for leg in sent:
                pools[leg.asset.key].take(-leg.amount)
            report.standalone_fees += fee_usd

        elif t == TxType.BRIDGE:
            for leg in sent:
                lots, _ = pools[leg.asset.key].take(-leg.amount)
                transit[leg.asset.symbol.upper()].extend(lots)
            for leg in recv:
                lots = transit.pop(leg.asset.symbol.upper(), [])
                carried = sum((l.qty for l in lots), ZERO)
                for l in lots:
                    pools[leg.asset.key].add(l)
                if leg.amount > carried:  # arrived from outside the portfolio
                    pools[leg.asset.key].add(Lot(leg.amount - carried, None, tx.timestamp, tx.tx_hash))
            report.standalone_fees += fee_usd

        elif t in (TxType.NETWORK_FEE, TxType.CONTRACT_INTERACTION, TxType.INTERNAL_TRANSFER):
            report.standalone_fees += fee_usd

    # ---- Final holdings ------------------------------------------------
    prices = current_prices or {}
    for key, pool in pools.items():
        s = report.assets.setdefault(key, _summary(meta[key]))
        s.quantity = pool.qty
        s.unknown_qty = pool.unknown_qty
        s.cost_basis = pool.known_cost
        s.current_price = prices.get(key)
        if s.unknown_qty > 0:
            s.incomplete = True

    for key, s in report.assets.items():
        if s.incomplete and s.unknown_qty > 0:
            report.warnings.append(
                f"{s.symbol}: {s.unknown_qty.normalize()} units have no known purchase price. "
                "Add the cost to complete profit/loss."
            )
    return report


def _stable(a: Asset) -> bool:
    return a.symbol.upper() in STABLECOINS


def _summary(a: Asset) -> AssetSummary:
    return AssetSummary(asset_key=a.key, symbol=a.symbol, chain=a.chain.value)


def _dispose(report, pools, tx, leg, proceeds, fee_share) -> None:
    qty = -leg.amount
    lots, shortfall = pools[leg.asset.key].take(qty)
    known = [l for l in lots if l.unit_cost is not None]
    unknown_qty = sum((l.qty for l in lots if l.unit_cost is None), ZERO) + shortfall
    known_qty = qty - unknown_qty
    cost = sum((l.qty * l.unit_cost for l in known), ZERO)
    if unknown_qty and _stable(leg.asset):
        # Stablecoins with no visible purchase: basis = value at disposal
        cost += proceeds * (unknown_qty / qty)
        known_qty, unknown_qty = qty, ZERO
    # Only the known-cost share of proceeds can produce a real P&L figure
    known_proceeds = proceeds * (known_qty / qty) if qty else ZERO
    known_fee = fee_share * (known_qty / qty) if qty else ZERO
    realized_known = known_proceeds - cost - known_fee

    d = Disposal(
        tx_hash=tx.tx_hash, timestamp=tx.timestamp, asset_key=leg.asset.key,
        symbol=leg.asset.symbol, qty=qty, proceeds=proceeds, cost_basis=cost,
        fees=fee_share, unknown_qty=unknown_qty,
        acquired_at=lots[0].acquired_at if lots else None,
        acquired_tx=lots[0].tx_hash if lots and lots[0].tx_hash != "avg" else None,
        realized=realized_known if unknown_qty == 0 else None,
    )
    report.disposals.append(d)
    s = report.assets.setdefault(leg.asset.key, _summary(leg.asset))
    s.proceeds += proceeds
    s.fees += fee_share
    s.realized += realized_known
    if unknown_qty > 0:
        s.incomplete = True
        report.warnings.append(
            f"{leg.asset.symbol} sale in {tx.tx_hash[:10]}…: {unknown_qty.normalize()} units sold "
            "with no known purchase price. Profit shown covers the known portion only."
        )
