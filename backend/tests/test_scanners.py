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


def test_pricing_respects_free_history_and_never_fails(monkeypatch):
    import asyncio
    from datetime import datetime, timedelta, timezone
    from core.models import Asset, Chain, Leg, NormalizedTx
    from pricing.coingecko import PriceService
    from scanners.base import ProviderError

    now = datetime.now(timezone.utc)
    calls = []

    class FakeHttp:
        async def get(self, url, params=None):
            calls.append((url, params))
            if "market_chart" in url:
                if params["from"] < (now - timedelta(days=365)).timestamp():
                    raise ProviderError("401 exceeds the allowed time range")
                ts = int((now - timedelta(days=10)).timestamp() * 1000)
                return {"prices": [[ts, 2500.0]]}
            raise ProviderError("down")

    monkeypatch.setenv("COINGECKO_PLAN", "demo")
    ps = PriceService(http=FakeHttp())
    eth = Asset(Chain.ETHEREUM, "ETH")
    old = NormalizedTx(Chain.ETHEREUM, "old", now - timedelta(days=900), "w", [Leg(eth, D(1))])
    new = NormalizedTx(Chain.ETHEREUM, "new", now - timedelta(days=10), "w", [Leg(eth, D(2))])
    asyncio.run(ps.price_transactions([old, new]))      # must not raise
    frm = calls[0][1]["from"]
    assert frm >= (now - timedelta(days=365)).timestamp()  # clamped to the free window
    assert new.legs[0].usd_value == D(5000) and old.legs[0].usd_value is None
    assert asyncio.run(ps.current([eth])) == {}          # current-price outage is non-fatal


def test_coin_directory_replaces_per_token_lookups(monkeypatch):
    import asyncio
    from core.models import Asset, Chain
    from pricing.coingecko import PriceService

    calls = []

    class FakeHttp:
        async def get(self, url, params=None):
            calls.append(url)
            if url == "/coins/list":
                return [{"id": "pepe", "platforms": {"ethereum": "0xABC"}},
                        {"id": "bonk", "platforms": {"solana": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"}}]
            raise AssertionError(f"unexpected call {url}")

    monkeypatch.setenv("COINGECKO_PLAN", "demo")
    ps = PriceService(http=FakeHttp())
    asyncio.run(ps._load_directory())
    pepe = Asset(Chain.ETHEREUM, "PEPE", contract="0xabc")
    bonk = Asset(Chain.SOLANA, "BONK", contract="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
    junk = Asset(Chain.ETHEREUM, "JUNK", contract="0xdead")
    scam = Asset(Chain.ETHEREUM, "Visit claim.xyz", contract="0xbeef")
    ids = [asyncio.run(ps.coin_id(a)) for a in (pepe, bonk, junk, scam)]
    assert ids == ["pepe", "bonk", None, None]
    assert calls == ["/coins/list"]          # one call total, no per-token lookups


def test_robinhood_chain_swap_and_nft():
    from scanners.robinhood import RobinhoodScanner
    me = "0x579d41f9be27cd2d0b9c2decc82877ae87c56b4c"
    ts = "2026-11-07T02:41:55.000000Z"
    txs = [{  # shape taken from the live Robinhood Chain Blockscout API
        "hash": "0xa2d9", "timestamp": ts, "status": "ok", "value": "2300000000000000",
        "fee": {"type": "actual", "value": "4021806244000"},
        "from": {"hash": "0x579d41f9bE27cD2d0B9c2DecC82877ae87c56B4C", "is_contract": False},
        "to": {"hash": "0x65050A9b7E5075A2bA5cED7b1b64EE66262c40Dc", "is_contract": True, "name": "Swap Router"},
    }]
    tokens = [
        {"transaction_hash": "0xa2d9", "timestamp": ts,
         "from": {"hash": "0xpool"}, "to": {"hash": me},
         "token": {"address_hash": "0xTSLA", "symbol": "TSLA", "name": "Tesla (tokenized)", "decimals": "18", "type": "ERC-20"},
         "total": {"decimals": "18", "value": "500000000000000000"}},
        {"transaction_hash": "0xnft", "timestamp": ts,
         "from": {"hash": "0xminter"}, "to": {"hash": me},
         "token": {"address_hash": "0xNFT", "symbol": "HOOD", "name": "Hood Pass", "type": "ERC-721"},
         "total": {"token_id": "7", "token_instance": {"image_url": "ipfs://Qm1/7.png", "metadata": {"name": "Hood Pass #7"}}}},
    ]
    out = {t.tx_hash: t for t in RobinhoodScanner.normalize(me, txs, [], tokens)}
    swap = out["0xa2d9"]
    assert {(l.asset.symbol, l.amount) for l in swap.legs} == {("ETH", D("-0.0023")), ("TSLA", D("0.5"))}
    assert swap.fee == D("0.000004021806244") and swap.source == "Swap Router"
    assert swap.chain.value == "robinhood"
    assert classify_all([swap], {me})[0].tx_type == TxType.SWAP
    nft = out["0xnft"].legs[0].asset
    assert nft.is_nft and nft.name == "Hood Pass #7" and nft.image == "https://ipfs.io/ipfs/Qm1/7.png"


def test_eth_address_is_also_scanned_on_robinhood_chain():
    from core.models import Chain
    from portfolio.pnl import Method
    from portfolio.service import Store
    p = Store().create(["0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045", "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"], Method.FIFO)
    chains = [c for c, _ in p.addresses]
    assert chains.count(Chain.ROBINHOOD) == 1 and Chain.ETHEREUM in chains and Chain.BITCOIN in chains


def test_solana_nft_lookup_sends_key_as_param(monkeypatch):
    import asyncio
    monkeypatch.setenv("HELIUS_API_KEY", "abc123")
    monkeypatch.delenv("HELIUS_RPC_URL", raising=False)
    seen = {}

    class FakeRpc:
        async def post(self, url, params=None, json=None):
            seen.update(params=params, method=json["method"])
            return {"result": [{"id": "Mint9", "interface": "V1_NFT",
                                "content": {"metadata": {"name": "Stray #9", "symbol": "STRAY"},
                                            "links": {"image": "https://arweave.net/x.png"}}}]}

    sc = SolanaScanner(http=object(), rpc=FakeRpc())
    asyncio.run(sc._resolve_mints({"Mint9"}))
    assert seen == {"params": {"api-key": "abc123"}, "method": "getAssetBatch"}
    a = sc._asset("Mint9", "NonFungible")
    assert a.is_nft and a.name == "Stray #9" and a.image == "https://arweave.net/x.png"


def test_alchemy_spam_uses_verdict_not_heuristics():
    from scanners.ethereum import parse_alchemy_nfts
    res = {"nfts": [
        {"contract": {"address": "0xA", "name": "WEThBandits", "isSpam": False,
                      "spamClassifications": ["Erc721DishonestTotalSupply"]},
         "tokenId": "1979", "image": {"thumbnailUrl": "https://res.cloudinary.com/x.png"}},
        {"contract": {"address": "0xB", "name": "Claim Rewards", "isSpam": True, "spamClassifications": ["Erc721TooManyOwners"]},
         "tokenId": "1", "image": {}},
    ]}
    out = parse_alchemy_nfts(res)
    assert out[("0xa", "1979")]["spam"] is False
    assert out[("0xb", "1")]["spam"] is True


def test_solana_compressed_nfts(monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "k")
    sc = SolanaScanner(http=object(), rpc=object())
    sc._meta["cAsset1"] = {"symbol": "DRIP", "decimals": 0, "is_nft": True, "name": "DRiP Drop #5",
                           "image": "https://cdn.helius-rpc.com/x.png", "compressed": True, "spam": False}
    sc._meta["cScam"] = {"symbol": "X", "decimals": 0, "is_nft": True, "name": "Claim 500 USDC at drop.xyz",
                         "image": None, "compressed": True, "spam": True}
    base = {"timestamp": 1_762_000_000, "fee": 5000, "transactionError": None, "instructions": [],
            "nativeTransfers": [], "tokenTransfers": []}
    received = sc.normalize({**base, "signature": "s1", "feePayer": "minter",
                             "events": {"compressed": [{"type": "COMPRESSED_NFT_MINT", "assetId": "cAsset1",
                                                        "newLeafOwner": ME_SOL, "oldLeafOwner": None}]}}, ME_SOL)
    sent = sc.normalize({**base, "signature": "s2", "feePayer": ME_SOL,
                         "events": {"compressed": [{"type": "COMPRESSED_NFT_TRANSFER", "assetId": "cAsset1",
                                                    "newLeafOwner": "friend", "oldLeafOwner": ME_SOL}]}}, ME_SOL)
    scam = sc.normalize({**base, "signature": "s3", "feePayer": "spammer",
                         "events": {"compressed": [{"type": "COMPRESSED_NFT_TRANSFER", "assetId": "cScam",
                                                    "newLeafOwner": ME_SOL, "oldLeafOwner": "spammer"}]}}, ME_SOL)
    leg = received.legs[0]
    assert leg.amount == 1 and leg.asset.is_nft and leg.asset.compressed and leg.asset.name == "DRiP Drop #5"
    assert sent.legs[0].amount == -1 and sent.fee > 0
    assert scam.legs[0].asset.spam is True
    from classifiers.spam import is_spam_tx
    assert is_spam_tx(scam) and not is_spam_tx(received)


def test_das_marks_compressed_and_scam():
    from scanners.solana import parse_das_asset
    m = parse_das_asset({"id": "c1", "interface": "V1_NFT", "compression": {"compressed": True},
                         "content": {"metadata": {"name": "Free gift", "description": "Visit claim-sol.com now"},
                                     "files": [{"uri": "https://a/x.png", "cdn_uri": "https://cdn.helius-rpc.com/x", "mime": "image/png"}]}})
    assert m["compressed"] and m["is_nft"] and m["spam"] and m["image"] == "https://cdn.helius-rpc.com/x"


def test_escrow_listing_then_sale_is_a_sale(monkeypatch):
    """Magic Eden-style escrow: the NFT leaves at listing; the sale tx only pays SOL."""
    monkeypatch.setenv("HELIUS_API_KEY", "k")
    sc = SolanaScanner(http=object(), rpc=object())
    sc._meta["Fox1"] = {"symbol": "FOX", "decimals": 0, "is_nft": True, "name": "Fox #1", "image": None}
    base = {"timestamp": 1_762_000_000, "fee": 5000, "transactionError": None, "instructions": [], "source": "MAGIC_EDEN"}
    listing = sc.normalize({**base, "signature": "list", "feePayer": ME_SOL, "type": "NFT_LISTING",
        "nativeTransfers": [{"fromUserAccount": ME_SOL, "toUserAccount": "escrow", "amount": 2_039_280}],
        "tokenTransfers": [{"fromUserAccount": ME_SOL, "toUserAccount": "escrow", "mint": "Fox1", "tokenAmount": 1, "tokenStandard": "NonFungible"}],
        "events": {"nft": {"type": "NFT_LISTING", "seller": ME_SOL, "amount": 5_000_000_000, "nfts": [{"mint": "Fox1"}]}}}, ME_SOL)
    sale = sc.normalize({**base, "signature": "sale", "feePayer": "buyer", "type": "NFT_SALE",
        "nativeTransfers": [{"fromUserAccount": "buyer", "toUserAccount": ME_SOL, "amount": 4_800_000_000}],
        "tokenTransfers": [{"fromUserAccount": "escrow", "toUserAccount": "buyer", "mint": "Fox1", "tokenAmount": 1, "tokenStandard": "NonFungible"}],
        "events": {"nft": {"type": "NFT_SALE", "seller": ME_SOL, "buyer": "buyer", "amount": 5_000_000_000, "nfts": [{"mint": "Fox1"}]}}}, ME_SOL)
    l, s_ = classify_all([listing, sale], {ME_SOL})
    assert l.legs == [] and l.label == "Listed NFT for sale · Magic Eden"
    assert s_.tx_type == TxType.NFT_SALE
    assert {(x.asset.symbol, x.amount) for x in s_.legs} == {("FOX", D(-1)), ("SOL", D("4.8"))}   # net of fees


def test_purchase_paid_through_other_accounts_uses_sale_price(monkeypatch):
    monkeypatch.setenv("HELIUS_API_KEY", "k")
    sc = SolanaScanner(http=object(), rpc=object())
    tx = sc.normalize({"signature": "buy", "timestamp": 1_762_000_000, "fee": 5000, "feePayer": ME_SOL,
        "transactionError": None, "instructions": [], "type": "NFT_SALE", "source": "TENSOR", "nativeTransfers": [],
        "tokenTransfers": [{"fromUserAccount": "seller", "toUserAccount": ME_SOL, "mint": "Moth9", "tokenAmount": 1, "tokenStandard": "NonFungible"}],
        "events": {"nft": {"type": "NFT_SALE", "seller": "seller", "buyer": ME_SOL, "amount": 1_500_000_000, "nfts": [{"mint": "Moth9"}]}}}, ME_SOL)
    t = classify_all([tx], {ME_SOL})[0]
    assert t.tx_type == TxType.NFT_PURCHASE
    assert ("SOL", D("-1.5")) in {(x.asset.symbol, x.amount) for x in t.legs}


def test_blur_pool_is_priced_as_eth(monkeypatch):
    monkeypatch.setenv("ALCHEMY_API_KEY", "test")
    sc = EthereumScanner(http=object(), nft_http=object())
    ts = {"blockTimestamp": "2026-10-21T12:00:00.000Z"}
    tx = sc._normalize("0xs", [
        {"hash": "0xs", "from": ME_ETH, "to": "0xbuyer", "category": "erc721", "asset": "BANDIT",
         "erc721TokenId": "0x7", "tokenId": "0x7", "rawContract": {"address": "0xNFT", "value": None}, "metadata": ts},
        {"hash": "0xs", "from": "0xblur", "to": ME_ETH, "category": "erc20", "asset": "BLUR POOL",
         "rawContract": {"address": "0x0000000000A39bb272e79075ade125fd351887Ac", "value": hex(2 * 10**17), "decimal": "0x12"}, "metadata": ts},
    ], ME_ETH)
    pool = next(l.asset for l in tx.legs if l.asset.symbol == "BLUR POOL")
    assert pool.coingecko_id == "ethereum"
    assert classify_all([tx], {ME_ETH})[0].tx_type == TxType.NFT_SALE
