"""Shared scanner plumbing: HTTP client with retries and rate-limit backoff."""
from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator, Protocol

import httpx

from core.models import NormalizedTx


class Scanner(Protocol):
    async def scan(self, address: str, since_cursor: str | None = None) -> AsyncIterator[NormalizedTx]:
        """Yield transactions newest-first. since_cursor is the newest tx hash
        already stored; scanning stops when it is reached (incremental sync)."""
        ...


class ProviderError(RuntimeError):
    pass


class Http:
    def __init__(self, base_url: str, rate_per_sec: float = 5.0, headers: dict | None = None):
        self.client = httpx.AsyncClient(base_url=base_url, timeout=30, headers={
            "User-Agent": "ChainScope/0.1", **(headers or {})})
        self._min_interval = 1.0 / rate_per_sec
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._last + self._min_interval - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = loop.time()

    async def request(self, method: str, url: str, **kw) -> Any:
        for attempt in range(6):
            await self._throttle()
            try:
                r = await self.client.request(method, url, **kw)
            except httpx.TransportError:
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = float(r.headers.get("retry-after", 2 ** attempt))
                await asyncio.sleep(min(retry_after, 60))
                continue
            if r.status_code >= 400:
                raise ProviderError(f"{r.request.url.host} {r.status_code}: {r.text[:200]}")
            return r.json()
        raise ProviderError(f"{url}: gave up after retries")

    async def get(self, url: str, **kw) -> Any:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw) -> Any:
        return await self.request("POST", url, **kw)

    async def aclose(self) -> None:
        await self.client.aclose()


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise ProviderError(f"Missing environment variable {name}")
    return v
