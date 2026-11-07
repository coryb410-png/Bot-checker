"""Best and worst trade, best and worst NFT flip.

Trades are sells and swaps of coins or tokens (not NFTs). A trade's result is
the realized P&L of everything it disposed of. Buying with stablecoins isn't a
trade here: spending dollars has no real gain or loss.

A flip is an NFT that was bought and later sold from this portfolio. Its
result is the sale proceeds minus what was paid, minus fees.

Only complete figures count: anything touching coins with an unknown
purchase price is left out rather than guessed.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from classifiers.spam import is_spam_tx
from core.models import TxType

TRADE_TYPES = {TxType.SELL, TxType.SWAP}


def compute_highlights(p, since: datetime | None = None) -> dict:
    """Highlights for sales on or after `since` (all time when None)."""
    r = p.report
    if not r:
        return {}
    txs = {t.tx_hash: t for t in p.txs if not is_spam_tx(t)}
    assets = {l.asset.key: l.asset for t in p.txs for l in t.legs}

    # ---- trades: sum disposals per transaction
    per_tx: dict[str, list] = defaultdict(list)
    for d in r.disposals:
        t = txs.get(d.tx_hash)
        a = assets.get(d.asset_key)
        if t and t.tx_type in TRADE_TYPES and a and not a.is_nft:
            per_tx[d.tx_hash].append(d)
    trades = []
    for h, ds in per_tx.items():
        if any(d.realized is None for d in ds):
            continue
        t = txs[h]
        if since and t.timestamp < since:
            continue
        trades.append({
            "tx_hash": h, "date": t.timestamp, "label": t.label, "chain": t.chain.value,
            "source": t.source, "realized": sum((d.realized for d in ds), Decimal(0)),
            "proceeds": sum((d.proceeds for d in ds), Decimal(0)),
        })

    # ---- NFT flips: NFT disposals whose purchase is known
    flips = []
    for d in r.disposals:
        a = assets.get(d.asset_key)
        if not a or not a.is_nft or d.realized is None or d.cost_basis <= 0:
            continue
        if since and d.timestamp < since:
            continue
        t = txs.get(d.tx_hash)
        held = (d.timestamp - d.acquired_at).days if d.acquired_at else None
        flips.append({
            "tx_hash": d.tx_hash, "buy_tx": d.acquired_tx, "date": d.timestamp, "bought_at": d.acquired_at,
            "held_days": held, "name": a.name or f"{a.symbol} #{a.token_id}", "image": a.image, "spam": a.spam,
            "chain": a.chain.value, "source": t.source if t else None,
            "cost": d.cost_basis, "proceeds": d.proceeds, "realized": d.realized,
        })

    def pick(items):
        if not items:
            return None, None
        ordered = sorted(items, key=lambda x: x["realized"])
        best, worst = ordered[-1], ordered[0]
        return best, (worst if len(ordered) > 1 else None)

    best_trade, worst_trade = pick(trades)
    best_flip, worst_flip = pick(flips)
    return {
        "best_trade": best_trade, "worst_trade": worst_trade,
        "best_flip": best_flip, "worst_flip": worst_flip,
        "trade_count": len(trades), "flip_count": len(flips),
    }
