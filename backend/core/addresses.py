"""Address detection and validation for Bitcoin, Ethereum and Solana.

Validation is structural (checksums where the format has one), done before any
API call so typos never cost a provider request.
"""
from __future__ import annotations

import hashlib
import re

from .models import Chain

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B58_INDEX = {c: i for i, c in enumerate(B58_ALPHABET)}
BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BECH32_CONST, BECH32M_CONST = 1, 0x2BC830A3


class InvalidAddress(ValueError):
    pass


def b58decode(s: str) -> bytes:
    n = 0
    for c in s:
        if c not in B58_INDEX:
            raise InvalidAddress(f"invalid base58 character '{c}'")
        n = n * 58 + B58_INDEX[c]
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def _bech32_polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def _bech32_verify(addr: str) -> bool:
    if addr.lower() != addr and addr.upper() != addr:
        return False
    addr = addr.lower()
    pos = addr.rfind("1")
    if pos < 1 or pos + 7 > len(addr) or len(addr) > 90:
        return False
    hrp, data = addr[:pos], addr[pos + 1:]
    if any(c not in BECH32_CHARSET for c in data):
        return False
    values = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    values += [BECH32_CHARSET.find(c) for c in data]
    const = _bech32_polymod(values)
    witver = BECH32_CHARSET.find(data[0])
    # v0 (bc1q…) uses bech32, v1+ (taproot bc1p…) uses bech32m
    return const == (BECH32_CONST if witver == 0 else BECH32M_CONST)


def is_bitcoin(addr: str) -> bool:
    if addr.lower().startswith("bc1"):
        return _bech32_verify(addr)
    if addr[:1] in ("1", "3") and 25 <= len(addr) <= 35:
        try:
            raw = b58decode(addr)
        except InvalidAddress:
            return False
        if len(raw) != 25:
            return False
        payload, checksum = raw[:-4], raw[-4:]
        return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == checksum and raw[0] in (0x00, 0x05)
    return False


_ETH_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def is_ethereum(addr: str) -> bool:
    # Structural check. Mixed-case EIP-55 checksums are verified by the
    # frontend (viem/ethers) before submission; the backend lower-cases.
    return bool(_ETH_RE.match(addr))


def is_solana(addr: str) -> bool:
    if not 32 <= len(addr) <= 44:
        return False
    try:
        return len(b58decode(addr)) == 32
    except InvalidAddress:
        return False


def detect_chain(addr: str) -> Chain:
    addr = addr.strip()
    if is_ethereum(addr):
        return Chain.ETHEREUM
    if is_bitcoin(addr):
        return Chain.BITCOIN
    if is_solana(addr):
        return Chain.SOLANA
    raise InvalidAddress("Not a valid Bitcoin, Ethereum or Solana address")


def normalize(addr: str, chain: Chain) -> str:
    addr = addr.strip()
    if chain == Chain.ETHEREUM:
        return addr.lower()
    if chain == Chain.BITCOIN and addr.lower().startswith("bc1"):
        return addr.lower()
    return addr  # base58 is case-sensitive


def looks_like_secret(text: str) -> bool:
    """Catch people pasting seed phrases or private keys and refuse them."""
    words = text.strip().split()
    if len(words) in (12, 15, 18, 21, 24) and all(w.isalpha() and w.islower() for w in words):
        return True
    t = text.strip().removeprefix("0x")
    if re.fullmatch(r"[0-9a-fA-F]{64}", t):  # raw 32-byte private key
        return True
    if text.strip()[:1] in ("5", "K", "L") and len(text.strip()) in (51, 52):  # BTC WIF
        return True
    if 86 <= len(text.strip()) <= 90:  # Solana 64-byte secret key in base58
        return True
    return False
