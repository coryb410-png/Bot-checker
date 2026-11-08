import asyncio
import sys
import types
from decimal import Decimal as D

if "httpx" not in sys.modules:
    try:
        import httpx  # noqa: F401
    except ImportError:
        stub = types.ModuleType("httpx"); stub.AsyncClient = object; stub.TransportError = Exception
        sys.modules["httpx"] = stub

from classifiers.classify import classify_all  # noqa: E402
from core.addresses import detect_chain  # noqa: E402
from core.models import Chain, TxType  # noqa: E402
from scanners.base import ProviderError  # noqa: E402
from scanners.dogecoin import DOGE, DogecoinScanner  # noqa: E402

ME = "DH5yaieqoZN36fDVciNyRueRGvGLR3mr7L"
FRIEND = "D6hLULEGDRbk86j58t5iWmeinqM6acA16V"


def esplora_tx(txid, t, vin, vout, fee):
    return {"txid": txid, "status": {"block_time": t}, "fee": fee,
            "vin": [{"prevout": {"scriptpubkey_address": a, "value": v}} for a, v in vin],
            "vout": [{"scriptpubkey_address": a, "value": v} for a, v in vout]}


class Esplora:
    def __init__(self, fail=False):
        self.fail = fail

    async def get(self, path, params=None):
        if self.fail:
            raise ProviderError("502 from explorer")
        assert path == f"/address/{ME}/txs/chain"
        return [
            esplora_tx("send", 1730100000, [(ME, 5_000_000_000)], [(FRIEND, 2_000_000_000), (ME, 2_990_000_000)], 10_000_000),
            esplora_tx("recv", 1730000000, [(FRIEND, 9_000_000_000)], [(ME, 5_000_000_000), (FRIEND, 3_990_000_000)], 10_000_000),
        ]


class BlockCypher:
    def __init__(self):
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        return {"hasMore": False, "txs": [
            {"hash": "bc-send", "block_height": 2, "confirmed": "2024-10-28T08:00:00Z", "fees": 10_000_000,
             "inputs": [{"addresses": [ME], "output_value": 5_000_000_000}],
             "outputs": [{"addresses": [FRIEND], "value": 2_000_000_000}, {"addresses": [ME], "value": 2_990_000_000}]},
            {"hash": "pending", "block_height": -1, "inputs": [], "outputs": []},
        ]}


def collect(sc):
    async def go():
        return [t async for t in sc.scan(ME)]
    return asyncio.run(go())


def test_detects_dogecoin():
    assert detect_chain(ME) == Chain.DOGECOIN


def test_scan_via_explorer():
    txs = collect(DogecoinScanner(http=Esplora(), backup=BlockCypher()))
    classify_all(txs, {ME})
    send, recv = txs
    assert recv.legs[0].asset == DOGE and recv.legs[0].amount == D(50) and recv.fee == 0
    assert send.legs[0].amount == D(-20) and send.fee == D("0.1") and send.fee_asset == DOGE
    assert recv.tx_type == TxType.TRANSFER_IN and send.tx_type == TxType.TRANSFER_OUT


def test_falls_back_to_blockcypher():
    bc = BlockCypher()
    txs = collect(DogecoinScanner(http=Esplora(fail=True), backup=bc))
    assert [t.tx_hash for t in txs] == ["bc-send"]            # unconfirmed skipped
    assert txs[0].legs[0].amount == D(-20) and txs[0].fee == D("0.1")
    assert bc.calls[0][0] == f"/addrs/{ME}/full"


def test_bitcoin_scanner_unchanged():
    from scanners.bitcoin import BTC, BitcoinScanner
    raw = esplora_tx("b", 1730000000, [("x", 300_000)], [("bc1me", 100_000)], 0)
    tx = BitcoinScanner.normalize(raw, "bc1me")
    assert tx.chain == Chain.BITCOIN and tx.legs[0].asset == BTC
