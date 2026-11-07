"""Chain-agnostic data model.

Every scanner converts raw chain data into NormalizedTx objects. Everything
downstream (classification, pricing, P&L) works only on this model, so adding
a new chain means writing one adapter and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Chain(str, Enum):
    BITCOIN = "bitcoin"
    ETHEREUM = "ethereum"
    SOLANA = "solana"
    ROBINHOOD = "robinhood"   # Robinhood Chain, an Ethereum L2 (chain ID 4663)


class TxType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    SWAP = "swap"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    INTERNAL_TRANSFER = "internal_transfer"
    BRIDGE = "bridge"
    NFT_PURCHASE = "nft_purchase"
    NFT_SALE = "nft_sale"
    NETWORK_FEE = "network_fee"
    CONTRACT_INTERACTION = "contract_interaction"


@dataclass(frozen=True)
class Asset:
    """An asset is identified by chain + contract (None for the native coin)."""
    chain: Chain
    symbol: str
    contract: str | None = None      # ERC-20 address / SPL mint / None for native
    decimals: int = 18
    is_nft: bool = False
    token_id: str | None = None      # NFTs only
    coingecko_id: str | None = None  # resolved by the pricing layer
    # Display-only metadata, excluded from equality so it never splits lots
    name: str | None = field(default=None, compare=False)
    image: str | None = field(default=None, compare=False)
    spam: bool = field(default=False, compare=False)
    compressed: bool = field(default=False, compare=False)   # Solana compressed NFT (cNFT)
    collection: str | None = field(default=None, compare=False)  # collection address (NFTs)

    @property
    def key(self) -> str:
        """Stable identifier used for cost-basis lots.

        Native coins share one key across chains where it makes sense
        (e.g. ETH on mainnet), so internal transfers keep their lots.
        """
        if self.is_nft:
            return f"{self.chain.value}:nft:{self.contract}:{self.token_id}"
        if self.contract is None:
            return f"native:{self.symbol.upper()}"
        return f"{self.chain.value}:{self.contract.lower() if self.chain != Chain.SOLANA else self.contract}"


def safe_image(url: str | None) -> str | None:
    """Return a browser-safe https image URL, or None.

    NFT metadata is written by whoever minted the token, so only plain
    web/IPFS/Arweave links are allowed through (no data:, javascript:, etc.).
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if url.startswith("ipfs://"):
        path = url[len("ipfs://"):].removeprefix("ipfs/")
        return f"https://ipfs.io/ipfs/{path}"
    if url.startswith("ar://"):
        return f"https://arweave.net/{url[len('ar://'):]}"
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    return url if url.startswith("https://") and len(url) < 2048 else None


@dataclass
class Leg:
    """One asset movement inside a transaction, from the tracked wallet's view.

    amount > 0 means the wallet received it, amount < 0 means it was sent.
    """
    asset: Asset
    amount: Decimal
    counterparty: str | None = None
    usd_value: Decimal | None = None   # filled by pricing; absolute value


@dataclass
class NormalizedTx:
    chain: Chain
    tx_hash: str
    timestamp: datetime
    wallet: str                       # the tracked address this view belongs to
    legs: list[Leg] = field(default_factory=list)
    fee: Decimal = Decimal(0)         # in native units, paid by this wallet
    fee_asset: Asset | None = None
    fee_usd: Decimal | None = None
    # Hints supplied by the scanner (program/protocol names, contract targets)
    source: str | None = None         # e.g. "JUPITER", "UNISWAP", "OPENSEA"
    interacted_with: str | None = None
    success: bool = True
    # Marketplace activity the scanner recognised that moves no balance of
    # its own: "nft_listing", "nft_cancel_listing", "nft_bid", "nft_cancel_bid"
    hint: str | None = None
    programs: list[str] = field(default_factory=list)   # every program/contract the tx touched
    # Set by the classifier
    tx_type: TxType | None = None
    label: str | None = None

    @property
    def sent(self) -> list[Leg]:
        return [l for l in self.legs if l.amount < 0]

    @property
    def received(self) -> list[Leg]:
        return [l for l in self.legs if l.amount > 0]
