"""Resolve human-readable wallet names to addresses.

  name.eth  → Ethereum address, read directly from the ENS registry on-chain
              through your Alchemy endpoint (no extra service).
  name.sol  → Solana address, via the Bonfida SNS resolver API
              (configurable with SNS_RESOLVER_URL).

Not covered yet: ENS names that resolve off-chain (e.g. cb.id, many
subnames on L2s) and full ENSIP-15 normalization of emoji names.
"""
from __future__ import annotations

import re

from scanners.base import Http, ProviderError, env

from .addresses import is_ethereum, is_solana
from .keccak import keccak256
from .models import Chain

ENS_REGISTRY = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"
SEL_RESOLVER = "0178b8bf"   # resolver(bytes32)
SEL_ADDR = "3b3b57de"       # addr(bytes32)
ZERO = "0x" + "0" * 40

NAME_RE = re.compile(r"^[^\s/\\]{1,250}\.(eth|sol)$", re.IGNORECASE)


class NameNotFound(ValueError):
    pass


def is_name(text: str) -> bool:
    return bool(NAME_RE.match(text.strip()))


def namehash(name: str) -> bytes:
    node = b"\x00" * 32
    if name:
        for label in reversed(name.split(".")):
            node = keccak256(node + keccak256(label.encode("utf-8")))
    return node


class NameResolver:
    def __init__(self, eth: Http | None = None, sns: Http | None = None):
        self._eth = eth
        self._sns = sns

    @property
    def eth(self) -> Http:
        if self._eth is None:
            self._eth = Http(f"https://eth-mainnet.g.alchemy.com/v2/{env('ALCHEMY_API_KEY')}", rate_per_sec=10)
        return self._eth

    @property
    def sns(self) -> Http:
        if self._sns is None:
            self._sns = Http(env("SNS_RESOLVER_URL", "https://sns-sdk-proxy.bonfida.workers.dev"), rate_per_sec=5)
        return self._sns

    async def resolve(self, name: str) -> tuple[Chain, str]:
        name = name.strip().lower()
        if not is_name(name):
            raise NameNotFound("Enter a name ending in .eth or .sol")
        if name.endswith(".eth"):
            return Chain.ETHEREUM, await self._ens(name)
        return Chain.SOLANA, await self._sol(name)

    async def _call(self, to: str, data: str) -> str:
        res = await self.eth.post("", json={"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                                            "params": [{"to": to, "data": data}, "latest"]})
        if "error" in res:
            raise ProviderError(str(res["error"]))
        return res.get("result") or "0x"

    async def _ens(self, name: str) -> str:
        node = namehash(name).hex()
        resolver = _word_to_address(await self._call(ENS_REGISTRY, "0x" + SEL_RESOLVER + node))
        if resolver == ZERO:
            raise NameNotFound(f"{name} isn't registered or has no resolver")
        addr = _word_to_address(await self._call(resolver, "0x" + SEL_ADDR + node))
        if addr == ZERO or not is_ethereum(addr):
            raise NameNotFound(f"{name} doesn't point to an Ethereum address")
        return addr

    async def _sol(self, name: str) -> str:
        label = name[: -len(".sol")]
        try:
            res = await self.sns.get(f"/resolve/{label}")
        except ProviderError:
            raise NameNotFound(f"{name} isn't registered")
        addr = res.get("result") if isinstance(res, dict) and res.get("s") == "ok" else None
        if not addr or not is_solana(addr):
            raise NameNotFound(f"{name} isn't registered")
        return addr


def _word_to_address(hexdata: str) -> str:
    h = hexdata.removeprefix("0x")
    if len(h) < 64:
        return ZERO
    return "0x" + h[24:64].lower()
