"""Portfolio value over time, one point per day.

Replays every (non-spam) transaction to know how much of each coin and token
the portfolio held at the end of each day, then multiplies by that day's USD
price. NFTs aren't included: there's no reliable history of floor prices, so
they're valued at today's floor in the headline total only.

The free CoinGecko plan only has the last 365 days of prices, so the chart
covers at most that window (all-time with COINGECKO_PLAN=pro).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from classifiers.spam import is_spam_tx

ZERO = Decimal(0)


async def value_history(p, prices, max_days: int | None = 365) -> list[dict]:
    txs = sorted((t for t in p.txs if not is_spam_tx(t)), key=lambda t: t.timestamp)
    if not txs:
        return []

    assets = {}
    for t in txs:
        for l in t.legs:
            if not l.asset.is_nft and not l.asset.spam:
                assets[l.asset.key] = l.asset
        if t.fee and t.fee_asset:
            assets[t.fee_asset.key] = t.fee_asset

    today = datetime.now(timezone.utc).date()
    first = txs[0].timestamp.date()
    window = getattr(prices, "history_days", None) or max_days
    start = max(first, today - timedelta(days=window)) if window else first

    start_dt = datetime.combine(start, time.min, timezone.utc)
    cids = {k: await prices.history_for(a, start_dt) for k, a in assets.items()}

    balances: dict[str, Decimal] = defaultdict(lambda: ZERO)
    i, points = 0, []
    day = first
    while day <= today:
        end_of_day = datetime.combine(day, time.max, timezone.utc)
        while i < len(txs) and txs[i].timestamp <= end_of_day:
            t = txs[i]
            for l in t.legs:
                if l.asset.key in assets:
                    balances[l.asset.key] += l.amount
            if t.fee and t.fee_asset and t.fee_asset.key in assets:
                balances[t.fee_asset.key] -= t.fee
            i += 1
        if day >= start:
            total, priced = ZERO, False
            noon = datetime.combine(day, time(12), timezone.utc)
            for k, qty in balances.items():
                if qty <= 0:
                    continue
                price = prices.price_on(cids.get(k), assets[k], noon)
                if price is not None:
                    total += qty * price
                    priced = True
            if priced or total == 0:
                points.append({"date": day.isoformat(), "value": format(total.quantize(Decimal("0.01")), "f")})
        day += timedelta(days=1)
    return points
