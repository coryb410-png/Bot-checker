"""OpenSea API v2: the NFTs a wallet holds, with pictures from OpenSea's own
image servers (i.seadn.io), which load fast and reliably. The links in many
NFTs' own metadata point at public IPFS gateways that are often slow or
refuse requests.

Needs OPENSEA_API_KEY. Used to give Robinhood Chain NFTs their pictures (and
to fill any picture gaps on the other EVM networks). Read-only.
"""
from __future__ import annotations

from core.models import Asset, Chain, safe_image

from .base import Http, env

CHAINS = {Chain.ETHEREUM: "ethereum", Chain.ROBINHOOD: "robinhood", Chain.BASE: "base",
          Chain.ARBITRUM: "arbitrum", Chain.OPTIMISM: "optimism", Chain.POLYGON: "polygon"}
MAX_PAGES = 25   # 200 per page: up to 5,000 NFTs per wallet and network


def enabled() -> bool:
    return bool(env("OPENSEA_API_KEY", ""))


def picture(nft: dict) -> str | None:
    return safe_image(nft.get("display_image_url") or nft.get("image_url"))


def is_cdn(url: str | None) -> bool:
    return bool(url) and "seadn.io" in url


class OpenSea:
    def __init__(self, http: Http | None = None):
        self.http = http or Http(env("OPENSEA_API_URL", "https://api.opensea.io/api/v2"), rate_per_sec=3,
                                 headers={"x-api-key": env("OPENSEA_API_KEY", ""), "accept": "application/json"})

    async def account_nfts(self, chain: Chain, owner: str) -> dict[tuple[str, str], dict]:
        """(contract, token id) -> OpenSea's NFT record, for everything the wallet holds."""
        slug = CHAINS[chain]
        out, params = {}, {"limit": 200}
        for _ in range(MAX_PAGES):
            res = await self.http.get(f"/chain/{slug}/account/{owner}/nfts", params=params)
            for n in res.get("nfts") or []:
                contract, tid = (n.get("contract") or "").lower(), str(n.get("identifier") or "")
                if contract and tid:
                    out[(contract, tid)] = n
            nxt = res.get("next")
            if not nxt:
                break
            params = {"limit": 200, "next": nxt}
        return out


def as_asset(chain: Chain, contract: str, tid: str, n: dict) -> Asset:
    """An NFT OpenSea says the wallet holds that the chain lookup didn't return."""
    coll = n.get("collection") or "NFT"
    name = (n.get("name") or "").strip() or f"{coll} #{tid}"
    return Asset(chain, "NFT", contract=contract, decimals=0, is_nft=True, token_id=tid, collection=contract,
                 name=name, image=picture(n), spam=bool(n.get("is_disabled")))
