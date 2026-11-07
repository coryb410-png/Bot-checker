"""Cardano via the Koios API (no key needed; free registration raises limits).

Docs: https://api.koios.rest   Base URL override: KOIOS_API_URL
Optional KOIOS_API_KEY (free account: 50,000 requests/day instead of 5,000).

Cardano has no accounts, only coins (UTXOs) sitting at addresses, and one
wallet uses many addresses that share a stake key. So whatever address is
entered, the whole wallet is scanned: every address under its stake key.
A transaction's effect on the wallet is everything it sent to the wallet's
addresses minus everything it spent from them, per asset. Staking rewards
withdrawn in a transaction are reported separately as income.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from core.models import Asset, Chain, Leg, NormalizedTx, safe_image

from .base import Http, env

LOVELACE = Decimal(1_000_000)
ADA = Asset(Chain.CARDANO, "ADA", decimals=6, coingecko_id="cardano")
PAGE = 1000          # Koios returns at most 1,000 rows per request
TX_BATCH = 50        # transactions per tx_info call
MAX_TXS = 20_000
HANDLE_POLICY = "f0ff48bbb7bbe9d59a40f1ce90e9e9d0ff5002ec48f232b49ca0fb9a"   # ADA Handle


def _ascii(hex_name: str) -> str | None:
    try:
        s = bytes.fromhex(hex_name or "").decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return s if s and s.isprintable() else None


def _join(v) -> str | None:
    """CIP-25 splits long strings (like image URLs) into lists of 64-char chunks."""
    if isinstance(v, list):
        return "".join(x for x in v if isinstance(x, str)) or None
    return v if isinstance(v, str) else None


def _cip68_display(hex_name: str) -> str:
    # CIP-68 user tokens carry a 4-byte label prefix (000de140 = NFT, 0014df10 = FT)
    if hex_name[:8] in ("000de140", "0014df10", "000643b0"):
        return hex_name[8:]
    return hex_name


_CID = re.compile(r"^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{50,})(/.*)?$")


def _clean_image(v) -> str | None:
    """Image link from metadata: ipfs://, ar://, https:// or a bare IPFS ID
    (common on Cardano). On-chain data: images are skipped."""
    url = _join(v)
    if not url:
        return None
    url = url.strip()
    if _CID.match(url):
        url = "ipfs://" + url
    return safe_image(url)


def _nft_image(entry: dict) -> str | None:
    img = _clean_image(entry.get("image"))
    if img:
        return img
    for f in entry.get("files") or []:   # some collections only list files
        if isinstance(f, dict) and str(f.get("mediaType", "image")).startswith("image"):
            img = _clean_image(f.get("src"))
            if img:
                return img
    return None


def _datum_text(v) -> str | None:
    """A CIP-68 datum value as text: bytes, or a list of byte chunks."""
    if not isinstance(v, dict):
        return None
    if "bytes" in v:
        return _ascii(v["bytes"])
    if isinstance(v.get("list"), list):
        parts = [_ascii((x or {}).get("bytes", "")) or "" for x in v["list"]]
        return "".join(parts) or None
    return None


class CardanoScanner:
    def __init__(self, http: Http | None = None):
        key = env("KOIOS_API_KEY", "")
        self.http = http or Http(env("KOIOS_API_URL", "https://api.koios.rest/api/v1"), rate_per_sec=8,
                                 headers={"Authorization": f"Bearer {key}"} if key else None)
        self._assets: dict[str, dict] = {}   # unit (policy + name hex) -> asset_info row

    # ------------------------------------------------------------ wallet scope
    async def _wallet(self, address: str) -> tuple[str | None, set[str]]:
        """(stake address or None, every payment address of the wallet)"""
        stake = address if address.startswith("stake1") else None
        if not stake:
            info = await self.http.post("/address_info", json={"_addresses": [address]})
            stake = (info[0].get("stake_address") if info else None) or None
        if not stake:
            return None, {address}   # enterprise or Byron address: no stake key
        addrs, offset = set(), 0
        while True:
            rows = await self.http.post("/account_addresses", params={"offset": offset, "limit": PAGE},
                                        json={"_stake_addresses": [stake], "_empty": True})
            for r in rows or []:
                addrs.update(r.get("addresses") or [])
            if len(rows or []) < PAGE:
                break
            offset += PAGE
        if not address.startswith("stake1"):
            addrs.add(address)
        return stake, addrs

    async def _tx_list(self, stake: str | None, addrs: set[str]) -> list[dict]:
        out, offset = [], 0
        while len(out) < MAX_TXS:
            if stake:
                rows = await self.http.get("/account_txs", params={"_stake_address": stake, "offset": offset, "limit": PAGE})
            else:
                rows = await self.http.post("/address_txs", params={"offset": offset, "limit": PAGE},
                                            json={"_addresses": sorted(addrs)})
            out += rows or []
            if len(rows or []) < PAGE:
                break
            offset += PAGE
        # newest first, one row per transaction
        seen, uniq = set(), []
        for r in sorted(out, key=lambda r: (r.get("block_height") or 0, r.get("block_time") or 0), reverse=True):
            if r["tx_hash"] not in seen:
                seen.add(r["tx_hash"])
                uniq.append(r)
        return uniq

    # ------------------------------------------------------------ scanning
    async def scan(self, address: str, since_cursor: str | None = None) -> AsyncIterator[NormalizedTx]:
        stake, addrs = await self._wallet(address)
        hashes = []
        for r in await self._tx_list(stake, addrs):
            if r["tx_hash"] == since_cursor:
                break
            hashes.append(r["tx_hash"])

        order = {h: i for i, h in enumerate(hashes)}
        for i in range(0, len(hashes), TX_BATCH):   # batch by batch, so progress shows
            raw_txs = await self.http.post("/tx_info", json={
                "_tx_hashes": hashes[i:i + TX_BATCH], "_inputs": True, "_metadata": False, "_assets": True,
                "_withdrawals": True, "_certs": False, "_scripts": False, "_bytecode": False}) or []
            await self._load_assets({f"{a['policy_id']}{a.get('asset_name') or ''}"
                                     for t in raw_txs for io in (t.get("inputs") or []) + (t.get("outputs") or [])
                                     for a in io.get("asset_list") or []})
            for raw in sorted(raw_txs, key=lambda t: order.get(t["tx_hash"], 0)):
                for tx in self.normalize(raw, address, stake, addrs):
                    yield tx

    async def _load_assets(self, units: set[str]) -> None:
        todo = sorted(u for u in units if u not in self._assets)
        for i in range(0, len(todo), 50):
            chunk = todo[i:i + 50]
            try:
                rows = await self.http.post("/asset_info", json={"_asset_list": [[u[:56], u[56:]] for u in chunk]})
            except Exception:
                continue   # names and images are a nice-to-have
            for r in rows or []:
                self._assets[f"{r['policy_id']}{r.get('asset_name') or ''}"] = r

    def asset(self, unit: str, decimals_hint: int | None = None) -> Asset:
        policy, name_hex = unit[:56], unit[56:]
        info = self._assets.get(unit) or {}
        reg = info.get("token_registry_metadata") or {}
        display_hex = _cip68_display(name_hex)
        ascii_name = info.get("asset_name_ascii") or _ascii(display_hex) or _ascii(name_hex)
        is_nft = str(info.get("total_supply") or "") == "1" and not reg
        if is_nft:
            meta = self._nft_meta(info, policy, ascii_name)
            name = meta.get("name") or ascii_name or info.get("fingerprint") or unit[:12]
            return Asset(Chain.CARDANO, (ascii_name or "NFT")[:16], contract=unit, decimals=0, is_nft=True,
                         token_id=unit, name=name, image=meta.get("image"), collection=policy)
        decimals = reg.get("decimals")
        if decimals is None:
            decimals = decimals_hint if decimals_hint is not None else 0
        symbol = reg.get("ticker") or ascii_name or info.get("fingerprint") or unit[:8]
        return Asset(Chain.CARDANO, symbol[:16], contract=unit, decimals=int(decimals),
                     name=reg.get("name") or ascii_name)

    @staticmethod
    def _nft_meta(info: dict, policy: str, ascii_name: str | None) -> dict:
        """Name and image for an NFT, from whichever layout its metadata uses:
        CIP-25 (label 721; keys as text or hex; v1 or v2) or CIP-68 (datum)."""
        name_hex = (info.get("asset_name") or "").lower()
        wanted = {k for k in (ascii_name, name_hex, _ascii(_cip68_display(name_hex)), _cip68_display(name_hex)) if k}

        m = info.get("minting_tx_metadata")
        # Koios has returned {"721": {...}}, {"key": "721", "json": {...}} and lists of those
        if isinstance(m, list):
            m = next((x for x in m if str((x or {}).get("key")) == "721"), {}) or {}
        if isinstance(m, dict) and "json" in m and str(m.get("key")) == "721":
            m = {"721": m["json"]}
        cip25 = (m or {}).get("721") if isinstance(m, dict) else None
        if isinstance(cip25, dict):
            by_policy = next((v for k, v in cip25.items() if str(k).lower() == policy.lower()), None)
            if isinstance(by_policy, dict):
                entry = next((v for k, v in by_policy.items() if k in wanted or str(k).lower() in wanted), None)
                if entry is None:
                    entries = [v for v in by_policy.values() if isinstance(v, dict)]
                    entry = entries[0] if len(entries) == 1 else None
                if isinstance(entry, dict):
                    return {"name": _join(entry.get("name")), "image": _nft_image(entry)}

        # CIP-68: reference NFT datum, a map of byte-string keys to values
        c = info.get("cip68_metadata") or {}
        for v in (c.values() if isinstance(c, dict) else []):
            fields = v.get("fields") if isinstance(v, dict) else None
            first = fields[0] if isinstance(fields, list) and fields and isinstance(fields[0], dict) else {}
            out = {}
            for kv in first.get("map") or []:
                k = _ascii(((kv or {}).get("k") or {}).get("bytes", ""))
                val = _datum_text((kv or {}).get("v"))
                if k in ("name", "image") and val:
                    out[k] = val
            if out:
                out["image"] = _clean_image(out.get("image"))
                return out
        return {}

    def normalize(self, raw: dict, wallet: str, stake: str | None, addrs: set[str]) -> list[NormalizedTx]:
        def mine(io: dict) -> bool:
            pay = ((io.get("payment_addr") or {}).get("bech32")) or io.get("address")
            return (pay in addrs) or (stake is not None and io.get("stake_addr") == stake)

        net: dict[str, Decimal] = defaultdict(Decimal)   # unit -> raw quantity ("" = lovelace)
        dec_hint: dict[str, int] = {}
        spent_any = False
        for sign, side in ((-1, raw.get("inputs") or []), (1, raw.get("outputs") or [])):
            for io in side:
                if not mine(io):
                    continue
                if sign < 0:
                    spent_any = True
                net[""] += sign * Decimal(int(io.get("value") or 0))
                for a in io.get("asset_list") or []:
                    unit = f"{a['policy_id']}{a.get('asset_name') or ''}"
                    net[unit] += sign * Decimal(int(a.get("quantity") or 0))
                    if a.get("decimals") is not None:
                        dec_hint[unit] = int(a["decimals"])

        fee = Decimal(int(raw.get("fee") or 0)) if spent_any else Decimal(0)
        reward = sum((Decimal(int(w.get("amount") or 0)) for w in raw.get("withdrawals") or []
                      if stake and w.get("stake_addr") == stake), Decimal(0))
        # Remove the fee and any rewards withdrawn so the rest is the real movement
        ada_move = net.pop("", Decimal(0)) + fee - reward

        others = lambda side: next((((io.get("payment_addr") or {}).get("bech32")) for io in side if not mine(io)), None)
        ts = datetime.fromtimestamp(int(raw.get("tx_timestamp") or raw.get("block_time") or 0), timezone.utc) \
            if (raw.get("tx_timestamp") or raw.get("block_time")) else datetime.now(timezone.utc)

        legs = []
        if ada_move:
            legs.append(Leg(ADA, ada_move / LOVELACE,
                            counterparty=others(raw.get("outputs") or []) if ada_move < 0 else others(raw.get("inputs") or [])))
        for unit, qty in net.items():
            if not qty:
                continue
            a = self.asset(unit, dec_hint.get(unit))
            amount = qty / (Decimal(10) ** a.decimals) if a.decimals else qty
            legs.append(Leg(a, amount))

        out = [NormalizedTx(Chain.CARDANO, raw["tx_hash"], ts, wallet, legs,
                            fee=fee / LOVELACE, fee_asset=ADA if fee else None)]
        if reward:
            # Staking rewards are income: a separate entry valued when withdrawn
            out.append(NormalizedTx(Chain.CARDANO, raw["tx_hash"] + "#reward", ts, wallet,
                                    [Leg(ADA, reward / LOVELACE, counterparty=stake)], hint="staking_reward"))
        if not legs and not fee and not reward:
            return []
        return out

    # ------------------------------------------------------------ owned NFTs
    async def held_nfts(self, owner: str) -> list[Asset]:
        stake, addrs = await self._wallet(owner)
        rows, offset = [], 0
        while True:
            if stake:
                page = await self.http.post("/account_assets", params={"offset": offset, "limit": PAGE},
                                            json={"_stake_addresses": [stake]})
            else:
                page = await self.http.post("/address_assets", params={"offset": offset, "limit": PAGE},
                                            json={"_addresses": sorted(addrs)})
            rows += page or []
            if len(page or []) < PAGE or offset > 20 * PAGE:
                break
            offset += PAGE
        units = {f"{r['policy_id']}{r.get('asset_name') or ''}" for r in rows if str(r.get("quantity")) == "1"}
        await self._load_assets(units)
        return [a for a in (self.asset(u) for u in sorted(units)) if a.is_nft]

    # ------------------------------------------------------------ $handles
    async def resolve_handle(self, handle: str) -> str:
        """$name -> the address holding that ADA Handle (CIP-68 first, then legacy)."""
        name_hex = handle.strip().lstrip("$").lower().encode().hex()
        for asset_name in ("000de140" + name_hex, name_hex):
            rows = await self.http.get("/asset_addresses", params={"_asset_policy": HANDLE_POLICY, "_asset_name": asset_name})
            if rows:
                return rows[0].get("payment_address") or rows[0]["address"]
        raise LookupError(handle)
