from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from classifiers.classify import classify_all
from core.addresses import InvalidAddress, detect_chain, looks_like_secret
from core.models import Asset, Chain, Leg, NormalizedTx, TxType
from portfolio.pnl import Method, compute_pnl

ETH = Asset(Chain.ETHEREUM, "ETH")
SOL = Asset(Chain.SOLANA, "SOL", decimals=9)
BTC = Asset(Chain.BITCOIN, "BTC", decimals=8)
USDC_SOL = Asset(Chain.SOLANA, "USDC", contract="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", decimals=6)
USDC_ETH = Asset(Chain.ETHEREUM, "USDC", contract="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", decimals=6)
PEPE = Asset(Chain.ETHEREUM, "PEPE", contract="0x6982508145454ce325ddbf47a25d4ec3d2311933")
NFT = Asset(Chain.ETHEREUM, "PUNK", contract="0xb47e3cd837ddf8e4c57f05d70ab865de6e193bbb", is_nft=True, token_id="7")

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
ME_ETH = "0x1111111111111111111111111111111111111111"
ME_ETH2 = "0x2222222222222222222222222222222222222222"
OTHER = "0x9999999999999999999999999999999999999999"


def tx(h, day, legs, fee=D(0), fee_asset=None, fee_usd=None, chain=Chain.ETHEREUM, wallet=ME_ETH, **kw):
    return NormalizedTx(chain, h, T0 + timedelta(days=day), wallet, legs, fee, fee_asset, fee_usd, **kw)


# ---------------------------------------------------------------- addresses
@pytest.mark.parametrize("addr,chain", [
    ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", Chain.BITCOIN),
    ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", Chain.BITCOIN),
    ("bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0", Chain.BITCOIN),
    ("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", Chain.ETHEREUM),
    ("JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", Chain.SOLANA),
])
def test_detect(addr, chain):
    assert detect_chain(addr) == chain


@pytest.mark.parametrize("bad", [
    "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb",              # bad checksum
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5",       # bad bech32 checksum
    "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA9604",        # 39 hex chars
    "hello",
])
def test_reject(bad):
    with pytest.raises(InvalidAddress):
        detect_chain(bad)


def test_secret_detection():
    assert looks_like_secret(" ".join(["abandon"] * 11 + ["about"]))
    assert looks_like_secret("a" * 64)
    assert not looks_like_secret("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")


# ---------------------------------------------------------------- classifier
def test_classification_rules():
    txs = [
        tx("buy", 0, [Leg(USDC_SOL, D(-1000)), Leg(SOL, D(10))], chain=Chain.SOLANA),
        tx("sell", 1, [Leg(SOL, D(-5)), Leg(USDC_SOL, D(900))], chain=Chain.SOLANA),
        tx("swap", 2, [Leg(ETH, D(-1)), Leg(PEPE, D(1000000))]),
        tx("nftbuy", 3, [Leg(ETH, D(-2)), Leg(NFT, D(1))]),
        tx("nftsell", 4, [Leg(NFT, D(-1)), Leg(ETH, D(3))]),
        tx("in", 5, [Leg(ETH, D(1), counterparty=OTHER)]),
        tx("out", 6, [Leg(ETH, D(-1), counterparty=OTHER)]),
        tx("mine", 7, [Leg(ETH, D(-1), counterparty=ME_ETH2)]),
        tx("bridge", 8, [Leg(ETH, D(-1), counterparty="0x3154cf16ccdb4c6d922629664174b904d80f2c35")]),
        tx("approve", 9, [], fee=D("0.001"), fee_asset=ETH, interacted_with=OTHER),
    ]
    got = {t.tx_hash: t.tx_type for t in classify_all(txs, {ME_ETH, ME_ETH2})}
    assert got == {
        "buy": TxType.BUY, "sell": TxType.SELL, "swap": TxType.SWAP,
        "nftbuy": TxType.NFT_PURCHASE, "nftsell": TxType.NFT_SALE,
        "in": TxType.TRANSFER_IN, "out": TxType.TRANSFER_OUT,
        "mine": TxType.INTERNAL_TRANSFER, "bridge": TxType.BRIDGE,
        "approve": TxType.CONTRACT_INTERACTION,
    }


# ---------------------------------------------------------------- P&L
def _history():
    return classify_all([
        tx("b1", 0, [Leg(USDC_ETH, D(-2000), usd_value=D(2000)), Leg(ETH, D(1), usd_value=D(2000))]),
        tx("b2", 10, [Leg(USDC_ETH, D(-3000), usd_value=D(3000)), Leg(ETH, D(1), usd_value=D(3000))]),
        tx("s1", 20, [Leg(ETH, D(-1), usd_value=D(4000)), Leg(USDC_ETH, D(4000), usd_value=D(4000))],
           fee=D("0.01"), fee_asset=ETH, fee_usd=D(40)),
    ], {ME_ETH})


def test_fifo():
    r = compute_pnl(_history(), Method.FIFO, {"native:ETH": D(5000)})
    eth = r.assets["native:ETH"]
    sale = [d for d in r.disposals if d.tx_hash == "s1"][0]
    # gas (0.01) leaves first from the oldest lot, so the 1 ETH sold is
    # 0.99 @ $2000 + 0.01 @ $3000 = $2010 basis
    assert sale.realized == D(4000) - D(2010) - D(40)
    assert eth.quantity == D("0.99")                              # 2 bought, 1 sold, 0.01 gas


def test_average():
    r = compute_pnl(_history(), Method.AVERAGE)
    # gas taken first leaves 1.99 ETH at avg $2500, then 1 sold
    sale = [d for d in r.disposals if d.tx_hash == "s1"][0]
    assert sale.realized == D(4000) - D(2500) - D(40)
    assert r.assets[USDC_ETH.key].quantity == D(4000)      # sale proceeds held
    assert r.complete


def test_unknown_basis_is_flagged_not_invented():
    txs = classify_all([
        tx("in", 0, [Leg(ETH, D(2), counterparty=OTHER, usd_value=D(4000))]),
        tx("s", 5, [Leg(ETH, D(-1), usd_value=D(3000)), Leg(USDC_ETH, D(3000), usd_value=D(3000))]),
    ], {ME_ETH})
    r = compute_pnl(txs)
    sale = [d for d in r.disposals if d.symbol == "ETH"][0]
    assert sale.realized is None
    assert r.assets["native:ETH"].incomplete
    assert r.warnings
    # A user-supplied cost completes it
    r2 = compute_pnl(txs, manual_costs={"in": D(2000)})
    assert [d for d in r2.disposals if d.symbol == "ETH"][0].realized == D(2000)
    assert not r2.assets["native:ETH"].incomplete


def test_internal_transfer_keeps_basis():
    txs = classify_all([
        tx("b", 0, [Leg(USDC_ETH, D(-1000), usd_value=D(1000)), Leg(ETH, D(1), usd_value=D(1000))]),
        tx("m", 1, [Leg(ETH, D(-1), counterparty=ME_ETH2)]),
        tx("m", 1, [Leg(ETH, D(1), counterparty=ME_ETH)], wallet=ME_ETH2),
        tx("s", 2, [Leg(ETH, D(-1), usd_value=D(1500)), Leg(USDC_ETH, D(1500), usd_value=D(1500))], wallet=ME_ETH2),
    ], {ME_ETH, ME_ETH2})
    r = compute_pnl(txs)
    eth_sales = [d.realized for d in r.disposals if d.symbol == "ETH"]
    assert eth_sales == [D(500)]


def test_example_wallet():
    from decimal import Decimal as D
    from portfolio.demo import build_demo
    p = build_demo("demo-test")
    r = p.report
    types = {t.tx_hash: t.tx_type for t in p.txs}
    assert types["0xexample06"] == TxType.INTERNAL_TRANSFER
    assert types["0xexample05"] == TxType.NFT_PURCHASE and types["0xexample09"] == TxType.NFT_SALE
    assert not r.complete                       # BTC and SOL arrived with no known cost
    assert r.portfolio_value > 0
    held = {a.symbol for a in r.assets.values() if a.quantity > 0}
    assert {"BTC", "ETH", "SOL", "MOTH"} <= held and "FOX" not in held
    nft = next(l.asset for t in p.txs for l in t.legs if l.asset.symbol == "MOTH")
    assert nft.image.startswith("data:image/svg+xml,") and nft.name == "Glowmoth #88"


def test_highlights_example_wallet():
    from decimal import Decimal as D
    from portfolio.demo import build_demo
    from portfolio.highlights import compute_highlights
    h = compute_highlights(build_demo("demo-h"))
    assert h["best_trade"]["label"].startswith("Sold · SOL") and h["best_trade"]["realized"] > 0
    assert h["worst_trade"]["label"] == "Swap · ETH → PEPE" and h["worst_trade"]["realized"] < 0
    bf, wf = h["best_flip"], h["worst_flip"]
    assert bf["name"] == "Pixel Fox #412" and bf["realized"] == D(8400) - D(3000) - D("8.4")
    assert bf["held_days"] == 105 and bf["buy_tx"] == "0xexample05"
    assert wf["name"] == "Glowmoth #12" and wf["realized"] == D(270) - D(760) - D("0.01")
    assert h["flip_count"] == 2
