"""Rate limits, so one heavy user or a bot can't use up the shared free API
allowances (CoinGecko, Koios, BlockCypher…) for everyone.

Who is "one user":
  • a partner app sending a key from API_CLIENT_KEYS ("name:key,name2:key2")
    in the X-API-Key header: limited per key, with higher limits;
  • a visitor of the ChainScope website: the website's proxy passes their IP
    in X-ChainScope-Client, trusted only alongside X-ChainScope-Proxy-Key
    matching PROXY_SHARED_SECRET (so nobody else can pretend to be the site);
  • anyone else: the IP Render's edge saw (the last X-Forwarded-For entry,
    which a caller can't forge), or the socket address.

Limits are token buckets kept in memory (one server). Starting a scan costs
the most upstream calls, so it has its own, much smaller bucket.
"""
from __future__ import annotations

import hmac
import math
import os
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Rule:
    per_minute: float   # steady refill rate
    burst: int          # how many can be used at once


RULES = {
    "request": Rule(_env_int("RATE_REQUESTS_PER_MIN", 240), 120),      # any API call (status polling included)
    "scan": Rule(_env_int("RATE_SCANS_PER_HOUR", 12) / 60, 6),         # new scans and refreshes
    "resolve": Rule(_env_int("RATE_RESOLVES_PER_MIN", 30), 30),        # name.eth / $handle lookups
    "demo": Rule(30, 10),
    "admin_auth": Rule(5, 10),                                         # wrong admin passwords
}
PARTNER_MULTIPLIER = _env_int("RATE_PARTNER_MULTIPLIER", 20)            # API-key holders get more


def _partner_keys() -> dict[str, str]:
    out = {}
    for item in (os.environ.get("API_CLIENT_KEYS") or "").split(","):
        name, _, key = item.strip().partition(":")
        if name and key:
            out[key] = name
    return out


def client_of(req: Request) -> tuple[str, bool]:
    """(identity, is_partner) for rate limiting."""
    key = req.headers.get("x-api-key")
    if key:
        for k, name in _partner_keys().items():
            if hmac.compare_digest(k, key):
                return f"key:{name}", True
    secret = os.environ.get("PROXY_SHARED_SECRET", "")
    sent = req.headers.get("x-chainscope-proxy-key", "")
    visitor = req.headers.get("x-chainscope-client", "").strip()
    if secret and visitor and hmac.compare_digest(secret, sent):
        return f"ip:{visitor}", False
    xff = [p.strip() for p in (req.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    if xff:
        return f"ip:{xff[-1]}", False   # added by Render's edge; earlier entries can be forged
    return f"ip:{req.client.host if req.client else 'unknown'}", False


@dataclass
class Bucket:
    tokens: float
    at: float = field(default_factory=time.monotonic)


_buckets: dict[str, Bucket] = {}
_last_sweep = time.monotonic()


def take(req: Request, kind: str) -> None:
    """Spend one token of `kind` for this caller, or raise 429 with Retry-After."""
    global _last_sweep
    rule = RULES[kind]
    who, partner = client_of(req)
    mult = PARTNER_MULTIPLIER if partner else 1
    rate, cap = rule.per_minute * mult / 60.0, rule.burst * mult      # tokens per second, max tokens
    now = time.monotonic()
    b = _buckets.get(f"{kind}|{who}")
    if b is None:
        b = _buckets[f"{kind}|{who}"] = Bucket(cap, now)
    b.tokens = min(cap, b.tokens + (now - b.at) * rate)
    b.at = now
    if b.tokens < 1:
        from core import monitor
        monitor.rate_limited(kind)
        wait = max(1, math.ceil((1 - b.tokens) / rate))
        raise HTTPException(429, _message(kind, wait), headers={"Retry-After": str(wait)})
    b.tokens -= 1
    if now - _last_sweep > 600:   # forget idle callers now and then
        _last_sweep = now
        for k in [k for k, v in _buckets.items() if now - v.at > 3600]:
            del _buckets[k]


def _message(kind: str, wait: int) -> str:
    when = f"{wait} seconds" if wait < 90 else f"{math.ceil(wait / 60)} minutes"
    if kind == "scan":
        return f"You've started a lot of scans in a short time. You can start another in about {when}."
    return f"Slow down a little: too many requests in a short time. Try again in about {when}."


def reset() -> None:   # for tests
    _buckets.clear()
