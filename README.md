# ChainScope

Read-only crypto history for Bitcoin, Ethereum, Solana and Robinhood Chain. Paste public addresses,
get every purchase, sale, swap and transfer with historical USD values, holdings
and realized/unrealized profit and loss. No wallet connection, signatures or keys.

## What's in here

```
chainscope/
├── backend/                 Python · FastAPI
│   ├── core/                Chain-agnostic data model + address validation
│   ├── scanners/            bitcoin.py (mempool.space), ethereum.py (Alchemy), solana.py (Helius)
│   ├── classifiers/         Buy / sell / swap / transfer / bridge / NFT / internal-transfer rules
│   ├── pricing/             CoinGecko historical + current prices
│   ├── portfolio/           pnl.py (FIFO + average cost engine), service.py (scan jobs)
│   ├── api/main.py          All endpoints
│   └── tests/               39 tests: addresses, classification, P&L, provider parsing
├── frontend/                Next.js 14 · address entry + dashboard
├── migrations/001_init.sql  PostgreSQL schema
├── docker-compose.yml
└── .env.example
```

## Run it

1. Get free API keys: **Alchemy** (Ethereum) and **Helius** (Solana). Bitcoin
   needs no key. A **CoinGecko** demo key covers about a year of price history;
   all-time history needs their paid plan.
2. `cp .env.example .env` and paste the keys in.
3. Either:
   - **Docker:** `docker compose up --build` → open http://localhost:3000
   - **By hand:**
     ```bash
     cd backend && pip install -r requirements.txt && uvicorn api.main:app --reload
     cd frontend && npm install && npm run dev
     ```
4. Tests: `cd backend && pytest`

**Testing a deployment before adding keys:** tap **Try an example wallet** on the home
page. It runs the real classifier and P&L engine on an invented portfolio without
calling any provider, so it works even with no API keys set.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/resolve?name=` | `name.eth` or `name.sol` → `{chain, address}` |
| `POST /api/demo` | Create an example wallet (invented data, no provider calls) → `{id}` |
| `POST /api/wallets/scan` | Submit addresses or names `{addresses: [...], method: "fifo"\|"average", labels?: {address: name}}` → `{id}` |
| `GET /api/wallets/{id}` | Scan status, progress per address, summary figures |
| `POST /api/wallets/{id}/refresh` | Incremental rescan (only fetches new transactions) |
| `DELETE /api/wallets/{id}` | Remove the portfolio |
| `GET /api/wallets/{id}/transactions` | Timeline; filter with `type`, `chain`, `nft=true`, `include_spam=true` (spam is hidden by default); page with `offset`/`limit` (max 500). NFT legs include `name`, `image`, `spam` |
| `GET /api/wallets/{id}/holdings` | Current holdings, value, cost basis, unrealized P&L |
| `GET /api/wallets/{id}/pnl?method=` | Realized P&L per asset and per sale; switches method |
| `PUT /api/wallets/{id}/cost-basis` | User enters what they paid for an incoming transfer |
| `GET /api/wallets/{id}/performance` | Cumulative realized P&L over time |
| `GET /api/transactions/{hash}?portfolio=` | One transaction |

## How the numbers work

- **Trades** (buy, sell, swap, NFT buy/sale) dispose of everything sent, valued at
  what was received, and create new lots for what came in. Trading fees reduce proceeds.
- **Gas** is removed from your native-coin holdings when paid.
- **Moves between your own addresses** are detected and never count as sales; the
  original purchase price follows the coins. Bridges carry lots across networks.
- **Unknown cost basis is flagged, never guessed.** Coins that arrive from an exchange,
  a gift or an airdrop have no on-chain purchase price. Sales of those coins show
  "P&L unknown" until the user enters the cost. Stablecoins are treated as $1.
- **Transfers out to someone else** reduce holdings at cost and are not counted as
  sales, since the chain can't tell a payment from a gift or an exchange deposit.

## Robinhood Chain

Robinhood Chain (chain ID 4663) is an Ethereum layer 2 that uses ETH for gas,
so every 0x address is scanned on both Ethereum and Robinhood Chain
automatically. Data comes from the public Blockscout explorer API
(`ROBINHOOD_BLOCKSCOUT_URL`, default https://robinhoodchain.blockscout.com),
which needs no key. ETH on either network shares one cost-basis pool. Tokenized
stocks and other Robinhood Chain tokens have no CoinGecko price yet, so their
USD values show as unknown. Scanner: `backend/scanners/robinhood.py`.

## Compressed NFTs (Solana)

cNFTs don't move as tokens, so they're read from the compressed events in
Helius's parsed transactions (`events.compressed`: asset ID plus old and new
owner). Names and pictures come from the same DAS lookup as regular NFTs, and
they're tagged "cNFT" in the dashboard. Many scam airdrops are cNFTs, so any
cNFT whose name or description advertises a link or giveaway is hidden as spam.

## Highlights

The dashboard shows the **best and worst trade** (sells and swaps of coins and
tokens) and the **best and worst NFT flip** (an NFT bought and later sold, with
what was paid, what it sold for and how long it was held). Only complete
figures count: trades involving coins with an unknown purchase price are left
out. Logic lives in `backend/portfolio/highlights.py`; the data is in the
`summary.highlights` field of `GET /api/wallets/{id}`.

## Spam filtering

Airdropped junk is hidden from the history and kept out of holdings, totals
and P&L. A token or NFT counts as spam when the provider flags it (Alchemy
marks spam NFT contracts), its name or symbol advertises a link or giveaway
("Visit claim-eth.xyz", "$5,000 reward"), or it is an unpriced token that only
ever arrived on its own (never sent, never part of a transaction you started).
Users can tick **Show spam** to see what was hidden. Rules live in
`backend/classifiers/spam.py`.

## Name lookup

- **`.eth`** names are read from the ENS registry on-chain through your Alchemy key.
- **`.sol`** names go through the Bonfida SNS resolver (`SNS_RESOLVER_URL` to override).
- Not yet supported: ENS names that resolve off-chain (e.g. `cb.id`) and emoji-name normalization.

## Known limits of this version (next steps, in order)

1. **Persistence.** Portfolios live in memory and vanish on restart. The Postgres
   schema is ready in `migrations/`; the next job is a `database/postgres.py` store
   that implements the same `create/get/delete` plus saving transactions, prices and
   cursors. Then move scan jobs to a Redis-backed worker (e.g. arq or RQ) so scans
   survive restarts and run on more than one server; move the rate limiter to Redis too.
2. **Live-provider check.** Parsing is tested against provider-shaped fixtures, but
   this code has not yet been run against live Alchemy, Helius, mempool.space and
   CoinGecko responses. Run one known wallet per chain and compare against an explorer
   before launch. Helius in particular has changed hostnames; both are configurable in `.env`.
3. **Ethereum gaps.** The Transfers API skips calls that move no assets (token approvals)
   and failed transactions with no transfers. Add a transaction-list source if needed.
   Only Ethereum mainnet is covered; L2s (Base, Arbitrum) are the same scanner with a
   different Alchemy URL.
4. **Bitcoin is per-address.** xpub/descriptor support (all addresses of one wallet) is
   a common request and a natural Phase 3 addition.
5. **NFT images** come from Alchemy (Ethereum) and Helius DAS (Solana). Only https,
   IPFS and Arweave links are passed to the browser; IPFS goes through the public
   ipfs.io gateway, which can be slow. Ethereum NFTs Alchemy marks as spam stay
   hidden until the viewer taps them. Solana has no spam flag yet.
6. **Price coverage.** Tokens not listed on CoinGecko and NFT floor values show "—".
   NFT trade values come from what was paid or received.
7. **Bridge registry** in `classifiers/classify.py` is a short starter list.

Not tax advice: the P&L engine follows standard FIFO/average-cost accounting,
but tax treatment varies by country.

## NFT floors, gallery and value chart

- **Floor prices:** Solana NFTs are valued at their Magic Eden collection floor and Ethereum NFTs at their OpenSea (then LooksRare) floor via Alchemy. These floors count toward the portfolio total and unrealized P&L. Collections with no floor, such as cNFTs Magic Eden doesn't list, stay unvalued.
- **NFT gallery:** held NFTs are grouped by collection, showing the floor, the count and the total value of each group.
- **Portfolio value chart:** a daily value of coins and tokens over 30 days, 90 days or 1 year, built by replaying history at each day's price. The free CoinGecko plan limits it to the last 365 days.

## Speed

- Prices are cached in a shared Render Key Value store (`CACHE_URL`): coin histories for 6 hours, the coin directory for 24 hours and current prices for 2 minutes. The cache is shared by every scan and survives the server sleeping. Without it, everything still works, just slower.
- Results appear as soon as the wallet has been read. Dollar values, profit/loss and NFT floors fill in afterwards.
- Independent lookups run side by side: Ethereum sent/received transfers, owned-NFT checks across wallets, and price history alongside NFT lookups.

## Cardano

- Enter any Cardano address (`addr1…`, a `stake1…` stake address, a legacy Byron address) or an ADA Handle (`$name`). The whole wallet is scanned: every address under the same stake key.
- Data comes from [Koios](https://api.koios.rest), which needs no key. Set `KOIOS_API_KEY` (free account) to raise the daily limit from 5,000 to 50,000 requests.
- ADA, native tokens (priced through CoinGecko where listed) and NFTs (CIP-25 and CIP-68 names and images) are all covered.
- Staking rewards are shown as income when withdrawn, valued at the ADA price on that day.
- Cardano NFT floors come from [OpenCNFT](https://opencnft.io) (no key; credited next to each floor), because jpg.store closed its API in 2026.

## XRP Ledger

- Enter a classic XRP address (`r…`). Data comes from the public XRP Ledger servers (`XRPL_RPC_URL`, default xrplcluster.com), with no key needed.
- Balance changes are read straight from each transaction's ledger metadata. That covers XRP, issued tokens (trust lines), built-in exchange trades, AMM deposits and XLS-20 NFTs, and it stays exact for partial payments and multi-step trades.
- NFT names and photos come from each NFT's metadata link, up to 80 lookups per scan. Tokens are priced through CoinGecko where listed.
- XRP NFT floors come from XRP Ledger Services (api.xrpldata.com, no key), per issuer and collection number (taxon).

## Dogecoin

- Enter a Dogecoin address (`D…`, or `A…`/`9…` multisig). Its checksum is verified, so it can't be mistaken for a Solana address.
- History comes from electrs-dogecoin (`DOGE_ESPLORA_URL`), the Dogecoin port of the explorer API used for Bitcoin, which needs no key. If it can't be reached, scans switch to BlockCypher automatically; set `BLOCKCYPHER_TOKEN` (free) for more headroom.
- DOGE is priced through CoinGecko, with full history, profit/loss, charts and highlights like every other network.
- Doginals (Dogecoin NFTs) the wallet holds appear in the NFT gallery when `MAESTRO_API_KEY` is set (free Maestro account). Image inscriptions are shown, while text ones such as DRC-20 token transfers are skipped. Each image loads from a public Doginals explorer (`DOGINALS_CONTENT_URL`). Doginals have no floor price yet.
