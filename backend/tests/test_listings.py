"""NFTs listed for sale: OpenSea + Magic Eden, joined with the scan's holdings."""
import asyncio
from decimal import Decimal

from core.models import Chain
from portfolio import listings as L


class FakeHttp:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    async def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        return self.pages(path, params or {})


def os_listing(chain, contract, tid, value, decimals=18, currency="ETH", end="1893456000", item_type=2):
    return {"order_hash": "0x1", "chain": chain, "type": "basic", "remaining_quantity": 1,
            "price": {"current": {"currency": currency, "decimals": decimals, "value": value}},
            "protocol_data": {"parameters": {"offer": [{"itemType": item_type, "token": contract,
                                                        "identifierOrCriteria": tid}], "endTime": end}}}


def test_opensea_listings_parse_and_page():
    def pages(path, params):
        assert path == "/account/0xme/listings"
        if "after" not in params:
            return {"listings": [os_listing("robinhood", "0xBEAR", "7", "50000000000000000"),
                                 os_listing("base", "0xpunk", "1", "25000000", 6, "USDC", end="0"),
                                 os_listing("ethereum", "0xft", "1", "1", item_type=1)],   # ERC-20 offer: not an NFT
                    "next": "c2"}
        return {"listings": [os_listing("matic", "0xpal", "3", "12000000000000000000", currency="POL")], "next": None}
    http = FakeHttp(pages)
    rows = asyncio.run(L.opensea_listings(http, "0xme"))
    assert [c[1].get("after") for c in http.calls] == [None, "c2"]
    bear, punk, pal = rows
    assert bear["chain"] == Chain.ROBINHOOD and bear["contract"] == "0xbear" and bear["price"] == Decimal("0.05")
    assert bear["url"] == "https://opensea.io/item/robinhood/0xbear/7" and bear["expires_at"].startswith("2030")
    assert punk["price"] == Decimal("25") and punk["currency"] == "USDC" and punk["expires_at"] is None
    assert pal["chain"] == Chain.POLYGON and pal["currency"] == "POL" and pal["price"] == Decimal("12")


def test_magiceden_listings():
    http = FakeHttp(lambda path, params: [
        {"mintAddress": "MintA", "name": "Retardio #1", "image": "https://x/a.png", "collectionName": "Retardio", "price": 1.25, "listStatus": "listed"},
        {"mintAddress": "MintB", "name": "Not listed", "price": 0, "listStatus": "unlisted"}])
    (row,) = asyncio.run(L.magiceden_listings(http, "SoLWallet"))
    assert http.calls[0] == ("/wallets/SoLWallet/tokens", {"listStatus": "listed", "offset": 0, "limit": 500})
    assert row["price"] == Decimal("1.25") and row["url"] == "https://magiceden.io/item-details/MintA"


def test_listings_join_holdings_and_value_in_dollars(monkeypatch):
    rows = [
        {"marketplace": "OpenSea", "chain": Chain.ROBINHOOD, "owner": "0xme", "contract": "0xbear", "token_id": "7",
         "price": Decimal("0.05"), "currency": "ETH", "quantity": 1, "expires_at": None, "url": "u1"},
        {"marketplace": "Magic Eden", "chain": Chain.SOLANA, "owner": "SoLWallet", "contract": "MintA", "token_id": "MintA",
         "price": Decimal("2"), "currency": "SOL", "quantity": 1, "expires_at": None, "url": "u2",
         "name": "Retardio #1", "image": "https://x/a.png", "collection_name": "Retardio"},
        {"marketplace": "OpenSea", "chain": Chain.BASE, "owner": "0xother", "contract": "0xpunk", "token_id": "1",
         "price": Decimal("25"), "currency": "USDC", "quantity": 1, "expires_at": None, "url": "u3"},
    ]
    calls = []

    async def fake_fetch(addresses):
        calls.append(addresses)
        return rows, [{"name": "OpenSea", "ok": True, "error": None}]

    monkeypatch.setattr(L, "fetch_all", fake_fetch)

    class P:
        addresses = [(Chain.ROBINHOOD, "0xme"), (Chain.SOLANA, "SoLWallet")]
        native_usd = {"ETH": 3000, "SOL": 150}
        listings_cache = {}

    holdings = [{"key": "robinhood:nft:0xBEAR:7", "nft": True, "name": "Robinhood Bear #7", "image": "https://i.seadn.io/b.png",
                 "collection": "robinhood:0xbear", "floor": {"usd": "120.00", "amount": "0.04", "currency": "ETH"},
                 "cost_basis": "90.00"}]
    p = P()
    out = asyncio.run(L.listings_for(p, holdings, None))
    assert out["count"] == 3 and out["total_usd"] == "475.00"
    bear = next(x for x in out["listings"] if x["marketplace"] == "OpenSea" and x["chain"] == "robinhood")
    assert bear["name"] == "Robinhood Bear #7" and bear["image"] == "https://i.seadn.io/b.png"
    assert bear["price_usd"] == "150.00" and bear["vs_floor_pct"] == 25.0 and bear["paid_usd"] == "90.00"
    assert [x["price_usd"] for x in out["listings"]] == ["300.00", "150.00", "25.00"]   # most valuable first
    # one wallet only, served from the cache
    mine = asyncio.run(L.listings_for(p, holdings, "0xME"))
    assert mine["count"] == 1 and len(calls) == 1


def test_fetch_all_reports_missing_opensea_key(monkeypatch):
    monkeypatch.delenv("OPENSEA_API_KEY", raising=False)
    rows, sources = asyncio.run(L.fetch_all([(Chain.ETHEREUM, "0xme")]))
    assert rows == [] and sources == [{"name": "OpenSea", "ok": False, "error": "OPENSEA_API_KEY isn't set"}]


def test_helius_listings_latest_event_wins():
    def ev(kind, mint, ts, source="TENSOR", amount=None, seller="Me", buyer=None):
        return {"signature": f"{kind}{mint}{ts}", "timestamp": ts, "type": kind, "source": source,
                "events": {"nft": {"type": kind, "source": source, "amount": amount, "seller": seller,
                                   "buyer": buyer, "timestamp": ts, "nfts": [{"mint": mint}]}}}

    def pages(path, params):
        assert path == "/addresses/Me/transactions" and params["api-key"] == "k"
        return {
            "NFT_LISTING": [ev("NFT_LISTING", "A", 300, amount=2_500_000_000),          # still listed (Tensor)
                            ev("NFT_LISTING", "B", 100, amount=1_000_000_000),          # later cancelled
                            ev("NFT_LISTING", "C", 100, "EXCHANGE_ART", 5_000_000_000), # later sold
                            ev("NFT_LISTING", "D", 50, "SOME_NEW_MARKET", 700_000_000)],
            "NFT_CANCEL_LISTING": [ev("NFT_CANCEL_LISTING", "B", 200)],
            "NFT_SALE": [ev("NFT_SALE", "C", 250, "EXCHANGE_ART", 5_000_000_000),
                         ev("NFT_SALE", "D", 60, "MAGIC_EDEN", 1, seller="Someone", buyer="Me")],   # buying ≠ selling
        }[params["type"]]

    rows = asyncio.run(L.helius_listings(FakeHttp(pages), "k", "Me"))
    got = {r["contract"]: r for r in rows}
    assert set(got) == {"A", "D"}
    assert got["A"]["marketplace"] == "Tensor" and got["A"]["price"] == Decimal("2.5") and got["A"]["must_hold"]
    assert got["D"]["marketplace"] == "Some New Market"


def test_xrpl_sell_offers():
    calls = []

    class XHttp:
        async def post(self, path, json=None):
            calls.append(json)
            p = json["params"][0]
            assert json["method"] == "account_objects" and p["type"] == "nft_offer"
            if "marker" not in p:
                return {"result": {"account_objects": [
                    {"LedgerEntryType": "NFTokenOffer", "Flags": 1, "NFTokenID": "000A", "Amount": "25000000", "Expiration": 820000000},
                    {"LedgerEntryType": "NFTokenOffer", "Flags": 0, "NFTokenID": "000B", "Amount": "1000000"},   # a buy offer
                ], "marker": "m2"}}
            return {"result": {"account_objects": [
                {"Flags": 1, "NFTokenID": "000C", "Amount": {"currency": "USD", "issuer": "rX", "value": "12.5"}}]}}

    rows = asyncio.run(L.xrpl_listings(XHttp(), "rMe"))
    assert [r["token_id"] for r in rows] == ["000A", "000C"] and len(calls) == 2
    a, c = rows
    assert a["price"] == Decimal("25") and a["currency"] == "XRP" and a["expires_at"].startswith("2025")
    assert c["price"] == Decimal("12.5") and c["currency"] == "USD" and c["marketplace"] == "XRP Ledger"


def test_stale_solana_listing_dropped_once_holdings_known(monkeypatch):
    rows = [{"marketplace": "Tensor", "chain": Chain.SOLANA, "owner": "Me", "contract": "Gone", "token_id": "Gone",
             "price": Decimal("1"), "currency": "SOL", "quantity": 1, "expires_at": None, "url": "u", "must_hold": True},
            {"marketplace": "Tensor", "chain": Chain.SOLANA, "owner": "Me", "contract": "Kept", "token_id": "Kept",
             "price": Decimal("1"), "currency": "SOL", "quantity": 1, "expires_at": None, "url": "u", "must_hold": True}]

    async def fake_fetch(addresses):
        return rows, []
    monkeypatch.setattr(L, "fetch_all", fake_fetch)

    class P:
        addresses = [(Chain.SOLANA, "Me")]
        native_usd = {"SOL": 100}
        listings_cache = {}
    held = [{"key": "solana:nft:Kept:Kept", "nft": True, "name": "Kept #1"}]
    out = asyncio.run(L.listings_for(P(), held, None))
    assert [x["name"] for x in out["listings"]] == ["Kept #1"]
    P.listings_cache = {}
    assert asyncio.run(L.listings_for(P(), [], None))["count"] == 2   # scan still running: show both


def test_exclusivo_listings_from_held_nfts(monkeypatch):
    async def no_other(addresses):
        return [{"marketplace": "Magic Eden", "chain": Chain.SOLANA, "owner": "Me", "contract": "MintME", "token_id": "MintME",
                 "price": Decimal("3"), "currency": "SOL", "quantity": 1, "expires_at": None, "url": "me"}], \
               [{"name": "Magic Eden", "ok": True, "error": None}]
    monkeypatch.setattr(L, "fetch_all", no_other)
    asked = []

    class ExcHttp:
        def __init__(self, *a, **k): pass

        async def get(self, path, params=None):
            asked.append(path)
            if path == "/tokens/solana/CollA/MintEXC":
                return {"data": {"chain": "solana", "owner": "Me", "listing": {
                    "price": {"raw": "1250000000", "decimal": 1.25, "currency": "SOL", "usd": 284.5},
                    "marketplace": "EXC", "seller": "Me", "status": "active"}}}
            if path == "/tokens/solana/CollA/MintME":   # same listing we already have from Magic Eden
                return {"data": {"owner": "Me", "listing": {"price": {"decimal": 3, "currency": "SOL"},
                                                            "marketplace": "magic-eden", "status": "active"}}}
            if path == "/tokens/robinhood/0xbear/7":
                return {"data": {"owner": "0xME", "listing": {"price": {"decimal": 0.02, "currency": "ETH", "usd": 60},
                                                              "marketplace": "EXC", "status": "active"}}}
            if path == "/tokens/solana/CollA/MintSold":
                return {"data": {"owner": "Me", "listing": {"price": {"decimal": 9, "currency": "SOL"}, "marketplace": "EXC", "status": "sold"}}}
            raise Exception("exclusivo.one 404: not found")

    monkeypatch.setattr(L, "Http", ExcHttp)

    class P:
        addresses = [(Chain.SOLANA, "Me"), (Chain.ROBINHOOD, "0xme")]
        native_usd = {"SOL": 200, "ETH": 3000}
        listings_cache = {}

    nft = lambda key, coll, name, value=None: {"key": key, "nft": True, "chain": key.split(":")[0], "name": name,
                                               "collection": coll, "value": value, "spam": False}
    held = [nft("solana:nft:MintEXC:MintEXC", "solana:CollA", "Retardio #9", "500"),
            nft("solana:nft:MintME:MintME", "solana:CollA", "Retardio #3"),
            nft("solana:nft:MintSold:MintSold", "solana:CollA", "Retardio #5"),
            nft("solana:nft:MintNone:MintNone", "solana:CollA", "Retardio #6"),
            nft("robinhood:nft:0xbear:7", "robinhood:0xbear", "Robinhood Bear #7"),
            nft("cardano:nft:x:y", "cardano:x", "Not on Exclusivo")]
    out = asyncio.run(L.listings_for(P(), held, None))
    by = {(x["name"], x["marketplace"]) for x in out["listings"]}
    assert by == {("Retardio #3", "Magic Eden"), ("Retardio #9", "Exclusivo"), ("Robinhood Bear #7", "Exclusivo")}
    exc = next(x for x in out["listings"] if x["name"] == "Retardio #9")
    assert exc["price"] == "1.25" and exc["price_usd"] == "250.00" and exc["url"] == "https://exclusivo.one/wallet/Me"
    assert not any("cardano" in a for a in asked) and len(asked) == 5
    assert {"name": "Exclusivo", "ok": True, "error": None} in out["sources"]
    # one wallet only
    assert {x["name"] for x in asyncio.run(L.listings_for(P(), held, "0xME"))["listings"]} == {"Robinhood Bear #7"}


REAL_SOLONCHAIN = "2iAySSuGGjpnl8dmQljWzGUmI7Jk8r+jlg1RC8S7zCkrpXmedF503gn4UglWsDH7j4c95Z8VW9DDh0RiMjvQAnnu1PPCmT/3AMLrCwAAAADOBfFqAAAAAAAB/w=="


def test_parse_real_solonchain_listing():
    import base64
    l = L.parse_solonchain(base64.b64decode(REAL_SOLONCHAIN))   # BullBears #8817, listed at 0.2 SOL
    assert l == {"seller": "7yPEAJEHGqJyRCTv2YUqY4WX6zCXzpBgfWM67joXJWmB",
                 "asset": "fvHYF1RtgCoeCAxNrbvTE3tcFsdXrGBacTTGJx96Sza", "lamports": 200_000_000, "listed_at": 1794180558}
    assert L.parse_solonchain(b"\0" * 90) is None


def test_solonchain_listings_by_seller():
    seller = "7yPEAJEHGqJyRCTv2YUqY4WX6zCXzpBgfWM67joXJWmB"
    sent = []

    class Rpc:
        async def post(self, path, params=None, json=None):
            sent.append(json)
            return {"result": [{"pubkey": "2T9T14KGVHxLX2n9VEH4ECDMjwc2LujPebpSvv8rEAif",
                                "account": {"data": [REAL_SOLONCHAIN, "base64"]}}]}

    class Scanner:
        rpc, rpc_params = Rpc(), {"api-key": "k"}
        _meta = {}

        async def _resolve_mints(self, mints):
            for m in mints:
                self._meta[m] = {"name": "BullBears #8817", "image": "https://cdn.helius/bb.png"}

    (row,) = asyncio.run(L.solonchain_listings(Scanner(), seller))
    flt = sent[0]["params"][1]["filters"]
    assert sent[0]["method"] == "getProgramAccounts" and sent[0]["params"][0] == L.SOLONCHAIN_PROGRAM
    assert {"dataSize": 91} in flt and {"memcmp": {"offset": 8, "bytes": seller}} in flt
    assert row["marketplace"] == "SolOnChain" and row["price"] == Decimal("0.2") and row["name"] == "BullBears #8817"
    assert row["url"] == "https://www.solonchain.io/marketplace/fvHYF1RtgCoeCAxNrbvTE3tcFsdXrGBacTTGJx96Sza"
    assert not row.get("must_hold")   # escrowed: the NFT isn't in the wallet while listed
