"""Spam detection for airdropped junk tokens and NFTs.

Real wallets collect unsolicited tokens that advertise scam sites in their
names ("Visit claim-rewards.xyz") or pretend to be valuable. They clutter the
history and are never something the user bought.

An asset is treated as spam when any of these hold:
  1. The data provider already flagged it (Alchemy marks spam NFT contracts).
  2. Its name or symbol advertises a link or a giveaway.
  3. It is a fungible token with no market price that only ever arrived on
     its own: never sent, and never part of a transaction the user started
     (so an obscure token the user actually bought is not hidden).

Spam is hidden, not deleted: the API can include it on request, so a real
token that tripped rule 3 is one tap away.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace

from core.models import NormalizedTx

from .classify import STABLECOINS

SCAM_TEXT = re.compile(
    r"(https?://|www\.|\.(com|io|xyz|org|net|app|site|club|top|live|pro|fi|gg|me|co)\b|t\.me|"
    r"\bclaim|\bvisit\b|\breward|\bairdrop|\bbonus\b|\bvoucher|\bgiveaway|\bfree\b|"
    r"\$\s?\d|✅|🎁|🔥)",
    re.IGNORECASE,
)


def looks_like_scam(text: str | None) -> bool:
    return bool(text and SCAM_TEXT.search(text))


def mark_spam(txs: list[NormalizedTx]) -> set[str]:
    """Set asset.spam on every leg of a spam asset. Returns the spam keys.
    Run after pricing, since rule 3 depends on whether a price was found."""
    sent: set[str] = set()
    engaged: set[str] = set()   # appeared in a tx the user initiated (a trade or paid fee)
    priced: set[str] = set()
    flagged: set[str] = set()
    sample = {}
    for t in txs:
        user_initiated = bool(t.fee) or any(l.amount < 0 for l in t.legs)
        for l in t.legs:
            k = l.asset.key
            sample[k] = l.asset
            if user_initiated:
                engaged.add(k)
            if l.amount < 0:
                sent.add(k)
            if l.usd_value is not None and not l.asset.is_nft:
                priced.add(k)
            if l.asset.spam or looks_like_scam(l.asset.symbol) or looks_like_scam(l.asset.name):
                flagged.add(k)

    for k, a in sample.items():
        unsolicited = (not a.is_nft and a.contract is not None and k not in priced
                       and k not in sent and k not in engaged and a.symbol.upper() not in STABLECOINS)
        if unsolicited:
            flagged.add(k)

    for t in txs:
        for l in t.legs:
            if l.asset.key in flagged and not l.asset.spam:
                l.asset = replace(l.asset, spam=True)
    return flagged


def is_spam_tx(t: NormalizedTx) -> bool:
    """A transaction is hidden as spam when every asset in it is spam and the
    user paid no fee (they didn't initiate it)."""
    return bool(t.legs) and all(l.asset.spam for l in t.legs) and not t.fee


def without_spam(t: NormalizedTx) -> list:
    return [l for l in t.legs if not l.asset.spam]


def counts(txs: list[NormalizedTx]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    seen = set()
    for t in txs:
        if is_spam_tx(t) and t.tx_hash not in seen:
            seen.add(t.tx_hash)
            out["transactions"] += 1
    out["assets"] = len({l.asset.key for t in txs for l in t.legs if l.asset.spam})
    return dict(out)
