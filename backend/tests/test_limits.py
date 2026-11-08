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

from api import limits  # noqa: E402


class Req:
    def __init__(self, headers=None, host="10.0.0.1"):
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.client = types.SimpleNamespace(host=host)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    limits.reset()
    monkeypatch.setenv("PROXY_SHARED_SECRET", "s3cret")
    monkeypatch.setenv("API_CLIENT_KEYS", "wallet:abc123")
    yield
    limits.reset()


def test_identity_rules():
    # The site's proxy is trusted only with the shared secret
    assert limits.client_of(Req({"x-chainscope-client": "1.2.3.4", "x-chainscope-proxy-key": "s3cret"})) == ("ip:1.2.3.4", False)
    # Without it, a forged client header is ignored and Render's last X-Forwarded-For hop is used
    assert limits.client_of(Req({"x-chainscope-client": "9.9.9.9", "x-forwarded-for": "6.6.6.6, 5.5.5.5"})) == ("ip:5.5.5.5", False)
    assert limits.client_of(Req({"x-api-key": "abc123"})) == ("key:wallet", True)
    assert limits.client_of(Req({"x-api-key": "wrong"}, host="7.7.7.7")) == ("ip:7.7.7.7", False)


def test_scans_are_limited_per_person_with_retry_after():
    a = Req({"x-forwarded-for": "1.1.1.1"})
    b = Req({"x-forwarded-for": "2.2.2.2"})
    for _ in range(limits.RULES["scan"].burst):
        limits.take(a, "scan")
    with pytest.raises(HTTPException) as e:
        limits.take(a, "scan")
    assert e.value.status_code == 429 and int(e.value.headers["Retry-After"]) > 0
    assert "scans" in e.value.detail
    limits.take(b, "scan")   # someone else is unaffected


def test_partner_keys_get_more_room():
    p = Req({"x-api-key": "abc123"})
    for _ in range(limits.RULES["scan"].burst * limits.PARTNER_MULTIPLIER):
        limits.take(p, "scan")
    with pytest.raises(HTTPException):
        limits.take(p, "scan")
