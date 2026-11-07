"""Shared cache for price data, kept in Render Key Value (Redis-compatible).

The free server forgets everything when it sleeps or restarts, and the free
CoinGecko plan allows only about one request every two seconds, so the same
coin's price history used to be downloaded again on nearly every scan. This
keeps it in a separate always-on store that every scan, for every wallet,
reads first.

Optional: with no CACHE_URL (or if the store is unreachable) every call is a
no-op and pricing works exactly as before.
"""
from __future__ import annotations

import json
import logging
import time

from scanners.base import env

log = logging.getLogger("chainscope.cache")


class Cache:
    def __init__(self, url: str | None = None, client=None):
        self._client = client
        self._url = url if url is not None else env("CACHE_URL", "")
        self._broken = False
        self._down_until = 0.0   # after a failure, skip the store for a while

    def _fail(self, what: str, e: Exception) -> None:
        log.warning("cache %s failed, skipping it for 5 minutes: %s", what, str(e)[:200])
        self._down_until = time.time() + 300

    @property
    def client(self):
        if time.time() < self._down_until:
            return None
        if self._client is None and self._url and not self._broken:
            try:
                import redis.asyncio as redis
                self._client = redis.from_url(self._url, socket_timeout=3, socket_connect_timeout=3)
            except Exception as e:
                log.warning("cache unavailable: %s", str(e)[:200])
                self._broken = True
        return self._client

    async def get(self, key: str):
        c = self.client
        if c is None:
            return None
        try:
            raw = await c.get(key)
        except Exception as e:
            self._fail("read", e)
            return None
        return json.loads(raw) if raw else None

    async def set(self, key: str, value, ttl: int) -> None:
        c = self.client
        if c is None:
            return
        try:
            await c.set(key, json.dumps(value, separators=(",", ":")), ex=ttl)
        except Exception as e:
            self._fail("write", e)

    async def mget(self, keys: list[str]) -> list:
        c = self.client
        if c is None or not keys:
            return [None] * len(keys)
        try:
            raws = await c.mget(keys)
        except Exception as e:
            self._fail("read", e)
            return [None] * len(keys)
        return [json.loads(r) if r else None for r in raws]
