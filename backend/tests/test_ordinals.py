"""Bitcoin Ordinals as NFTs: Xverse (names, floors) or ordinals.com (free)."""
import asyncio
from decimal import Decimal

from core.models import Chain
from scanners import ordinals as O

IMG = "77c5a00da7dcf2c8f965effd25dda16ec8ec8d6b8937e89bbbdf10a1dc5aeb0di0"
HTML = "4fd271181e901809f6e2d5f89ce95ddfeb886f8db1582a35c812401af8e77661i42"
BRC = "1417086d6abf96f68287b799b13b0081ec895d0b4a5fb7b70d2fde404eeb8aa1i0"


class OrdHttp:
    def __init__(self):
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append(("GET", path))
        assert path == "/address/bc1pme"
        return {"outputs": ["x:0"], "inscriptions": [IMG, HTML, BRC], "sat_balance": 1638}

    async def post(self, path, json=None):
        self.calls.append(("POST", path, tuple(json)))
        assert path == "/inscriptions"
        return [
            {"id": IMG, "number": 69994606, "content_type": "image/avif", "effective_content_type": "image/avif",
             "parents": ["b1ef66c2d1a047cbaa6260b74daac43813924378fe08ef8545da4cb79e8fcf00i0"]},
            {"id": HTML, "number": 12, "content_type": "text/html;charset=utf-8", "parents": []},
            {"id": BRC, "number": 3, "content_type": "text/plain;charset=utf-8", "parents": []},   # a BRC-20 transfer
        ]


def test_ordinals_from_ordinals_com(monkeypatch):
    monkeypatch.delenv("XVERSE_API_KEY", raising=False)
    http = OrdHttp()
    nfts = asyncio.run(O.OrdinalsClient(ord_http=http).held("bc1pme"))
    assert [a.token_id for a in nfts] == [IMG, HTML]                      # text inscriptions left out
    img, html = nfts
    assert img.chain == Chain.BITCOIN and img.is_nft and img.name == "Inscription #69994606"
    assert img.image == f"https://ordinals.com/content/{IMG}"
    assert img.collection == "b1ef66c2d1a047cbaa6260b74daac43813924378fe08ef8545da4cb79e8fcf00i0"   # grouped by parent
    assert html.image is None and html.collection == "ordinals"
    assert http.calls[1] == ("POST", "/inscriptions", (IMG, HTML, BRC))


def test_ordinals_from_xverse_with_floors(monkeypatch):
    class XHttp:
        async def get(self, path, params=None):
            assert path == "/v1/ordinals/address/bc1pme/inscriptions"
            return {"limit": 2, "offset": 0, "items": [
                {"id": IMG, "number": 69994606, "contentType": "image/webp", "collectionId": "nodemonkes",
                 "collectionName": "NodeMonkes", "collectionFloorPrice": {"valueInSats": 2500000, "valueInUsd": 2400},
                 "contentUrl": "https://x/c", "renderUrl": "https://x/r"},
                {"id": BRC, "number": 3, "contentType": "text/plain", "collectionId": None}]}

    O.FLOORS.clear()
    (nft,) = asyncio.run(O.OrdinalsClient(xverse=XHttp(), ord_http=OrdHttp()).held("bc1pme"))
    assert nft.name == "NodeMonkes #69994606" and nft.collection == "nodemonkes" and nft.symbol == "NodeMonkes"
    assert O.FLOORS["nodemonkes"] == Decimal("0.025")

    from pricing.floors import FloorService, collection_key
    floors = asyncio.run(FloorService().floors_for([nft]))
    f = floors[collection_key(nft)]
    assert f.currency == "BTC" and f.amount == Decimal("0.025") and f.source == "Xverse"


def test_xverse_failure_falls_back_to_ordinals_com():
    from scanners.base import ProviderError

    class Down:
        async def get(self, path, params=None):
            raise ProviderError("api.secretkeylabs.io 401: bad key")

    nfts = asyncio.run(O.OrdinalsClient(xverse=Down(), ord_http=OrdHttp()).held("bc1pme"))
    assert len(nfts) == 2


def test_bitcoin_wallets_get_ordinals_in_scans(monkeypatch):
    from portfolio import service
    from portfolio.pnl import Method
    from core.models import Asset

    class Btc:
        async def held_nfts(self, owner):
            return [Asset(Chain.BITCOIN, "ORDINAL", contract=IMG, decimals=0, is_nft=True, token_id=IMG,
                          collection="ordinals", name="Inscription #1", image="https://ordinals.com/content/x")]

    monkeypatch.setattr(service, "_scanner", lambda c: Btc() if c == Chain.BITCOIN else object())
    p = service.Store().create(["bc1pdrm7tcyk4k6c3cdcjwkp49jmfrwmtvt0dvqyy7y4qp79tgks4lmqdpj6rw"], Method.FIFO)
    asyncio.run(service._find_untracked_nfts(p))
    assert [a.name for a in p.untracked_nfts] == ["Inscription #1"]
