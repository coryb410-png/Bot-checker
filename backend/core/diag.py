"""One-off, read-only diagnostics printed to the server log at startup.

Set DIAG_SOL to a comma-separated list of Solana addresses (NFT mints or
accounts) to log what program owns each account, who holds each mint (and
whether it's delegated/frozen, i.e. listed in place) and which programs its
recent transactions used. Used to learn how a marketplace stores listings.
Unset it again afterwards. Public on-chain data only; nothing is stored.
"""
from __future__ import annotations

import json
import os


async def run() -> None:
    targets = [t.strip() for t in (os.environ.get("DIAG_SOL") or "").split(",") if t.strip()]
    if not targets:
        return
    from scanners.solana import SolanaScanner
    s = SolanaScanner()

    async def rpc(method, params):
        try:
            res = await s.rpc.post("", params=s.rpc_params, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            return res.get("result") if isinstance(res, dict) else res
        except Exception as e:
            return {"error": str(e)[:300]}

    def out(label, data):
        print(f"[diag] {label}: {json.dumps(data, default=str)[:3500]}", flush=True)

    for t in targets:
        info = await rpc("getAccountInfo", [t, {"encoding": "jsonParsed"}])
        v = (info or {}).get("value") if isinstance(info, dict) else None
        out(f"account {t}", {"owner_program": (v or {}).get("owner"), "lamports": (v or {}).get("lamports"),
                             "executable": (v or {}).get("executable"),
                             "data": (v or {}).get("data") if isinstance((v or {}).get("data"), dict) else
                                     f"{len(((v or {}).get('data') or [''])[0])} b64 chars"})
        largest = await rpc("getTokenLargestAccounts", [t])
        for acc in ((largest or {}).get("value") or [])[:2] if isinstance(largest, dict) else []:
            ta = await rpc("getAccountInfo", [acc["address"], {"encoding": "jsonParsed"}])
            tv = ((ta or {}).get("value") or {}) if isinstance(ta, dict) else {}
            out(f"token account of {t}", {"address": acc["address"], "amount": acc.get("uiAmountString"),
                                          "program": tv.get("owner"), "parsed": (tv.get("data") or {}).get("parsed")})
        asset = await rpc("getAsset", {"id": t})
        if isinstance(asset, dict) and asset.get("ownership"):
            out(f"das ownership {t}", {"ownership": asset.get("ownership"), "interface": asset.get("interface"),
                                       "authorities": asset.get("authorities")})
        try:
            txs = await s.http.get(f"/addresses/{t}/transactions", params={"api-key": s.key, "limit": 6})
        except Exception as e:
            txs = []
            out(f"txs {t}", {"error": str(e)[:300]})
        for tx in txs or []:
            progs = sorted({i.get("programId") for i in tx.get("instructions") or []}
                           | {ii.get("programId") for i in tx.get("instructions") or [] for ii in i.get("innerInstructions") or []})
            out(f"tx {t}", {"sig": tx.get("signature"), "time": tx.get("timestamp"), "type": tx.get("type"),
                            "source": tx.get("source"), "description": (tx.get("description") or "")[:200],
                            "fee_payer": tx.get("feePayer"), "programs": progs,
                            "nft_event": (tx.get("events") or {}).get("nft"),
                            "native": [(n.get("fromUserAccount"), n.get("toUserAccount"), n.get("amount"))
                                       for n in tx.get("nativeTransfers") or []][:6],
                            "tokens": [(n.get("fromUserAccount"), n.get("toUserAccount"), n.get("mint"))
                                       for n in tx.get("tokenTransfers") or []][:6]})
