import asyncio
import sys
import types
from datetime import datetime, timezone
from decimal import Decimal as D

if "httpx" not in sys.modules:
    try:
        import httpx  # noqa: F401
    except ImportError:
        stub = types.ModuleType("httpx"); stub.AsyncClient = object; stub.TransportError = Exception
        sys.modules["httpx"] = stub

import pytest  # noqa: E402

from classifiers.spam import is_spam_tx, looks_like_scam, mark_spam  # noqa: E402
from core.models import Asset, Chain, Leg, NormalizedTx  # noqa: E402
from core.names import NameNotFound, NameResolver, is_name, namehash  # noqa: E402

ME = "0x1111111111111111111111111111111111111111"
T = datetime(2026, 1, 1, tzinfo=timezone.utc)
ETH = Asset(Chain.ETHEREUM, "ETH")


def tok(sym, addr, name=None):
    return Asset(Chain.ETHEREUM, sym, contract=addr, name=name)


def tx(h, legs, fee=D(0)):
    return NormalizedTx(Chain.ETHEREUM, h, T, ME, legs, fee, ETH if fee else None)


# ---------------------------------------------------------------- spam
def test_scam_text():
    assert looks_like_scam("Visit uniswap-claim.com")
    assert looks_like_scam("$5000 USDC Reward")
    assert looks_like_scam("t.me/freetokens")
    assert not looks_like_scam("Pepe")
    assert not looks_like_scam("yearn.finance")


def test_spam_rules():
    junk = tok("JUNK", "0xjunk")                     # unpriced, unsolicited
    scam = tok("ETH", "0xscam", "Claim at eth-gift.xyz")  # scam text, even if priced
    gem = tok("GEM", "0xgem")                        # unpriced but the user bought it
    pepe = tok("PEPE", "0xpepe")                     # priced airdrop: kept
    txs = [
        tx("a", [Leg(junk, D(1000))]),
        tx("b", [Leg(scam, D(1), usd_value=D(3000))]),
        tx("c", [Leg(ETH, D(-1), usd_value=D(3000)), Leg(gem, D(50))], fee=D("0.001")),
        tx("d", [Leg(pepe, D(10), usd_value=D(5))]),
    ]
    flagged = mark_spam(txs)
    assert flagged == {junk.key, scam.key}
    assert [is_spam_tx(t) for t in txs] == [True, True, False, False]


def test_example_wallet_hides_spam():
    from portfolio.demo import build_demo
    p = build_demo("demo-x")
    spam = [t for t in p.txs if is_spam_tx(t)]
    assert len(spam) == 1
    assert all(a.symbol != "Visit claim-eth.xyz" for a in p.report.assets.values())


# ---------------------------------------------------------------- names
def test_is_name():
    assert is_name("vitalik.eth") and is_name("Bonfida.SOL")
    assert not is_name("0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045")
    assert not is_name("vitalik")


def test_namehash_vectors():
    # From EIP-137
    assert namehash("").hex() == "00" * 32
    assert namehash("eth").hex() == "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"
    assert namehash("foo.eth").hex() == "de9b09fd7c5f901e23a3f19fecc54828e9c848539801e86591bd9801b019f84f"


class FakeHttp:
    def __init__(self, responder):
        self.responder, self.calls = responder, []

    async def post(self, url, json=None):
        self.calls.append(json)
        return self.responder(json)

    async def get(self, url, **kw):
        self.calls.append(url)
        return self.responder(url)


def test_ens_resolution():
    resolver = "0x231b0ee14048e9dccd1d247744d114a4eb5e8e63"
    target = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"

    def rpc(body):
        to, data = body["params"][0]["to"], body["params"][0]["data"]
        assert data.endswith(namehash("vitalik.eth").hex())
        word = resolver if data.startswith("0x0178b8bf") else target
        return {"result": "0x" + "0" * 24 + word[2:]}

    eth = FakeHttp(rpc)
    chain, addr = asyncio.run(NameResolver(eth=eth, sns=object()).resolve("Vitalik.eth"))
    assert (chain, addr) == (Chain.ETHEREUM, target)
    assert eth.calls[1]["params"][0]["to"] == resolver


def test_ens_unregistered():
    eth = FakeHttp(lambda b: {"result": "0x" + "0" * 64})
    with pytest.raises(NameNotFound):
        asyncio.run(NameResolver(eth=eth, sns=object()).resolve("nobody-here-12345.eth"))


def test_sns_resolution():
    owner = "HKKp49qGWXd639QsuH7JiLijfVW5UtCVY4s1n2HANwEA"
    sns = FakeHttp(lambda url: {"s": "ok", "result": owner} if url == "/resolve/bonfida" else {"s": "error"})
    assert asyncio.run(NameResolver(eth=object(), sns=sns).resolve("bonfida.sol")) == (Chain.SOLANA, owner)
    with pytest.raises(NameNotFound):
        asyncio.run(NameResolver(eth=object(), sns=sns).resolve("missing.sol"))


def test_bought_collections_are_never_spam():
    from core.models import Chain
    sol = Asset(Chain.SOLANA, "SOL")
    bought = Asset(Chain.SOLANA, "WGNETS", contract="w1", is_nft=True, token_id="w1", spam=True)   # DAS flagged
    gifted = Asset(Chain.SOLANA, "WGNETS", contract="w2", is_nft=True, token_id="w2", spam=True)   # same collection
    scam = Asset(Chain.SOLANA, "CLAIM", contract="s1", is_nft=True, token_id="s1", spam=True)
    txs = [
        NormalizedTx(Chain.SOLANA, "buy", T, "me", [Leg(sol, D(-1)), Leg(bought, D(1))], D("0.00001"), sol),
        NormalizedTx(Chain.SOLANA, "gift", T, "me", [Leg(gifted, D(1))]),
        NormalizedTx(Chain.SOLANA, "drop", T, "me", [Leg(scam, D(1))]),
    ]
    flagged = mark_spam(txs)
    assert flagged == {scam.key}
    assert [is_spam_tx(t) for t in txs] == [False, False, True]


def test_links_alone_are_not_scams():
    from classifiers.spam import looks_like_scam_description
    assert not looks_like_scam_description("Wegen Genesis. Learn more at wegen.xyz")
    assert looks_like_scam_description("You are eligible! Claim 500 USDC at sol-drop.xyz")
