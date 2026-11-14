"""Admin settings that survive restarts and deploys.

Only the panel's own switches are saved: rate limits, maintenance mode,
networks turned off, and blocked visitors (by scrambled ID only). No scans,
addresses or wallet data. They live in the Render Key Value store (CACHE_URL);
without it, settings work as before and reset on restart.
"""
from __future__ import annotations

import json
import logging
import time

from core import monitor

log = logging.getLogger("chainscope.settings")
KEY = "chainscope:admin-settings"


def _client(prices):
    cache = getattr(prices, "cache", None)
    return cache.client if cache is not None else None


def available(prices) -> bool:
    return _client(prices) is not None


def snapshot() -> dict:
    from api import limits
    return {
        "limits": {k: [r.per_minute, r.burst] for k, r in limits.RULES.items() if k != "admin_auth"},
        "maintenance": dict(monitor.maintenance),
        "disabled_networks": sorted(monitor.disabled_networks),
        "blocked": {k: v for k, v in monitor.blocked.items() if v["until"] > time.time()},
    }


def apply(data: dict) -> None:
    from api import limits
    from core.models import Chain
    for k, (per_minute, burst) in (data.get("limits") or {}).items():
        if k in limits.RULES and k != "admin_auth" and per_minute > 0 and burst >= 1:
            limits.RULES[k] = limits.Rule(float(per_minute), int(burst))
    m = data.get("maintenance") or {}
    if "on" in m:
        monitor.maintenance["on"] = bool(m["on"])
    if m.get("message"):
        monitor.maintenance["message"] = str(m["message"])[:300]
    valid = {c.value for c in Chain}
    monitor.disabled_networks.clear()
    monitor.disabled_networks.update(n for n in data.get("disabled_networks") or [] if n in valid)
    now = time.time()
    monitor.blocked.clear()
    monitor.blocked.update({k: v for k, v in (data.get("blocked") or {}).items() if v.get("until", 0) > now})


async def save(prices) -> bool:
    c = _client(prices)
    if c is None:
        return False
    try:
        await c.set(KEY, json.dumps(snapshot(), separators=(",", ":")))
        return True
    except Exception as e:
        log.warning("admin settings not saved: %s", str(e)[:200])
        monitor.record("admin", "Settings could not be saved to Key Value; they'll reset on restart")
        return False


async def load(prices) -> bool:
    c = _client(prices)
    if c is None:
        return False
    try:
        raw = await c.get(KEY)
    except Exception as e:
        log.warning("admin settings not loaded: %s", str(e)[:200])
        return False
    if not raw:
        return False
    try:
        apply(json.loads(raw))
    except Exception as e:
        log.warning("admin settings ignored (bad data): %s", str(e)[:200])
        return False
    monitor.record("admin", "Loaded saved admin settings")
    return True
