"""Historical and current USD prices from CoinGecko.

Docs: https://docs.coingecko.com
Free "demo" keys only return about 365 days of history; full all-time history
needs a paid key (set COINGECKO_PLAN=pro). Prices are cached per asset/day,
and in production that cache lives in the historical_prices table.
"""
from __future__ import annotations

import bisect
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from classifiers.classify import STABLECOINS
from classifiers.spam import looks_like_scam
from core.models import Asset, Chain, NormalizedTx
from scanners.base import Http, ProviderError, env

from .cache import Cache

PLATFORM = {Chain.ETHEREUM: "ethereum", Chain.SOLANA: "solana", Chain.CARDANO: "cardano", Chain.XRP: "xrp"}
NATIVE_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "ADA": "cardano", "XRP": "ripple"}
log = logging.getLogger("chainscope.pricing")
SERIES_TTL = 6 * 3600
DIRECTORY_TTL = 86400
CURRENT_TTL = 120


def _xrp_keys(contract: str) -> list[str]:
    """XRP Ledger tokens are "currency.issuer"; listings write the currency as
    text or as 40-hex, joined by "." or "-". Try each form."""
    cur, _, issuer = contract.partition(".")
    if len(cur) == 40:
        text = bytes.fromhex(cur).rstrip(b"\x00").decode("utf-8", "ignore")
        hexed = cur
    else:
        text, hexed = cur, cur.encode().hex().upper().ljust(40, "0")
    forms = [f"{c}{sep}{issuer}" for c in (hexed, text) for sep in (".", "-")]
    return [f.lower() for f in forms] + [issuer.lower()]


class PriceService:
    def __init__(self, http: Http | None = None, cache: Cache | None = None):
        pro = env("COINGECKO_PLAN", "demo") == "pro"
        key = env("COINGECKO_API_KEY", "")
        base = "https://pro-api.coingecko.com/api/v3" if pro else "https://api.coingecko.com/api/v3"
        header = {"x-cg-pro-api-key" if pro else "x-cg-demo-api-key": key} if key else {}
        self.http = http or Http(base, rate_per_sec=0.45 if not pro else 8, headers=header)
        # Free (demo/public) access only serves the last 365 days of history.
        # Older transactions keep an unknown USD value instead of failing the scan.
        self.history_days: int | None = None if pro else 364
        self._ids: dict[str, str | None] = {}
        # contract address -> CoinGecko id, for every listed coin, from ONE call
        self._directory: dict[tuple[str, str], str] = {}
        self._directory_at = 0.0
        self._series: dict[str, tuple[list[int], list[Decimal]]] = {}
        self._covered: dict[str, tuple[float, float]] = {}   # cid -> (from ts, fetched at)
        self.cache = cache or Cache()   # shared across restarts and scans

    async def _load_directory(self) -> None:
        """Download CoinGecko's whole coin list with contract addresses once a
        day. Replaces one lookup call per token with a single call."""
        if self._directory and time.time() - self._directory_at < DIRECTORY_TTL:
            return
        cached = await self.cache.get("cg:dir:v1")
        if cached and time.time() - cached["at"] < DIRECTORY_TTL:
            self._directory = {(pf, k): cid for pf, k, cid in cached["rows"]}
            self._directory_at = cached["at"]
            return
        try:
            coins = await self.http.get("/coins/list", params={"include_platform": "true"})
        except ProviderError as e:
            log.warning("coin directory unavailable, falling back to per-token lookups: %s", str(e)[:200])
            return
        wanted = set(PLATFORM.values())
        directory = {}
        for c in coins or []:
            for platform, address in (c.get("platforms") or {}).items():
                if platform in wanted and address:
                    key = address if platform == "solana" else address.lower()   # cardano: policy id + asset name hex
                    directory[(platform, key)] = c["id"]
        if directory:
            self._directory, self._directory_at = directory, time.time()
            await self.cache.set("cg:dir:v1", {"at": self._directory_at,
                                               "rows": [[pf, k, cid] for (pf, k), cid in directory.items()]},
                                 DIRECTORY_TTL)

    async def coin_id(self, a: Asset) -> str | None:
        if a.coingecko_id:
            return a.coingecko_id
        if a.contract is None:
            return NATIVE_IDS.get(a.symbol.upper())
        if a.is_nft or a.chain not in PLATFORM or a.spam:
            return None
        if looks_like_scam(a.symbol) or looks_like_scam(a.name):
            return None   # junk airdrops are never priced
        if self._directory:
            platform = PLATFORM[a.chain]
            if a.chain == Chain.XRP:
                return next((self._directory[(platform, k)] for k in _xrp_keys(a.contract)
                             if (platform, k) in self._directory), None)
            key = a.contract if a.chain == Chain.SOLANA else a.contract.lower()
            return self._directory.get((platform, key))   # not listed = no market price
        if a.key not in self._ids:
            try:
                d = await self.http.get(f"/coins/{PLATFORM[a.chain]}/contract/{a.contract}")
                self._ids[a.key] = d["id"]
            except ProviderError:
                self._ids[a.key] = None   # unlisted token: value stays unknown
        return self._ids[a.key]

    async def _load(self, cid: str, start: datetime, end: datetime | None = None) -> None:
        """Daily-ish USD prices for cid from start through today. Cached and
        shared by every scan; refetched after 6 hours or when an older start
        is needed. (Earlier versions cached the first scan's range forever.)"""
        now = datetime.now(timezone.utc)
        frm, to = start - timedelta(days=1), now
        if self.history_days is not None:
            frm = max(frm, now - timedelta(days=self.history_days))
        cov = self._covered.get(cid)
        if cov and cov[0] <= frm.timestamp() + 86400 and time.time() - cov[1] < SERIES_TTL:
            return
        cached = await self.cache.get(f"cg:series:v1:{cid}")
        if cached and cached["from"] <= frm.timestamp() + 86400 and time.time() - cached["at"] < SERIES_TTL:
            self._series[cid] = (cached["ts"], [Decimal(x) for x in cached["px"]])
            self._covered[cid] = (cached["from"], cached["at"])
            return
        if cov:
            frm = min(frm, datetime.fromtimestamp(cov[0], timezone.utc))
        if frm >= to:
            self._series[cid] = ([], [])   # all activity is older than the plan allows
            self._covered[cid] = (frm.timestamp(), time.time())
            return
        try:
            d = await self.http.get(f"/coins/{cid}/market_chart/range", params={
                "vs_currency": "usd", "from": int(frm.timestamp()), "to": int(to.timestamp())})
        except ProviderError as e:
            # Prices are best-effort: a pricing problem must never fail a scan
            log.warning("price history unavailable for %s: %s", cid, str(e)[:200])
            self._series.setdefault(cid, ([], []))
            return
        pts = d.get("prices") or []
        self._series[cid] = ([int(p[0] / 1000) for p in pts], [Decimal(str(p[1])) for p in pts])
        self._covered[cid] = (frm.timestamp(), time.time())
        ts, px = self._series[cid]
        await self.cache.set(f"cg:series:v1:{cid}", {"from": frm.timestamp(), "at": time.time(), "ts": ts,
                                                     "px": [str(x) for x in px]}, SERIES_TTL)

    async def history_for(self, a: Asset, start: datetime) -> str | None:
        """Make sure a price series from start to today is loaded; return its id."""
        cid = await self.coin_id(a)
        if cid:
            await self._load(cid, start)
        return cid

    def price_on(self, cid: str | None, a: Asset, when: datetime) -> Decimal | None:
        p = self._at(cid, when) if cid else None
        if p is None and a.symbol.upper() in STABLECOINS:
            return Decimal(1)
        return p

    def _at(self, cid: str, when: datetime) -> Decimal | None:
        ts, px = self._series.get(cid, ([], []))
        if not ts:
            return None
        i = bisect.bisect_left(ts, int(when.timestamp()))
        cands = [j for j in (i - 1, i) if 0 <= j < len(ts)]
        j = min(cands, key=lambda k: abs(ts[k] - when.timestamp()))
        return px[j] if abs(ts[j] - when.timestamp()) < 2 * 86400 else None

    async def price_transactions(self, txs: list[NormalizedTx]) -> None:
        if not txs:
            return
        start = min(t.timestamp for t in txs)
        end = max(t.timestamp for t in txs)
        assets = {l.asset for t in txs for l in t.legs} | {t.fee_asset for t in txs if t.fee_asset}
        if any(a.contract and not a.is_nft for a in assets):
            await self._load_directory()
        for a in assets:
            cid = await self.coin_id(a)
            if cid:
                await self._load(cid, start, end)

        for t in txs:
            for leg in t.legs:
                p = await self._price(leg.asset, t.timestamp)
                if p is not None:
                    leg.usd_value = abs(leg.amount) * p
            if t.fee_asset and t.fee:
                p = await self._price(t.fee_asset, t.timestamp)
                t.fee_usd = t.fee * p if p is not None else None
            _infer_nft_values(t)

    async def _price(self, a: Asset, when: datetime) -> Decimal | None:
        cid = await self.coin_id(a)
        p = self._at(cid, when) if cid else None
        if p is None and a.symbol.upper() in STABLECOINS:
            return Decimal(1)
        return p

    async def current(self, assets: list[Asset]) -> dict[str, Decimal]:
        if any(a.contract and not a.is_nft for a in assets):
            await self._load_directory()
        ids = {a.key: await self.coin_id(a) for a in assets}
        wanted = sorted({i for i in ids.values() if i})
        out: dict[str, Decimal] = {}
        known: dict[str, Decimal] = {}
        for cid, v in zip(wanted, await self.cache.mget([f"cg:now:v1:{c}" for c in wanted])):
            if v is not None:
                known[cid] = Decimal(v)
        for key, cid in ids.items():
            if cid in known:
                out[key] = known[cid]
        wanted = [c for c in wanted if c not in known]
        for i in range(0, len(wanted), 200):
            try:
                d = await self.http.get("/simple/price", params={"ids": ",".join(wanted[i:i + 200]), "vs_currencies": "usd"})
            except ProviderError as e:
                log.warning("current prices unavailable: %s", str(e)[:200])
                continue
            for key, cid in ids.items():
                if cid in d:
                    out[key] = Decimal(str(d[cid]["usd"]))
            for cid in wanted[i:i + 200]:
                if cid in d and "usd" in d[cid]:
                    await self.cache.set(f"cg:now:v1:{cid}", str(d[cid]["usd"]), CURRENT_TTL)
        return out


def _infer_nft_values(t: NormalizedTx) -> None:
    """An NFT's value in a trade is what was paid for it (or received)."""
    nft = [l for l in t.legs if l.asset.is_nft and l.usd_value is None]
    if not nft:
        return
    other = [l for l in t.legs if not l.asset.is_nft and l.usd_value is not None and (l.amount > 0) != (nft[0].amount > 0)]
    total = sum((l.usd_value for l in other), Decimal(0))
    if total:
        for l in nft:
            l.usd_value = total / len(nft)


def now() -> datetime:
    return datetime.now(timezone.utc)
