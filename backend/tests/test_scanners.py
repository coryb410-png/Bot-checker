"""Normalization tests using provider-shaped fixtures (no network)."""
import sys
import types
from decimal import Decimal as D

if "httpx" not in sys.modules:   # allow running without installed deps
    try:
        import httpx  # noqa: F401
    except ImportError:
        stub = types.ModuleType("httpx")
        stub.AsyncClient = object
        stub.TransportError = Exception
        sys.modules["httpx"] = stub

from classifiers.classify import classify_all  # noqa: E402
from core.models import TxType  # noqa: E402
from scanners.bitcoin import BitcoinScanner  # noqa: E402
from scanners.ethereum import EthereumScanner  # noqa: E402
from scanners.solana import SolanaScanner  # noqa: E402

ME_BTC = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
ME_SOL = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
ME_ETH = "0x1111111111111111111111111111111111111111"


def test_bitcoin_send_with_change():
    raw = {
        "txid": "ab" * 32, "fee": 1_000, "status": {"confirmed": True, "block_time": 1_700_000_000},
        "vin": [{"prevout": {"scriptpubkey_address": ME_BTC, "value": 100_000}}],
        "vout": [{"scriptpubkey_address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "value": 60_000},
                 {"scriptpubkey_address": ME_BTC, "value": 39_000}],
    }
    tx = BitcoinScanner.normalize(raw, ME_BTC)
    assert tx.legs[0].amount == D("-0.0006")      # change and fee excluded
    assert tx.fee == D("0.00001")
    assert classify_all([tx], {ME_BTC})[0].tx_type == TxType.TRANSFER_OUT


def test_solana_jupiter_swap(monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "test")
    sc = SolanaScanner(http=object(), rpc=object())
    raw = {
        "signature": "5sig", "timestamp": 1_762_000_000, "fee": 5000, "feePayer": ME_SOL,
        "source": "JUPITER", "type": "SWAP", "transactionError": None, "instructions": [],
        "nativeTransfers": [{"fromUserAccount": ME_SOL, "toUserAccount": "rent", "amount": 2_039_280}],
        "tokenTransfers": [
            {"fromUserAccount": ME_SOL, "toUserAccount": "pool", "mint": "So11111111111111111111111111111111111111112", "tokenAmount": 10.0},
            {"fromUserAccount": "pool", "toUserAccount": ME_SOL, "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "tokenAmount": 1530.25},
        ],
    }
    tx = sc.normalize(raw, ME_SOL)
    amounts = {l.asset.symbol: l.amount for l in tx.legs}
    assert amounts == {"SOL": D("-10.0"), "USDC": D("1530.25")}
    assert tx.fee == D("0.000005") + D("0.00203928")   # rent folded into fee
    t = classify_all([tx], {ME_SOL})[0]
    assert t.tx_type == TxType.SELL and t.label == "Sold · SOL → USDC"


def test_ethereum_nft_purchase(monkeypatch):
    monkeypatch.setenv("ALCHEMY_API_KEY", "test")
    sc = EthereumScanner(http=object(), nft_http=object())
    ts = {"blockTimestamp": "2026-10-21T12:00:00.000Z"}
    transfers = [
        {"hash": "0xh", "from": ME_ETH, "to": "0xseaport", "category": "external", "value": 1.5,
         "rawContract": {"value": hex(15 * 10**17), "decimal": "0x12", "address": None}, "metadata": ts},
        {"hash": "0xh", "from": "0xseller", "to": ME_ETH, "category": "erc721", "asset": "PUNK",
         "erc721TokenId": "0x7", "tokenId": "0x7", "rawContract": {"address": "0xABC", "value": None}, "metadata": ts},
    ]
    tx = sc._normalize("0xh", transfers, ME_ETH)
    assert {(l.asset.symbol, l.amount) for l in tx.legs} == {("ETH", D("-1.5")), ("PUNK", D(1))}
    assert classify_all([tx], {ME_ETH})[0].tx_type == TxType.NFT_PURCHASE


def test_nft_metadata_parsing():
    from core.models import safe_image
    from scanners.ethereum import parse_alchemy_nfts
    from scanners.solana import parse_das_asset

    assert safe_image("ipfs://Qm123/1.png") == "https://ipfs.io/ipfs/Qm123/1.png"
    assert safe_image("ipfs://ipfs/Qm123") == "https://ipfs.io/ipfs/Qm123"
    assert safe_image("javascript:alert(1)") is None
    assert safe_image("data:image/svg+xml,<svg>") is None

    eth = parse_alchemy_nfts({"nfts": [{
        "contract": {"address": "0xABC", "name": "Punks", "isSpam": False},
        "tokenId": "7", "name": None,
        "image": {"thumbnailUrl": "https://nft-cdn.alchemy.com/x.png"},
    }]})
    assert eth[("0xabc", "7")] == {"name": "Punks #7", "image": "https://nft-cdn.alchemy.com/x.png", "spam": False}

    sol = parse_das_asset({"id": "Mint1", "interface": "ProgrammableNFT",
                           "content": {"metadata": {"name": "Moth #88"}, "links": {"image": "ar://abc"}}})
    assert sol["is_nft"] and sol["name"] == "Moth #88" and sol["image"] == "https://arweave.net/abc"


def test_metadata_does_not_split_lots():
    from dataclasses import replace
    from core.models import Asset, Chain
    a = Asset(Chain.ETHEREUM, "PUNK", contract="0xabc", is_nft=True, token_id="7")
    assert replace(a, image="https://x/y.png", name="Punk #7") == a
