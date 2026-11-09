import asyncio
import sys
import types

if "httpx" not in sys.modules:
    try:
        import httpx  # noqa: F401
    except ImportError:
        stub = types.ModuleType("httpx"); stub.AsyncClient = object; stub.TransportError = Exception
        sys.modules["httpx"] = stub

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api import admin, limits  # noqa: E402
from core import monitor  # noqa: E402

TOKEN = "correct-horse-battery-staple"


class Req:
    def __init__(self, token=None, ip="1.2.3.4"):
        self.headers = {"x-forwarded-for": ip}
        if token is not None:
            self.headers["x-admin-token"] = token
        self.client = types.SimpleNamespace(host=ip)


class Body:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
def routes(monkeypatch):
    from portfolio.demo import build_demo
    from portfolio.service import Store
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    limits.reset()
    store = Store()
    store.add(build_demo("demo-1"))
    r = admin.register(types.SimpleNamespace(get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
                                             put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f)),
                       store, types.SimpleNamespace())
    r["store"] = store
    yield r
    monitor.maintenance["on"] = False
    limits.reset()


def run(coro):
    return asyncio.run(coro)


def test_locked_without_or_with_wrong_password(routes, monkeypatch):
    with pytest.raises(HTTPException) as e:
        run(routes["overview"](Req("nope")))
    assert e.value.status_code == 401
    monkeypatch.setenv("ADMIN_TOKEN", "short")
    with pytest.raises(HTTPException) as e:
        run(routes["overview"](Req(TOKEN)))
    assert e.value.status_code == 503   # panel off until a proper password is set


def test_wrong_passwords_get_slowed_down(routes):
    for _ in range(limits.RULES["admin_auth"].burst):
        with pytest.raises(HTTPException):
            run(routes["overview"](Req("guess", ip="9.9.9.9")))
    with pytest.raises(HTTPException) as e:
        run(routes["overview"](Req("guess", ip="9.9.9.9")))
    assert e.value.status_code == 429


def test_overview_and_scans_never_show_addresses(routes, monkeypatch):
    monkeypatch.setenv("ALCHEMY_API_KEY", "secret-value-123")
    o = run(routes["overview"](Req(TOKEN)))
    assert o["scans"]["in_memory"] == 1
    keys = {k["name"]: k["set"] for k in o["keys"]}
    assert keys["ALCHEMY_API_KEY"] is True and "secret-value-123" not in repr(o)
    rows = run(routes["scans"](Req(TOKEN)))
    p = routes["store"].get("demo-1")
    text = repr(rows)
    assert rows[0]["addresses"] >= 1 and all(a not in text for _, a in p.addresses)


def test_errors_are_scrubbed_of_addresses():
    msg = monitor.scrub("Alchemy 403 for 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 at https://x/v2/abc?api-key=SECRET and 7yPEAJEHGqJyRCTv2YUqY4WX6zCXzpBgfWM67joXJWmB")
    assert "0xd8dA" not in msg and "SECRET" not in msg and "7yPEAJ" not in msg


def test_maintenance_limits_partner_keys_and_delete(routes, monkeypatch):
    m = run(routes["set_maintenance"](Body(on=True, message="Back soon"), Req(TOKEN)))
    assert m == {"on": True, "message": "Back soon"}
    run(routes["set_limits"](Body(kind="scan", per_minute=1.0, burst=2), Req(TOKEN)))
    assert (limits.RULES["scan"].per_minute, limits.RULES["scan"].burst) == (1.0, 2)
    monkeypatch.setenv("API_CLIENT_KEYS", "old:k1")
    k = run(routes["partner_key"](Body(name="wallet-app"), Req(TOKEN)))
    assert k["key"].startswith("cs_") and k["api_client_keys"] == f"old:k1,wallet-app:{k['key']}"
    assert run(routes["delete_all"](Req(TOKEN))) == {"deleted": 1}
    kinds = [e["kind"] for e in run(routes["events"](Req(TOKEN)))]
    assert "admin" in kinds and all(k["key"] not in e["message"] for e in monitor.events)
