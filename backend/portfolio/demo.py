"""Example wallet: an invented portfolio that runs through the real classifier
and P&L engine without calling any blockchain or price provider.

Used by the "Try an example wallet" button so anyone can explore the
dashboard, and so you can test a deployment before adding API keys.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal as D
from urllib.parse import quote

from classifiers.spam import mark_spam
from core.models import Asset, Chain, Leg, NormalizedTx
from portfolio.pnl import Method
from portfolio.service import Portfolio, progress_key, recompute

# Clearly fake addresses (valid formats, never used on-chain by us)
BTC_ADDR = "bc1qexamp1e0000000000000000000000000000000"
ETH_ADDR = "0x000000000000000000000000000000000000e1a1"
ETH_COLD = "0x000000000000000000000000000000000000c01d"
SOL_ADDR = "Examp1eWa11et1111111111111111111111111111"


def pixel_art(seed: int, hue: int) -> str:
    """Small symmetric pixel sprite as an SVG data URI (invented artwork)."""
    rnd = random.Random(seed)
    fg, fg2 = f"hsl({hue} 70% 52%)", f"hsl({(hue + 40) % 360} 75% 70%)"
    bg = f"hsl({(hue + 200) % 360} 30% 16%)"
    rects = [f'<rect width="10" height="10" fill="{bg}"/>']
    for y in range(1, 9):
        for c in range(1, 5):
            v = rnd.random()
            if v < 0.5:
                continue
            col = fg2 if v > 0.85 else fg
            rects.append(f'<rect x="{c}" y="{y}" width="1" height="1" fill="{col}"/>'
                         f'<rect x="{9 - c}" y="{y}" width="1" height="1" fill="{col}"/>')
    rects.append('<rect x="3" y="4" width="1" height="1" fill="#fff"/><rect x="6" y="4" width="1" height="1" fill="#fff"/>')
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" shape-rendering="crispEdges">'
           + "".join(rects) + "</svg>")
    return "data:image/svg+xml," + quote(svg)


BTC = Asset(Chain.BITCOIN, "BTC", decimals=8)
ETH = Asset(Chain.ETHEREUM, "ETH")
SOL = Asset(Chain.SOLANA, "SOL", decimals=9)
USDC = Asset(Chain.ETHEREUM, "USDC", contract="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", decimals=6)
USDC_SOL = Asset(Chain.SOLANA, "USDC", contract="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", decimals=6)
PEPE = Asset(Chain.ETHEREUM, "PEPE", contract="0x6982508145454ce325ddbf47a25d4ec3d2311933")
FOX = Asset(Chain.ETHEREUM, "FOX", contract="0x00000000000000000000000000000000000f0c5e", decimals=0,
            is_nft=True, token_id="412", name="Pixel Fox #412", image=pixel_art(412, 24))
MOTH12 = Asset(Chain.SOLANA, "MOTH", contract="Examp1eG1owmoth12", decimals=0, is_nft=True,
               token_id="Examp1eG1owmoth12", name="Glowmoth #12", image=pixel_art(12, 140))
MOTH = Asset(Chain.SOLANA, "MOTH", contract="Examp1eG1owmoth88", decimals=0, is_nft=True,
             token_id="Examp1eG1owmoth88", name="Glowmoth #88", image=pixel_art(88, 168))

SCAM = Asset(Chain.ETHEREUM, "Visit claim-eth.xyz", contract="0x00000000000000000000000000000000005ca111",
             decimals=18, name="$5,000 ETH reward")

CURRENT = {BTC.key: D(104000), ETH.key: D(3900), SOL.key: D(185), USDC.key: D(1),
           USDC_SOL.key: D(1), PEPE.key: D("0.0000085")}


def _tx(h, day, chain, wallet, legs, fee="0", fee_usd="0", source=None, interacted=None):
    fee_asset = {Chain.BITCOIN: BTC, Chain.ETHEREUM: ETH, Chain.SOLANA: SOL}[chain]
    return NormalizedTx(chain, h, datetime.fromisoformat(day).replace(tzinfo=timezone.utc), wallet, legs,
                        D(fee), fee_asset if D(fee) else None, D(fee_usd), source=source, interacted_with=interacted)


def L(asset, amount, usd, cp=None):
    return Leg(asset, D(str(amount)), counterparty=cp, usd_value=D(str(usd)))


def build_demo(pid: str, method: Method = Method.FIFO) -> Portfolio:
    txs = [
        _tx("example-btc-01", "2025-01-14", Chain.BITCOIN, BTC_ADDR, [L(BTC, "0.5", 47500, "bc1qexchange")]),
        _tx("0xexample02", "2025-02-03", Chain.ETHEREUM, ETH_ADDR, [L(USDC, -6000, 6000), L(ETH, 2, 6000)], "0.004", 12, "UNISWAP"),
        _tx("Example03", "2025-03-11", Chain.SOLANA, SOL_ADDR, [L(USDC_SOL, -3000, 3000), L(SOL, 20, 3000)], "0.00002", "0.01", "JUPITER"),
        _tx("0xexample04", "2025-04-02", Chain.ETHEREUM, ETH_ADDR, [L(ETH, "-0.5", 900), L(PEPE, 90000000, 900)], "0.005", 9, "UNISWAP"),
        _tx("0xexample05", "2025-05-20", Chain.ETHEREUM, ETH_ADDR, [L(ETH, "-1.2", 3000), L(FOX, 1, 3000)], "0.003", "7.5", "OPENSEA"),
        _tx("0xexample06", "2025-06-15", Chain.ETHEREUM, ETH_ADDR, [L(ETH, "-0.25", 640, ETH_COLD)], "0.0004", 1),
        _tx("0xexample06", "2025-06-15", Chain.ETHEREUM, ETH_COLD, [L(ETH, "0.25", 640, ETH_ADDR)]),
        _tx("Example07", "2025-07-08", Chain.SOLANA, SOL_ADDR, [L(SOL, -3, 480), L(MOTH, 1, 480)], "0.00002", "0.01", "TENSOR"),
        _tx("Example08", "2025-08-19", Chain.SOLANA, SOL_ADDR, [L(SOL, -8, 1520), L(USDC_SOL, 1520, 1520)], "0.00002", "0.01", "JUPITER"),
        _tx("Example16", "2025-09-10", Chain.SOLANA, SOL_ADDR, [L(SOL, -4, 760), L(MOTH12, 1, 760)], "0.00002", "0.01", "MAGIC_EDEN"),
        _tx("0xexample09", "2025-09-02", Chain.ETHEREUM, ETH_ADDR, [L(FOX, -1, 8400), L(ETH, 2, 8400)], "0.002", "8.4", "OPENSEA"),
        _tx("example-btc-10", "2025-10-27", Chain.BITCOIN, BTC_ADDR, [L(BTC, "-0.1", 11400, "bc1qfriend")], "0.00002", "2.28"),
        _tx("0xexample11", "2025-11-10", Chain.ETHEREUM, ETH_ADDR, [L(PEPE, -90000000, 630), L(USDC, 630, 630)], "0.003", "6.6", "UNISWAP"),
        _tx("0xexample12", "2026-01-22", Chain.ETHEREUM, ETH_COLD, [L(ETH, -1, 3300), L(USDC, 3300, 3300)], "0.002", "6.6", "UNISWAP"),
        _tx("0xexample13", "2026-03-05", Chain.ETHEREUM, ETH_ADDR, [], "0.001", 4, "AAVE", "0xaave"),
        _tx("0xexample15", "2026-08-12", Chain.ETHEREUM, ETH_ADDR,
            [Leg(SCAM, D(5000), counterparty="0xspammer")]),     # unsolicited airdrop: hidden as spam
        _tx("Example17", "2026-02-14", Chain.SOLANA, SOL_ADDR, [L(MOTH12, -1, 270), L(SOL, "1.5", 270)], "0.00002", "0.01", "TENSOR"),
        _tx("Example14", "2026-10-04", Chain.SOLANA, SOL_ADDR, [L(SOL, 5, 900, "SomeoneE1se")]),
    ]
    p = Portfolio(id=pid, addresses=[(Chain.BITCOIN, BTC_ADDR), (Chain.ETHEREUM, ETH_ADDR),
                                     (Chain.ETHEREUM, ETH_COLD), (Chain.SOLANA, SOL_ADDR)],
                  method=method, status="ready", txs=txs, demo=True)
    mark_spam(p.txs)
    p.progress = {progress_key(c, a): sum(1 for t in txs if t.wallet == a and t.chain == c) for c, a in p.addresses}
    recompute(p, CURRENT)
    from pricing.floors import Floor, collection_key
    p.floors = {collection_key(MOTH): Floor(D(4), "SOL", "Magic Eden (example)")}
    p.native_usd = {"SOL": CURRENT[SOL.key], "ETH": CURRENT[ETH.key]}
    recompute(p, {**CURRENT, MOTH.key: D(4) * CURRENT[SOL.key]})
    p.updated_at = datetime.now(timezone.utc)
    return p
