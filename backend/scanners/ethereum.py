"""Ethereum via Alchemy (alchemy_getAssetTransfers + receipts for gas).

Docs: https://www.alchemy.com/docs  (Transfers API, eth_getTransactionReceipt)

Covers ETH (external + internal), ERC-20, ERC-721 and ERC-1155 movements.
Calls that move no assets (e.g. token approvals) are not returned by the
Transfers API; add an Etherscan-style tx list later if you need those.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import AsyncIterator

from core.models import Asset, Chain, Leg, NormalizedTx, safe_image

from .base import Http, env

ETH = Asset(Chain.ETHEREUM, "ETH", decimals=18, coingecko_id="ethereum")
CATEGORIES = ["external", "internal", "erc20", "erc721", "erc1155", "specialnft"]
WEI = Decimal(10) ** 18


class EthereumScanner:
    def __init__(self, http: Http | None = None, nft_http: Http | None = None):
        key = env("ALCHEMY_API_KEY")
        self.http = http or Http(f"https://eth-mainnet.g.alchemy.com/v2/{key}", rate_per_sec=10)
        self.nft_http = nft_http or Http(f"https://eth-mainnet.g.alchemy.com/nft/v3/{key}", rate_per_sec=5)
        self._id = 0

    async def _rpc(self, method: str, params: list) -> dict:
        self._id += 1
        res = await self.http.post("", json={"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
        if "error" in res:
            raise RuntimeError(res["error"])
        return res["result"]

    async def _transfers(self, direction: str, address: str, since_cursor: str | None) -> list[dict]:
        out, page_key = [], None
        while True:
            params = {
                "fromBlock": "0x0", "toBlock": "latest", direction: address,
                "category": CATEGORIES, "withMetadata": True, "excludeZeroValue": True,
                "maxCount": "0x3e8", "order": "desc",
            }
            if page_key:
                params["pageKey"] = page_key
            res = await self._rpc("alchemy_getAssetTransfers", [params])
            for t in res["transfers"]:
                if t["hash"] == since_cursor:
                    return out
                out.append(t)
            page_key = res.get("pageKey")
            if not page_key:
                return out

    async def scan(self, address: str, since_cursor: str | None = None) -> AsyncIterator[NormalizedTx]:
        address = address.lower()
        transfers = (await self._transfers("fromAddress", address, since_cursor)
                     + await self._transfers("toAddress", address, since_cursor))
        by_hash: dict[str, list[dict]] = defaultdict(list)
        seen = set()
        for t in transfers:
            uid = t.get("uniqueId") or (t["hash"], t.get("logIndex"), t["from"], t["to"], t.get("value"))
            if uid in seen:
                continue  # self-transfers appear in both directions
            seen.add(uid)
            by_hash[t["hash"]].append(t)

        txs = [self._normalize(h, ts, address) for h, ts in by_hash.items()]
        await self._attach_fees([t for t in txs if t._sender_is_me], address)  # type: ignore[attr-defined]
        await self._attach_nft_metadata(txs)
        for tx in sorted(txs, key=lambda t: t.timestamp, reverse=True):
            yield tx

    def _normalize(self, tx_hash: str, transfers: list[dict], me: str) -> NormalizedTx:
        legs: list[Leg] = []
        for t in transfers:
            sign = Decimal(1) if (t.get("to") or "").lower() == me else Decimal(-1)
            other = t["from"] if sign > 0 else t.get("to")
            cat, rc = t["category"], t.get("rawContract") or {}
            if cat in ("external", "internal"):
                amount = Decimal(int(rc.get("value") or "0x0", 16)) / WEI if rc.get("value") else Decimal(str(t["value"] or 0))
                legs.append(Leg(ETH, sign * amount, counterparty=other))
            elif cat == "erc20":
                dec = int(rc.get("decimal") or "0x12", 16)
                raw = int(rc.get("value") or "0x0", 16)
                asset = Asset(Chain.ETHEREUM, t.get("asset") or "UNKNOWN", contract=rc["address"].lower(), decimals=dec)
                legs.append(Leg(asset, sign * Decimal(raw) / (Decimal(10) ** dec), counterparty=other))
            else:  # NFTs
                items = t.get("erc1155Metadata") or [{"tokenId": t.get("tokenId") or t.get("erc721TokenId"), "value": "0x1"}]
                for it in items:
                    asset = Asset(Chain.ETHEREUM, t.get("asset") or "NFT", contract=rc["address"].lower(),
                                  decimals=0, is_nft=True, token_id=str(int(it["tokenId"], 16)))
                    legs.append(Leg(asset, sign * int(it.get("value") or "0x1", 16), counterparty=other))

        merged = _merge_legs(legs)
        ts = datetime.fromisoformat(transfers[0]["metadata"]["blockTimestamp"].replace("Z", "+00:00"))
        tx = NormalizedTx(Chain.ETHEREUM, tx_hash, ts, me, merged)
        # The top-level "external" transfer's sender is the tx signer
        tx._sender_is_me = any(t["category"] == "external" and t["from"].lower() == me for t in transfers) \
            or any(l.amount < 0 for l in merged)  # type: ignore[attr-defined]
        tx.interacted_with = next((t.get("to") for t in transfers if t["category"] == "external"), None)
        return tx

    async def _attach_fees(self, txs: list[NormalizedTx], me: str) -> None:
        # Batched JSON-RPC: 50 receipts per request
        for i in range(0, len(txs), 50):
            chunk = txs[i:i + 50]
            batch = [{"jsonrpc": "2.0", "id": j, "method": "eth_getTransactionReceipt", "params": [t.tx_hash]}
                     for j, t in enumerate(chunk)]
            results = await self.http.post("", json=batch)
            for r in results:
                rec, tx = r.get("result"), chunk[r["id"]]
                if not rec or rec["from"].lower() != me:
                    continue
                gas = int(rec["gasUsed"], 16) * int(rec.get("effectiveGasPrice", "0x0"), 16)
                tx.fee, tx.fee_asset = Decimal(gas) / WEI, ETH
                tx.success = rec.get("status") == "0x1"
                tx.interacted_with = rec.get("to")

    async def _attach_nft_metadata(self, txs: list[NormalizedTx]) -> None:
        """Names, images and spam flags for every NFT seen (Alchemy NFT API v3)."""
        tokens = {(l.asset.contract, l.asset.token_id) for t in txs for l in t.legs if l.asset.is_nft}
        meta: dict[tuple[str, str], dict] = {}
        tokens_list = sorted(tokens)
        for i in range(0, len(tokens_list), 100):
            chunk = tokens_list[i:i + 100]
            try:
                res = await self.nft_http.post("/getNFTMetadataBatch", json={
                    "tokens": [{"contractAddress": c, "tokenId": tid} for c, tid in chunk],
                    "refreshCache": False})
            except Exception:
                continue   # images are a nice-to-have; never fail a scan over them
            meta.update(parse_alchemy_nfts(res))
        for t in txs:
            for leg in t.legs:
                m = meta.get((leg.asset.contract, leg.asset.token_id)) if leg.asset.is_nft else None
                if m:
                    leg.asset = replace(leg.asset, **m)

    async def balances(self, address: str) -> dict[str, Decimal]:
        eth = int(await self._rpc("eth_getBalance", [address, "latest"]), 16)
        out = {ETH.key: Decimal(eth) / WEI}
        res = await self._rpc("alchemy_getTokenBalances", [address, "erc20"])
        for tb in res["tokenBalances"]:
            raw = int(tb["tokenBalance"], 16)
            if raw:
                meta = await self._rpc("alchemy_getTokenMetadata", [tb["contractAddress"]])
                out[f"ethereum:{tb['contractAddress'].lower()}"] = Decimal(raw) / Decimal(10) ** (meta.get("decimals") or 18)
        return out


def parse_alchemy_nfts(res) -> dict[tuple[str, str], dict]:
    items = res.get("nfts", []) if isinstance(res, dict) else (res or [])
    out = {}
    for n in items:
        contract = (n.get("contract") or {})
        addr = (contract.get("address") or "").lower()
        tid = str(n.get("tokenId") or "")
        if tid.startswith("0x"):
            tid = str(int(tid, 16))
        img = n.get("image") or {}
        raw_meta = (n.get("raw") or {}).get("metadata") or {}
        url = img.get("thumbnailUrl") or img.get("cachedUrl") or img.get("originalUrl") or raw_meta.get("image")
        # Only Alchemy's actual verdict counts. spamClassifications lists minor
        # heuristics (e.g. unusual supply) that legitimate collections often trip.
        spam = contract.get("isSpam") in (True, "true", "True")
        name = n.get("name") or raw_meta.get("name") or (contract.get("name") and f"{contract['name']} #{tid}")
        out[(addr, tid)] = {"name": name, "image": safe_image(url), "spam": spam}
    return out


def _merge_legs(legs: list[Leg]) -> list[Leg]:
    """Net out movements of the same asset inside one tx (e.g. refunds)."""
    acc: dict[str, Leg] = {}
    for l in legs:
        if l.asset.key in acc:
            acc[l.asset.key].amount += l.amount
        else:
            acc[l.asset.key] = Leg(l.asset, l.amount, l.counterparty)
    return [l for l in acc.values() if l.amount != 0]
