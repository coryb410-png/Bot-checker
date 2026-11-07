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
from core.addresses import detect_chain, is_cardano  # noqa: E402
from core.models import Chain, TxType  # noqa: E402
from scanners.cardano import ADA, CardanoScanner  # noqa: E402

STAKE = "stake1uyehkck0lajq8gr28t9uxnuvgcqrc6070x3k9r8048z8y5gh6ffgw"
A1 = "addr1qx2fxv2umyhttkxyxp8x0dlpdt3k6cwng5pxj3jhsydzer3n0d3vllmyqwsx5wktcd8cc3sq835lu7drv2xwl2wywfgse35a3x"
A2 = "addr1q9secondaddress"            # second address of the same wallet (only matched by stake key)
SHOP = "addr1zjpgstore"                 # marketplace script address
POL = "a" * 56                          # an NFT collection policy
NFT_UNIT = POL + "5370616365427564313233"   # "SpaceBud123"
MIN_UNIT = "29d222ce763455e3d7a09a665ce554f00ac89d2e99a1a83d267170c6" + "4d494e"   # MIN token


def io(addr, lovelace, assets=(), stake=None):
    return {"payment_addr": {"bech32": addr}, "stake_addr": stake, "value": str(lovelace),
            "asset_list": [{"policy_id": u[:56], "asset_name": u[56:], "quantity": str(q), "decimals": None} for u, q in assets]}


TXS = {
    # Buy an NFT for 50 ADA (+ 1.5 ADA min-ADA attached to the NFT), fee 0.3 ADA
    "buy": {"tx_hash": "buy", "tx_timestamp": 1730000000, "fee": "300000", "withdrawals": [],
            "inputs": [io(A1, 100_000_000, stake=STAKE)],
            "outputs": [io(SHOP, 50_000_000), io(A2, 1_500_000, [(NFT_UNIT, 1)], stake=STAKE),
                        io(A1, 48_200_000, stake=STAKE)]},
    # Swap 10 ADA for 250 MIN, also withdrawing 12 ADA of staking rewards
    "swap": {"tx_hash": "swap", "tx_timestamp": 1730100000, "fee": "200000",
             "withdrawals": [{"amount": "12000000", "stake_addr": STAKE}],
             "inputs": [io(A1, 48_200_000, stake=STAKE)],
             "outputs": [io(SHOP, 10_000_000), io(A1, 50_000_000 - 0, [(MIN_UNIT, 250_000_000)], stake=STAKE)]},
}
ASSET_INFO = [
    {"policy_id": POL, "asset_name": NFT_UNIT[56:], "asset_name_ascii": "SpaceBud123", "total_supply": "1",
     "minting_tx_metadata": {"721": {POL: {"SpaceBud123": {"name": "SpaceBud #123", "image": ["ipfs://QmSpace", "Bud123"]}}}}},
    {"policy_id": MIN_UNIT[:56], "asset_name": MIN_UNIT[56:], "asset_name_ascii": "MIN", "total_supply": "3000000000000000",
     "token_registry_metadata": {"ticker": "MIN", "name": "Minswap", "decimals": 6}},
]


class FakeKoios:
    def __init__(self):
        self.calls = []

    async def post(self, url, json=None, params=None):
        self.calls.append(url)
        if url == "/address_info":
            return [{"address": A1, "stake_address": STAKE}]
        if url == "/account_addresses":
            return [{"stake_address": STAKE, "addresses": [A1]}]
        if url == "/tx_info":
            return [TXS[h] for h in json["_tx_hashes"]]
        if url == "/asset_info":
            return ASSET_INFO
        if url == "/account_assets":
            return [{"policy_id": POL, "asset_name": NFT_UNIT[56:], "quantity": "1"},
                    {"policy_id": MIN_UNIT[:56], "asset_name": MIN_UNIT[56:], "quantity": "250000000"}]
        raise AssertionError(url)

    async def get(self, url, params=None):
        self.calls.append(url)
        if url == "/account_txs":
            return [{"tx_hash": "buy", "block_height": 1}, {"tx_hash": "swap", "block_height": 2}]
        if url == "/asset_addresses":
            return [{"payment_address": A1}] if params["_asset_name"].startswith("000de140") else []
        raise AssertionError(url)


def scan(since=None):
    sc = CardanoScanner(http=FakeKoios())

    async def go():
        return [t async for t in sc.scan(A1, since)]
    return sc, asyncio.run(go())


def test_detects_cardano_addresses():
    assert detect_chain(A1) == Chain.CARDANO and detect_chain(STAKE) == Chain.CARDANO
    assert not is_cardano(A1[:-1] + "y")   # checksum


def test_whole_wallet_nft_purchase_swap_and_reward():
    _, txs = scan()
    by = {t.tx_hash: t for t in txs}
    assert [t.tx_hash for t in txs] == ["swap", "swap#reward", "buy"]   # newest first
    classify_all(txs, {A1})

    buy = by["buy"]
    legs = {l.asset.symbol: l for l in buy.legs}
    # Paid 50 ADA to the shop; the 1.5 ADA riding with the NFT landed in the wallet's other address
    assert legs["ADA"].amount == D(-50) and buy.fee == D("0.3") and buy.fee_asset == ADA
    nft = legs["SpaceBud123"].asset
    assert nft.is_nft and nft.name == "SpaceBud #123" and nft.collection == POL
    assert nft.image == "https://ipfs.io/ipfs/QmSpaceBud123"
    assert buy.tx_type == TxType.NFT_PURCHASE

    swap = by["swap"]
    legs = {l.asset.symbol: l for l in swap.legs}
    # 48.2 in, 50 back incl. 12 of rewards, 0.2 fee  ->  10 ADA really spent on MIN
    assert legs["ADA"].amount == D(-10) and legs["MIN"].amount == D(250) and legs["MIN"].asset.decimals == 6
    assert swap.tx_type == TxType.SWAP

    reward = by["swap#reward"]
    assert reward.legs[0].amount == D(12) and reward.tx_type == TxType.TRANSFER_IN
    assert reward.label.startswith("Staking reward")


def test_incremental_scan_stops_at_cursor():
    _, txs = scan(since="buy")
    assert [t.tx_hash for t in txs] == ["swap", "swap#reward"]


def test_reward_cost_basis_is_value_when_received():
    from portfolio.pnl import compute_pnl
    _, txs = scan()
    classify_all(txs, {A1})
    for t in txs:
        for l in t.legs:
            if l.asset == ADA:
                l.usd_value = abs(l.amount) * D("0.5")
    reward = [t for t in txs if t.tx_hash == "swap#reward"]
    r = compute_pnl(reward, current_prices={ADA.key: D("0.5")})
    held = r.assets[ADA.key]
    assert held.quantity == D(12) and held.cost_basis == D(6) and held.unknown_qty == 0


def test_owned_nfts_and_handles():
    sc = CardanoScanner(http=FakeKoios())
    held = asyncio.run(sc.held_nfts(A1))
    assert [(a.name, a.is_nft) for a in held] == [("SpaceBud #123", True)]
    assert asyncio.run(sc.resolve_handle("$madboy")) == A1


def test_handle_names_are_recognised():
    from core.names import is_name
    assert is_name("$madboy") and is_name("madboy.sol") and not is_name("$") and not is_name("madboy")
