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
