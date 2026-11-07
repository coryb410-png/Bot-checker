"""Turn raw asset movements into human-meaningful transaction types.

Rules are applied in order; the first match wins. Each rule only looks at the
normalized legs plus scanner hints, so the same logic covers all three chains.
"""
from __future__ import annotations

from decimal import Decimal

from core.models import Asset, Leg, NormalizedTx, TxType

STABLECOINS = {"USDC", "USDT", "DAI", "PYUSD", "USDE", "FDUSD", "TUSD", "USDS"}

# Lower-cased contract / program addresses of well-known bridges.
# Extend from a maintained registry in production.
BRIDGES = {
    "0x3ee18b2214aff97000d974cf647e7c347e8fa585",  # Wormhole token bridge (ETH)
    "0x8eb8a3b98659cce290402893d0123abb75e3ab28",  # Avalanche bridge
    "0x99c9fc46f92e8a1c0dec1b1747d010903e884be1",  # Optimism gateway
    "0x8315177ab297ba92a06054ce80a67ed4dbd7ed3a",  # Arbitrum bridge
    "0x3154cf16ccdb4c6d922629664174b904d80f2c35",  # Base bridge
    "worm2zogwbbz",                                 # Wormhole (Solana) prefix
}
BRIDGE_SOURCES = {"WORMHOLE", "DEBRIDGE", "ALLBRIDGE", "MAYAN", "STARGATE", "ACROSS"}
NFT_MARKETS = {"OPENSEA", "BLUR", "MAGIC_EDEN", "TENSOR", "LOOKSRARE", "X2Y2", "SEAPORT"}


def _is_stable(a: Asset) -> bool:
    return a.symbol.upper() in STABLECOINS


def _is_bridge(tx: NormalizedTx) -> bool:
    if tx.source and tx.source.upper() in BRIDGE_SOURCES:
        return True
    targets = [tx.interacted_with] + [l.counterparty for l in tx.legs]
    return any(t and any(t.lower().startswith(b) for b in BRIDGES) for t in targets)


def _fmt(legs: list[Leg]) -> str:
    syms = []
    for l in legs:
        s = "NFT" if l.asset.is_nft else l.asset.symbol
        if s not in syms:
            syms.append(s)
    return "+".join(syms)


def classify(tx: NormalizedTx, own_addresses: set[str]) -> NormalizedTx:
    sent = [l for l in tx.sent if l.amount != 0]
    recv = [l for l in tx.received if l.amount != 0]
    # Ignore dust/fee-only native movements when a real trade is present
    fungible_sent = [l for l in sent if not l.asset.is_nft]
    fungible_recv = [l for l in recv if not l.asset.is_nft]
    nft_sent = [l for l in sent if l.asset.is_nft]
    nft_recv = [l for l in recv if l.asset.is_nft]

    def done(t: TxType, label: str) -> NormalizedTx:
        tx.tx_type, tx.label = t, label
        return tx

    if tx.hint and not (sent or recv):
        where = f" · {tx.source.replace('_', ' ').title()}" if tx.source else ""
        labels = {"nft_listing": "Listed NFT for sale", "nft_cancel_listing": "Cancelled NFT listing",
                  "nft_bid": "Placed NFT bid", "nft_cancel_bid": "Cancelled NFT bid"}
        if tx.hint in labels:
            return done(TxType.CONTRACT_INTERACTION, labels[tx.hint] + where)

    if not tx.success:
        return done(TxType.NETWORK_FEE, "Failed transaction · fee only")

    # 1. NFT trades
    if nft_recv and fungible_sent and not nft_sent:
        return done(TxType.NFT_PURCHASE, f"NFT purchase · {_fmt(fungible_sent)} → NFT")
    if nft_sent and fungible_recv and not nft_recv:
        return done(TxType.NFT_SALE, f"NFT sale · NFT → {_fmt(fungible_recv)}")

    # 2. Bridges (assets leave for / arrive from another network)
    if _is_bridge(tx) and (sent or recv):
        return done(TxType.BRIDGE, f"Bridge · {_fmt(sent or recv)}")

    # 3. Trades: something out, something different in
    if fungible_sent and fungible_recv:
        out_keys = {l.asset.key for l in fungible_sent}
        in_keys = {l.asset.key for l in fungible_recv}
        if out_keys != in_keys:
            out_stable = all(_is_stable(l.asset) for l in fungible_sent)
            in_stable = all(_is_stable(l.asset) for l in fungible_recv)
            if out_stable and not in_stable:
                return done(TxType.BUY, f"Bought · {_fmt(fungible_recv)}")
            if in_stable and not out_stable:
                return done(TxType.SELL, f"Sold · {_fmt(fungible_sent)} → {_fmt(fungible_recv)}")
            return done(TxType.SWAP, f"Swap · {_fmt(fungible_sent)} → {_fmt(fungible_recv)}")

    # 4. Plain transfers
    if sent and not recv:
        cps = {l.counterparty.lower() for l in sent if l.counterparty}
        if cps and cps <= own_addresses:
            return done(TxType.INTERNAL_TRANSFER, f"Moved {_fmt(sent)} between your wallets")
        return done(TxType.TRANSFER_OUT, f"Transfer out · {_fmt(sent)}")
    if recv and not sent:
        cps = {l.counterparty.lower() for l in recv if l.counterparty}
        if cps and cps <= own_addresses:
            return done(TxType.INTERNAL_TRANSFER, f"Moved {_fmt(recv)} between your wallets")
        return done(TxType.TRANSFER_IN, f"Transfer in · {_fmt(recv)}")

    # 5. Nothing moved but a fee was paid
    if tx.fee > 0 and tx.interacted_with:
        name = tx.source.title() if tx.source else "contract"
        return done(TxType.CONTRACT_INTERACTION, f"Interaction · {name}")
    return done(TxType.NETWORK_FEE, "Network fee")


def classify_all(txs: list[NormalizedTx], own_addresses: set[str]) -> list[NormalizedTx]:
    own = {a.lower() for a in own_addresses}
    return [classify(t, own) for t in txs]
