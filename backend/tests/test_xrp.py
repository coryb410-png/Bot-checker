import asyncio
import sys
import types
from decimal import Decimal as D

if "httpx" not in sys.modules:
    try:
        import httpx  # noqa: F401
    except ImportError:
        stub = types.ModuleType("httpx"); stub.AsyncClient = object; stub.TransportError = Exception
        sys.modules["httpx"] = stub

from classifiers.classify import classify_all  # noqa: E402
from core.addresses import detect_chain  # noqa: E402
from core.models import Chain, TxType  # noqa: E402
from scanners.xrp import XRP, XrpScanner, account_id_hex, currency_symbol  # noqa: E402

ME = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
FRIEND = "rLNaPoKeeBjZe2qs6x52yVPZpZ8td4dc6w"
SOLO_ISSUER = "rsoLo2S1kiGeCcn6hCUXVrCpGMWLrRrLZz"
SOLO = "534F4C4F00000000000000000000000000000000"
NFT_ID = "00081388" + account_id_hex(FRIEND) + "0000000A" + "0000002A"
ME_PAGE = account_id_hex(ME) + "F" * 24


def acct(addr, before, after):
    return {"ModifiedNode": {"LedgerEntryType": "AccountRoot", "FinalFields": {"Account": addr, "Balance": str(after)},
                             "PreviousFields": {"Balance": str(before)}}}


def row(h, tx, nodes, result="tesSUCCESS", when="2026-10-01T12:00:00Z"):
    return {"hash": h, "close_time_iso": when, "validated": True, "tx_json": tx,
            "meta": {"TransactionResult": result, "AffectedNodes": nodes}}


ROWS = [
    # newest first
    row("FAIL", {"Account": ME, "TransactionType": "Payment", "Fee": "12", "Destination": FRIEND},
        [acct(ME, 51_239_988, 51_239_976)], result="tecUNFUNDED_PAYMENT", when="2026-10-04T12:00:00Z"),
    # Buy an NFT for 20 XRP (fee 0.000012); the NFT appears on my NFTokenPage
    row("NFTBUY", {"Account": ME, "TransactionType": "NFTokenAcceptOffer", "Fee": "12"},
        [acct(ME, 71_240_000, 51_239_988), acct(FRIEND, 5_000_000, 25_000_000),
         {"ModifiedNode": {"LedgerEntryType": "NFTokenPage", "LedgerIndex": ME_PAGE,
                           "FinalFields": {"NFTokens": [{"NFToken": {"NFTokenID": NFT_ID, "URI": "697066733A2F2F516D4E6674"}}]},
                           "PreviousFields": {"NFTokens": []}}}], when="2026-10-03T12:00:00Z"),
    # DEX trade: spend 30 XRP for 100 SOLO (trust line with me on the HIGH side)
    row("TRADE", {"Account": ME, "TransactionType": "OfferCreate", "Fee": "10"},
        [acct(ME, 101_240_010, 71_240_000),
         {"ModifiedNode": {"LedgerEntryType": "RippleState",
                           "FinalFields": {"Balance": {"currency": SOLO, "issuer": "rrrrrrrrrrrrrrrrrrrrBZbvji", "value": "-100"},
                                           "LowLimit": {"issuer": SOLO_ISSUER, "currency": SOLO, "value": "0"},
                                           "HighLimit": {"issuer": ME, "currency": SOLO, "value": "1000000"}},
                           "PreviousFields": {"Balance": {"currency": SOLO, "issuer": "rrrrrrrrrrrrrrrrrrrrBZbvji", "value": "0"}}}}],
        when="2026-10-02T12:00:00Z"),
    # Receive 101.24001 XRP from a friend (they pay the fee)
    row("PAYIN", {"Account": FRIEND, "TransactionType": "Payment", "Fee": "12", "Destination": ME},
        [{"CreatedNode": {"LedgerEntryType": "AccountRoot", "NewFields": {"Account": ME, "Balance": "101240010"}}},
         acct(FRIEND, 200_000_000, 98_759_978)], when="2026-10-01T12:00:00Z"),
]


class FakeRpc:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def post(self, url, json=None):
        method, params = json["method"], json["params"][0]
        self.calls.append((method, params.get("marker")))
        if method == "account_tx":
            i = params.get("marker") or 0
            res = {"transactions": self.pages[i], "status": "success"}
            if i + 1 < len(self.pages):
                res["marker"] = i + 1
            return {"result": res}
        if method == "account_nfts":
            return {"result": {"account_nfts": [{"NFTokenID": NFT_ID, "URI": "697066733A2F2F516D4E6674"}], "status": "success"}}
        raise AssertionError(method)


class FakeMeta:
    async def get(self, url, **kw):
        assert url == "https://ipfs.io/ipfs/QmNft"
        return {"name": "Xpunk #42", "image": "ipfs://QmPunk42.png", "collection": {"name": "Xpunks"}}


def scan(pages, since=None):
    sc = XrpScanner(http=FakeRpc(pages), meta_http=FakeMeta())

    async def go():
        return [t async for t in sc.scan(ME, since)]
    return sc, asyncio.run(go())


def test_detects_xrp():
    assert detect_chain(ME) == Chain.XRP
    assert currency_symbol(SOLO) == "SOLO" and currency_symbol("USD") == "USD" and currency_symbol("03" + "A" * 38) == "LP"


def test_payment_trade_nft_and_failed_tx():
    sc, txs = scan([ROWS[:2], ROWS[2:]])          # two pages
    by = {t.tx_hash: t for t in txs}
    assert [t.tx_hash for t in txs] == ["FAIL", "NFTBUY", "TRADE", "PAYIN"]
    classify_all(txs, {ME})

    pay = by["PAYIN"]
    assert [(l.asset, l.amount) for l in pay.legs] == [(XRP, D("101.24001"))] and pay.fee == 0
    assert pay.tx_type == TxType.TRANSFER_IN

    trade = by["TRADE"]
    legs = {l.asset.symbol: l.amount for l in trade.legs}
    assert legs == {"XRP": D("-30"), "SOLO": D("100")} and trade.fee == D("0.00001")
    assert trade.tx_type == TxType.SWAP
    solo = next(l.asset for l in trade.legs if l.asset.symbol == "SOLO")
    assert solo.contract == f"{SOLO}.{SOLO_ISSUER}"

    buy = by["NFTBUY"]
    nft = next(l for l in buy.legs if l.asset.is_nft)
    assert nft.amount == 1 and nft.asset.name == "Xpunk #42" and nft.asset.image == "https://ipfs.io/ipfs/QmPunk42.png"
    # collection = issuer + its unscrambled collection number (taxon)
    from scanners.xrp import nft_collection
    assert nft.asset.collection == "{}:{}".format(*nft_collection(NFT_ID)) and nft.asset.collection.startswith(FRIEND + ":")
    assert next(l.amount for l in buy.legs if l.asset == XRP) == D(-20)
    assert buy.tx_type == TxType.NFT_PURCHASE

    fail = by["FAIL"]
    assert fail.legs == [] and fail.fee == D("0.000012") and not fail.success
    assert fail.tx_type == TxType.NETWORK_FEE


def test_incremental_scan_stops_at_cursor():
    _, txs = scan([ROWS], since="TRADE")
    assert [t.tx_hash for t in txs] == ["FAIL", "NFTBUY"]


def test_owned_nfts():
    sc = XrpScanner(http=FakeRpc([[]]), meta_http=FakeMeta())
    held = asyncio.run(sc.held_nfts(ME))
    assert [(a.name, a.symbol) for a in held] == [("Xpunk #42", "Xpunks")]


def test_xrp_token_price_keys():
    from pricing.coingecko import _xrp_keys
    keys = _xrp_keys(f"{SOLO}.{SOLO_ISSUER}")
    assert f"solo.{SOLO_ISSUER.lower()}" in keys and f"{SOLO.lower()}.{SOLO_ISSUER.lower()}" in keys
