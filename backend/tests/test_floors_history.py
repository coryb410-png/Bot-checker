import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

if "httpx" not in sys.modules:
    try:
        import httpx  # noqa: F401
    except ImportError:
        stub = types.ModuleType("httpx"); stub.AsyncClient = object; stub.TransportError = Exception
        sys.modules["httpx"] = stub

from core.models import Asset, Chain, Leg, NormalizedTx  # noqa: E402
from pricing.floors import FloorService, collection_key  # noqa: E402
from scanners.base import ProviderError  # noqa: E402


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def get(self, url, params=None):
        self.calls.append(url)
        r = self.routes.get(url)
        if r is None:
            raise ProviderError("404")
        return r


def test_solana_floor_one_lookup_per_collection():
    me = FakeHttp({
        "/tokens/mintA": {"collection": "wegen", "collectionName": "Wegen"},
        "/collections/wegen/stats": {"symbol": "wegen", "floorPrice": 250_000_000},
    })
    fs = FloorService(me=me, alchemy_nft=FakeHttp({}))
    a = Asset(Chain.SOLANA, "Wegen", contract="mintA", decimals=0, is_nft=True, token_id="mintA", collection="CollW")
    b = Asset(Chain.SOLANA, "Wegen", contract="mintB", decimals=0, is_nft=True, token_id="mintB", collection="CollW")
    lone = Asset(Chain.SOLANA, "X", contract="mintZ", decimals=0, is_nft=True, token_id="mintZ")  # not on ME
    floors = asyncio.run(fs.floors_for([a, b, lone]))
    f = floors[collection_key(a)]
    assert (f.amount, f.currency, f.source) == (D("0.25"), "SOL", "Magic Eden")
    assert collection_key(a) == collection_key(b) and collection_key(lone) not in floors
    assert me.calls.count("/collections/wegen/stats") == 1          # shared by both NFTs
    asyncio.run(fs.floors_for([a]))
    assert me.calls.count("/collections/wegen/stats") == 1          # cached


def test_ethereum_floor_prefers_opensea():
    alc = FakeHttp({"/getFloorPrice": {"openSea": {"floorPrice": 0.42, "priceCurrency": "ETH", "collectionUrl": "https://opensea.io/x"},
                                       "looksRare": {"floorPrice": 0.4, "priceCurrency": "ETH"}}})
    fs = FloorService(me=FakeHttp({}), alchemy_nft=alc)
    a = Asset(Chain.ETHEREUM, "BANDIT", contract="0xabc", decimals=0, is_nft=True, token_id="7", collection="0xabc")
    f = asyncio.run(fs.floors_for([a]))[collection_key(a)]
    assert (f.amount, f.source) == (D("0.42"), "OpenSea")


def test_floor_values_nfts_in_portfolio(monkeypatch):
    from portfolio import service
    from portfolio.pnl import Method
    from pricing.floors import Floor

    sol = Asset(Chain.SOLANA, "SOL", decimals=9)
    nft = Asset(Chain.SOLANA, "Wegen", contract="m1", decimals=0, is_nft=True, token_id="m1", collection="CollW")
    p = service.Store().create(["JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"], Method.FIFO)
    now = datetime.now(timezone.utc)
    p.txs = [NormalizedTx(Chain.SOLANA, "buy", now, "w", [Leg(sol, D(-1), usd_value=D(150)), Leg(nft, D(1), usd_value=D(150))],
                          D("0.00001"), sol)]
    service.recompute(p, {})

    class FakeFloors:
        async def floors_for(self, nfts):
            return {collection_key(nft): Floor(D(2), "SOL", "Magic Eden")}

    monkeypatch.setattr(service, "_floor_service", FakeFloors())
    prices = asyncio.run(service._value_nfts(p, {service.NATIVE_SOL.key: D(100)}))
    assert prices == {nft.key: D(200)}
    service.recompute(p, prices)
    assert p.report.assets[nft.key].unrealized == D(50)    # floor $200 vs $150 paid


class FakePrices:
    history_days = 364

    def __init__(self, series):
        self.series = series   # symbol -> price

    async def history_for(self, a, start):
        return a.symbol if a.symbol in self.series else None

    def price_on(self, cid, a, when):
        return self.series.get(cid) if cid else (D(1) if a.symbol == "USDC" else None)


def test_value_history_replays_balances():
    from portfolio.history import value_history
    from portfolio.pnl import Method
    from portfolio.service import Store

    sol = Asset(Chain.SOLANA, "SOL", decimals=9)
    usdc = Asset(Chain.SOLANA, "USDC", contract="EPj", decimals=6)
    nft = Asset(Chain.SOLANA, "N", contract="n", decimals=0, is_nft=True, token_id="n")
    now = datetime.now(timezone.utc)
    p = Store().create(["JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"], Method.FIFO)
    p.txs = [
        NormalizedTx(Chain.SOLANA, "in", now - timedelta(days=5), "w", [Leg(sol, D(10))]),
        NormalizedTx(Chain.SOLANA, "sell", now - timedelta(days=2), "w", [Leg(sol, D(-4)), Leg(usdc, D(400))], D(1), sol),
        NormalizedTx(Chain.SOLANA, "nft", now - timedelta(days=1), "w", [Leg(sol, D(-1)), Leg(nft, D(1))]),
    ]
    pts = asyncio.run(value_history(p, FakePrices({"SOL": D(100)})))
    vals = {pt["date"]: D(pt["value"]) for pt in pts}
    day = lambda n: (now - timedelta(days=n)).date().isoformat()
    assert vals[day(5)] == D(1000)                  # 10 SOL
    assert vals[day(2)] == D(5 * 100 + 400)         # 10-4-1 fee = 5 SOL + 400 USDC
    assert vals[day(0)] == D(4 * 100 + 400)         # bought an NFT with 1 SOL; NFTs not in the line
    assert len(pts) == 6
