"""Admin upgrades: health history, network switches, saved settings, usage, blocks."""
import asyncio
import json
import time
import types
from collections import Counter, deque

import pytest
from fastapi import HTTPException

from api import admin, limits
from core import monitor, settings

TOKEN = "correct-horse-battery-staple"


class Req:
    def __init__(self, token=TOKEN, ip="1.2.3.4"):
        self.headers = {"x-forwarded-for": ip, "x-admin-token": token}
        self.client = types.SimpleNamespace(host=ip)


class B:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def set(self, k, v, ex=None):
        self.data[k] = v

    async def get(self, k):
        return self.data.get(k)


def run(c):
    return asyncio.run(c)


@pytest.fixture
def env(monkeypatch):
    from portfolio.service import Store
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    limits.reset()
    redis = FakeRedis()
    prices = types.SimpleNamespace(cache=types.SimpleNamespace(client=redis))
    d = lambda *a, **k: (lambda f: f)
    r = admin.register(types.SimpleNamespace(get=d, post=d, put=d, delete=d), Store(), prices)
    yield r, redis, prices
    monitor.disabled_networks.clear(); monitor.blocked.clear(); monitor.visitors.clear()
    monitor.maintenance["on"] = False
    for k, v in limits.DEFAULTS.items():
        limits.RULES[k] = limits.Rule(v.per_minute, v.burst)
    limits.reset()


def test_health_history_counts_uptime_and_outages(monkeypatch):
    monitor.health_history.clear()
    seq = [True, True, False, False, True, False, True]
    for i, ok in enumerate(seq):
        monkeypatch.setattr(time, "time", lambda i=i: 1_000_000 + i * 300)
        monitor.health_ran([{"name": "Koios", "ok": ok, "ms": 50}])
    (s,) = monitor.health_summary()
    assert s["outages"] == 2 and s["checks"] == 7 and s["uptime"] == round(100 * 4 / 7, 1)
    assert s["recent"] == [1, 1, 0, 0, 1, 0, 1] and s["ok"] is True


def test_turned_off_network_is_skipped_with_a_note(monkeypatch):
    from datetime import datetime, timezone
    from decimal import Decimal as D
    from core.models import Asset, Chain, Leg, NormalizedTx
    from portfolio import service
    from portfolio.pnl import Method

    class Good:
        async def scan(self, address, since_cursor=None):
            yield NormalizedTx(Chain.ETHEREUM, "0xok", datetime.now(timezone.utc), address,
                               [Leg(Asset(Chain.ETHEREUM, "ETH"), D(1))])

    class Empty:
        async def scan(self, address, since_cursor=None):
            return
            yield  # pragma: no cover

    class NoPrices:
        async def price_transactions(self, txs): pass
        async def current(self, assets): return {}

    called = []
    monkeypatch.setattr(service, "_scanner", lambda c: called.append(c) or (Good() if c == Chain.ETHEREUM else Empty()))
    monitor.disabled_networks.add("polygon")
    try:
        p = service.Store().create(["0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"], Method.FIFO)
        asyncio.run(service.run_scan(p, NoPrices()))
    finally:
        monitor.disabled_networks.clear()
    assert p.status == "ready" and Chain.POLYGON not in called
    (note,) = [v for k, v in p.network_errors.items() if k.startswith("polygon:")]
    assert "Polygon is turned off" in note
    assert "ethereum" in monitor.net_times


def test_settings_are_saved_and_restored(env):
    r, redis, prices = env
    run(r["set_network"](B(network="cardano", on=False), Req()))
    run(r["set_limits"](B(kind="scan", per_minute=0.5, burst=3), Req()))
    run(r["set_maintenance"](B(on=True, message="Back soon"), Req()))
    saved = json.loads(redis.data[settings.KEY])
    assert saved["disabled_networks"] == ["cardano"] and saved["limits"]["scan"] == [0.5, 3]
    blob = json.dumps(saved)
    assert "1.2.3.4" not in blob and TOKEN not in blob
    # simulate a restart
    monitor.disabled_networks.clear(); monitor.maintenance["on"] = False
    limits.RULES["scan"] = limits.DEFAULTS["scan"]
    run(settings.load(prices))
    assert monitor.disabled_networks == {"cardano"} and monitor.maintenance["on"] is True
    assert limits.RULES["scan"].burst == 3
    run(r["reset_limits"](Req()))
    assert limits.RULES["scan"].burst == limits.DEFAULTS["scan"].burst


def test_unknown_network_rejected(env):
    r, _, _ = env
    with pytest.raises(HTTPException) as e:
        run(r["set_network"](B(network="tron", on=False), Req()))
    assert e.value.status_code == 400


def test_visitors_are_scrambled_and_can_be_blocked(env):
    r, redis, _ = env
    limits.take(Req(ip="9.9.9.9"), "request")
    limits.take(Req(ip="9.9.9.9"), "scan")
    out = run(r["visitors"](Req()))
    blob = json.dumps(out)
    assert "9.9.9.9" not in blob
    (v,) = [x for x in out["visitors"] if x["scans"] == 1]
    assert v["id"].startswith("v-") and v["requests"] == 1
    run(r["block"](B(id=v["id"], hours=1), Req()))
    assert monitor.is_blocked(v["id"]) and v["id"] in json.loads(redis.data[settings.KEY])["blocked"]
    assert run(r["visitors"](Req()))["blocks"][0]["id"] == v["id"]
    run(r["unblock"](v["id"], Req()))
    assert not monitor.is_blocked(v["id"])


def test_cannot_block_yourself(env):
    r, _, _ = env
    me = limits.visitor_of(Req())[0]
    with pytest.raises(HTTPException) as e:
        run(r["block"](B(id=me, hours=1), Req()))
    assert e.value.status_code == 400


def test_blocks_expire():
    monitor.blocked["v-0123456789"] = {"until": time.time() - 1, "label": "Visitor"}
    assert not monitor.is_blocked("v-0123456789") and "v-0123456789" not in monitor.blocked


def test_usage_has_24_hours_and_network_times():
    monitor.hourly.clear()
    monitor.scan_begun("x", ["solana"]); monitor.scan_ended("x", True, txs=3)
    monitor.network_timed("solana", 4.0, True); monitor.network_timed("solana", 2.0, False)
    u = monitor.usage()
    assert len(u["hours"]) == 24 and u["hours"][-1]["started"] == 1 and u["hours"][-1]["finished"] == 1
    sol = next(n for n in u["networks"] if n["network"] == "solana")
    assert sol["slowest_seconds"] >= 4.0 and sol["errors"] >= 1


def test_settings_without_key_value_store_are_a_no_op():
    assert run(settings.save(types.SimpleNamespace())) is False
    assert run(settings.load(types.SimpleNamespace(cache=types.SimpleNamespace(client=None)))) is False
