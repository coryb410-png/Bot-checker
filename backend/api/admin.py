"""Admin panel API: live status and controls, all in memory.

Locked by ADMIN_TOKEN (set it in Render; the panel is off without it), sent
as the X-Admin-Token header. Wrong tokens are rate limited. Nothing here is
stored, and no wallet addresses are shown: scans are listed by network,
size and status only.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import time
from collections import Counter

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from api import limits
from core import monitor
from core.models import Chain

# Settings whose presence the panel reports (never their values)
KEYS = [
    ("ALCHEMY_API_KEY", "Alchemy (Ethereum, Base, Arbitrum, Optimism, Polygon, ETH NFTs)"),
    ("HELIUS_API_KEY", "Helius (Solana)"),
    ("COINGECKO_API_KEY", "CoinGecko (prices)"),
    ("KOIOS_API_KEY", "Koios (Cardano, raises the daily limit)"),
    ("MAESTRO_API_KEY", "Maestro (Dogecoin NFTs)"),
    ("BLOCKCYPHER_TOKEN", "BlockCypher (Dogecoin backup)"),
    ("MAGICEDEN_API_KEY", "Magic Eden (Solana floors)"),
    ("CACHE_URL", "Shared price cache (Render Key Value)"),
    ("PROXY_SHARED_SECRET", "Website proxy key (per-person limits)"),
    ("API_CLIENT_KEYS", "Partner API keys"),
]


def _auth(req: Request) -> None:
    token = os.environ.get("ADMIN_TOKEN", "")
    if len(token) < 16:
        raise HTTPException(503, "The admin panel is off. Set ADMIN_TOKEN (16+ characters) in Render to turn it on.")
    sent = req.headers.get("x-admin-token", "")
    if not hmac.compare_digest(token, sent):
        limits.take(req, "admin_auth")   # repeated wrong passwords get slowed down
        raise HTTPException(401, "Wrong admin password.")


class LimitsBody(BaseModel):
    kind: str
    per_minute: float = Field(gt=0, le=100000)
    burst: int = Field(ge=1, le=100000)


class MaintenanceBody(BaseModel):
    on: bool
    message: str | None = Field(default=None, max_length=300)


class CacheBody(BaseModel):
    target: str = "all"   # prices | floors | all


class PartnerBody(BaseModel):
    name: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9-]+$")


def register(app, store, prices) -> dict:

    @app.get("/api/admin/overview")
    async def overview(req: Request):
        _auth(req)
        ps = list(store.portfolios.values())
        c = monitor.counters
        partners = [i.split(":", 1)[0] for i in (os.environ.get("API_CLIENT_KEYS") or "").split(",") if ":" in i]
        return {
            "started_at": monitor.STARTED_AT.isoformat(timespec="seconds"),
            "uptime_seconds": monitor.uptime_seconds(),
            "version": (os.environ.get("RENDER_GIT_COMMIT") or "")[:7] or None,
            "maintenance": monitor.maintenance,
            "scans": {
                "in_memory": len(ps),
                "by_status": dict(Counter(p.status for p in ps)),
                "transactions_in_memory": sum(len(p.txs) for p in ps),
                "started": c["scan_started"], "finished": c["scan_finished"], "failed": c["scan_failed"],
            },
            "networks": dict(monitor.networks.most_common()),
            "provider_errors": {k.split(":", 1)[1]: v for k, v in c.items() if k.startswith("provider_error:")},
            "rate_limited": {k.split(":", 1)[1]: v for k, v in c.items() if k.startswith("rate_limited:")},
            "limits": {k: {"per_minute": round(r.per_minute, 4), "burst": r.burst} for k, r in limits.RULES.items()},
            "partner_multiplier": limits.PARTNER_MULTIPLIER,
            "keys": [{"name": n, "what": w, "set": bool(os.environ.get(n))} for n, w in KEYS],
            "partners": partners,
            "cors_origins": [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()],
        }

    @app.get("/api/admin/scans")
    async def scans(req: Request):
        _auth(req)
        rows = []
        for p in sorted(store.portfolios.values(), key=lambda p: p.created_at, reverse=True):
            rows.append({
                "id": p.id, "status": p.status, "demo": p.demo,
                "created_at": p.created_at.isoformat(timespec="seconds"),
                "networks": dict(Counter(c.value for c, _ in p.addresses)),
                "addresses": len({a.lower() if a.startswith("0x") else a for _, a in p.addresses}),
                "transactions": len(p.txs),
                "nfts": _nfts_held(p),
                "network_errors": len(p.network_errors),
                "error": monitor.scrub(p.error) or None,
            })
        return rows

    @app.delete("/api/admin/scans/{pid}", status_code=204)
    async def delete_scan(pid: str, req: Request):
        _auth(req)
        if not store.delete(pid):
            raise HTTPException(404, "No such scan (it may already be gone).")
        monitor.record("admin", "Deleted a scan")

    @app.delete("/api/admin/scans", status_code=200)
    async def delete_all(req: Request, include_running: bool = False):
        _auth(req)
        busy = {"queued", "scanning", "pricing", "valuing"}
        gone = [pid for pid, p in list(store.portfolios.items()) if include_running or p.status not in busy]
        for pid in gone:
            store.delete(pid)
        monitor.record("admin", f"Cleared {len(gone)} scans")
        return {"deleted": len(gone)}

    @app.get("/api/admin/events")
    async def events(req: Request, limit: int = 200):
        _auth(req)
        return list(monitor.events)[-max(1, min(limit, 300)):][::-1]   # newest first

    @app.post("/api/admin/health")
    async def health_check(req: Request):
        _auth(req)
        return await _health(prices)

    @app.put("/api/admin/limits")
    async def set_limits(body: LimitsBody, req: Request):
        _auth(req)
        if body.kind not in limits.RULES or body.kind == "admin_auth":
            raise HTTPException(400, "Unknown limit.")
        limits.RULES[body.kind] = limits.Rule(body.per_minute, body.burst)
        limits.reset()
        monitor.record("admin", f"Changed the {body.kind} limit to {body.per_minute}/min, burst {body.burst} (until restart)")
        return {"ok": True}

    @app.put("/api/admin/maintenance")
    async def set_maintenance(body: MaintenanceBody, req: Request):
        _auth(req)
        monitor.maintenance["on"] = body.on
        if body.message:
            monitor.maintenance["message"] = body.message.strip()
        monitor.record("admin", "Maintenance mode " + ("ON: new scans paused" if body.on else "OFF"))
        return monitor.maintenance

    @app.post("/api/admin/cache/clear")
    async def clear_cache(body: CacheBody, req: Request):
        _auth(req)
        cleared = []
        if body.target in ("prices", "all"):
            prices._series.clear(); prices._covered.clear(); prices._ids.clear()
            prices._directory.clear(); prices._directory_at = 0.0
            client = prices.cache.client
            if client is not None:
                try:
                    keys = [k async for k in client.scan_iter(match="cg:*", count=500)]
                    if keys:
                        await client.delete(*keys)
                except Exception as e:   # the shared store being down shouldn't fail the button
                    monitor.record("admin", f"Shared price cache not cleared: {e}")
            cleared.append("prices")
        if body.target in ("floors", "all"):
            from portfolio import service
            fs = service._floor_service
            if fs is not None:
                fs._floors.clear(); fs._symbols.clear()
            cleared.append("NFT floors")
        monitor.record("admin", "Cleared caches: " + ", ".join(cleared))
        return {"cleared": cleared}

    @app.post("/api/admin/partner-key")
    async def partner_key(body: PartnerBody, req: Request):
        """A new partner API key. Not stored: copy the new API_CLIENT_KEYS value into Render."""
        _auth(req)
        key = "cs_" + secrets.token_urlsafe(24)
        existing = (os.environ.get("API_CLIENT_KEYS") or "").strip().strip(",")
        line = f"{existing},{body.name}:{key}" if existing else f"{body.name}:{key}"
        monitor.record("admin", f"Generated a partner key for {body.name}")
        return {"name": body.name, "key": key, "api_client_keys": line}

    return {"overview": overview, "scans": scans, "delete_scan": delete_scan, "delete_all": delete_all,
            "events": events, "set_limits": set_limits, "set_maintenance": set_maintenance,
            "clear_cache": clear_cache, "partner_key": partner_key}


def _nfts_held(p) -> int:
    nft_keys = {l.asset.key for t in p.txs for l in t.legs if l.asset.is_nft}
    held = sum(1 for k, a in (p.report.assets.items() if p.report else []) if k in nft_keys and a.quantity > 0)
    return held + len(getattr(p, "untracked_nfts", []) or [])


async def _health(prices) -> list[dict]:
    """Quick check of every data provider, using each scanner's own settings."""
    from portfolio.service import _scanner
    from pricing.floors import FloorService

    async def jrpc(http, method, params=None, **kw):
        return await http.post("", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}, **kw)

    def sol():
        s = _scanner(Chain.SOLANA)
        return jrpc(s.rpc, "getHealth", params=s.rpc_params)

    checks = {
        "CoinGecko (prices)": lambda: prices.http.get("/ping"),
        "Bitcoin · mempool.space": lambda: _scanner(Chain.BITCOIN).http.get("/blocks/tip/height"),
        "Ethereum · Alchemy": lambda: jrpc(_scanner(Chain.ETHEREUM).http, "eth_blockNumber"),
        "Base · Alchemy": lambda: jrpc(_scanner(Chain.BASE).http, "eth_blockNumber"),
        "Arbitrum · Alchemy": lambda: jrpc(_scanner(Chain.ARBITRUM).http, "eth_blockNumber"),
        "Optimism · Alchemy": lambda: jrpc(_scanner(Chain.OPTIMISM).http, "eth_blockNumber"),
        "Polygon · Alchemy": lambda: jrpc(_scanner(Chain.POLYGON).http, "eth_blockNumber"),
        "Robinhood Chain · Blockscout": lambda: _scanner(Chain.ROBINHOOD).http.get("/stats"),
        "Solana · Helius": sol,
        "Cardano · Koios": lambda: _scanner(Chain.CARDANO).http.get("/tip"),
        "XRP Ledger": lambda: _scanner(Chain.XRP)._rpc("server_info"),
        "Dogecoin · explorer": lambda: _scanner(Chain.DOGECOIN).http.get("/blocks/tip/height"),
        "Dogecoin · BlockCypher backup": lambda: _scanner(Chain.DOGECOIN).backup.get(""),
        "Solana floors · Magic Eden": lambda: FloorService().me.get("/collections", params={"limit": 1}),
    }
    if os.environ.get("CACHE_URL"):
        async def ping_cache():
            client = prices.cache.client
            if client is None:
                raise RuntimeError("not connected")
            return await client.ping()
        checks["Shared price cache"] = ping_cache

    async def run(name, fn):
        t = time.monotonic()
        try:
            await asyncio.wait_for(fn(), 10)
            return {"name": name, "ok": True, "ms": int((time.monotonic() - t) * 1000)}
        except asyncio.TimeoutError:
            return {"name": name, "ok": False, "ms": 10000, "error": "No answer within 10 seconds"}
        except Exception as e:
            return {"name": name, "ok": False, "ms": int((time.monotonic() - t) * 1000), "error": monitor.scrub(str(e), 160)}

    return list(await asyncio.gather(*(run(n, f) for n, f in checks.items())))
