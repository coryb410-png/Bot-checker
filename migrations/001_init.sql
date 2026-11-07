-- ChainScope schema (PostgreSQL 15+)
-- Matches the in-memory model in backend/core/models.py and backend/portfolio/.

CREATE TABLE blockchains (
    id          TEXT PRIMARY KEY,               -- 'bitcoin' | 'ethereum' | 'solana'
    native_symbol TEXT NOT NULL,
    explorer_tx_url TEXT NOT NULL
);
INSERT INTO blockchains VALUES
  ('bitcoin',  'BTC', 'https://mempool.space/tx/'),
  ('ethereum', 'ETH', 'https://etherscan.io/tx/'),
  ('solana',   'SOL', 'https://solscan.io/tx/');

-- A portfolio groups one or more public addresses.
CREATE TABLE portfolios (
    id          TEXT PRIMARY KEY,
    user_id     UUID,                           -- NULL for anonymous scans
    cost_method TEXT NOT NULL DEFAULT 'fifo' CHECK (cost_method IN ('fifo','average')),
    status      TEXT NOT NULL DEFAULT 'queued',
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ                     -- anonymous portfolios are purged after this
);

CREATE TABLE wallets (
    id          BIGSERIAL PRIMARY KEY,
    portfolio_id TEXT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    chain       TEXT NOT NULL REFERENCES blockchains(id),
    address     TEXT NOT NULL,
    label       TEXT,
    sync_cursor TEXT,                           -- newest tx hash already stored
    last_synced_at TIMESTAMPTZ,
    UNIQUE (portfolio_id, chain, address)
);
CREATE INDEX wallets_address_idx ON wallets (chain, address);

CREATE TABLE assets (
    key         TEXT PRIMARY KEY,               -- see Asset.key
    chain       TEXT NOT NULL REFERENCES blockchains(id),
    symbol      TEXT NOT NULL,
    name        TEXT,
    contract    TEXT,
    decimals    INT NOT NULL,
    is_nft      BOOLEAN NOT NULL DEFAULT false,
    token_id    TEXT,
    coingecko_id TEXT
);

-- Raw chain transactions are shared across portfolios (same address, same data).
CREATE TABLE transactions (
    chain       TEXT NOT NULL REFERENCES blockchains(id),
    hash        TEXT NOT NULL,
    wallet      TEXT NOT NULL,                  -- the address whose view this is
    block_time  TIMESTAMPTZ NOT NULL,
    fee         NUMERIC(38,18) NOT NULL DEFAULT 0,
    fee_asset   TEXT REFERENCES assets(key),
    fee_usd     NUMERIC(24,2),
    source      TEXT,
    interacted_with TEXT,
    success     BOOLEAN NOT NULL DEFAULT true,
    PRIMARY KEY (chain, hash, wallet)
);
CREATE INDEX transactions_wallet_time_idx ON transactions (chain, wallet, block_time DESC);

CREATE TABLE token_transfers (
    id          BIGSERIAL PRIMARY KEY,
    chain       TEXT NOT NULL,
    tx_hash     TEXT NOT NULL,
    wallet      TEXT NOT NULL,
    asset_key   TEXT NOT NULL REFERENCES assets(key),
    amount      NUMERIC(78,18) NOT NULL,         -- signed, from the wallet's view
    counterparty TEXT,
    usd_value   NUMERIC(24,2),
    FOREIGN KEY (chain, tx_hash, wallet) REFERENCES transactions(chain, hash, wallet) ON DELETE CASCADE
);
CREATE INDEX token_transfers_tx_idx ON token_transfers (chain, tx_hash, wallet);

-- Classification depends on which addresses share a portfolio, so it is per portfolio.
CREATE TABLE transaction_classifications (
    portfolio_id TEXT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    chain       TEXT NOT NULL,
    tx_hash     TEXT NOT NULL,
    wallet      TEXT NOT NULL,
    tx_type     TEXT NOT NULL,
    label       TEXT NOT NULL,
    user_override BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (portfolio_id, chain, tx_hash, wallet)
);

CREATE TABLE historical_prices (
    coingecko_id TEXT NOT NULL,
    day         DATE NOT NULL,
    usd         NUMERIC(38,12) NOT NULL,
    PRIMARY KEY (coingecko_id, day)
);

CREATE TABLE cost_basis_lots (
    id          BIGSERIAL PRIMARY KEY,
    portfolio_id TEXT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    asset_key   TEXT NOT NULL REFERENCES assets(key),
    qty_remaining NUMERIC(78,18) NOT NULL,
    unit_cost_usd NUMERIC(38,12),               -- NULL = unknown basis
    acquired_at TIMESTAMPTZ NOT NULL,
    source_tx   TEXT NOT NULL
);

CREATE TABLE manual_cost_basis (
    portfolio_id TEXT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    tx_hash     TEXT NOT NULL,
    total_cost_usd NUMERIC(24,2) NOT NULL,
    PRIMARY KEY (portfolio_id, tx_hash)
);

CREATE TABLE wallet_holdings (
    portfolio_id TEXT NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    asset_key   TEXT NOT NULL REFERENCES assets(key),
    quantity    NUMERIC(78,18) NOT NULL,
    cost_basis_usd NUMERIC(24,2),
    unknown_basis_qty NUMERIC(78,18) NOT NULL DEFAULT 0,
    as_of       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (portfolio_id, asset_key)
);
