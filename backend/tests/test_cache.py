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
from pricing.cache import Cache  # noqa: E402
from pricing.coingecko import PriceService  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, k):
        return self.data.get(k)

    async def set(self, k, v, ex=None):
        self.data[k] = v

    async def mget(self, keys):
        return [self.data.get(k) for k in keys]


class FakeCG:
    def __init__(self):
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append(url)
        now = datetime.now(timezone.utc)
        if url == "/coins/list":
            return [{"id": "bonk", "platforms": {"solana": "DezX"}}]
        if url.endswith("/market_chart/range"):
            return {"prices": [[(now - timedelta(days=d)).timestamp() * 1000, 2.0] for d in range(10, -1, -1)]}
        if url == "/simple/price":
            return {"solana": {"usd": 150.0}, "bonk": {"usd": 0.00002}}
        raise AssertionError(url)


def _scan(svc):
    sol = Asset(Chain.SOLANA, "SOL", decimals=9)
    bonk = Asset(Chain.SOLANA, "BONK", contract="DezX", decimals=5)
    tx = NormalizedTx(Chain.SOLANA, "s", datetime.now(timezone.utc) - timedelta(days=3), "w",
                      [Leg(sol, D(-1)), Leg(bonk, D(100000))])
    asyncio.run(svc.price_transactions([tx]))
    cur = asyncio.run(svc.current([sol, bonk]))
    return tx, cur


def test_second_server_reuses_shared_cache():
    shared = FakeRedis()
    first_cg = FakeCG()
    tx1, cur1 = _scan(PriceService(http=first_cg, cache=Cache(client=shared)))
    assert first_cg.calls   # first scan downloads

    # A fresh server (empty memory, e.g. after sleeping) with the same store
    again = FakeCG()
    tx2, cur2 = _scan(PriceService(http=again, cache=Cache(client=shared)))
    assert again.calls == []                                    # nothing re-downloaded
    assert [l.usd_value for l in tx2.legs] == [l.usd_value for l in tx1.legs]
    assert cur1 and cur2 == cur1


def test_no_cache_configured_is_harmless():
    c = Cache(url="")
    assert asyncio.run(c.get("x")) is None
    asyncio.run(c.set("x", 1, 10))
    assert asyncio.run(c.mget(["a", "b"])) == [None, None]


def test_unreachable_store_is_skipped_after_one_failure():
    class Down:
        calls = 0

        async def get(self, k):
            Down.calls += 1
            raise ConnectionError("no route")

    c = Cache(client=Down())
    assert asyncio.run(c.get("a")) is None and asyncio.run(c.get("b")) is None
    assert Down.calls == 1
