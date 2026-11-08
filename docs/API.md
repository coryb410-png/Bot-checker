# ChainScope API (v1)

Read-only wallet analytics for public addresses. It needs no keys or signatures from users.

**Base URL:** `https://madboy-chainscope-api.onrender.com/api/v1`
**Interactive docs:** `https://madboy-chainscope-api.onrender.com/api/docs` (OpenAPI: `/api/openapi.json`)

Use the `/api/v1/…` paths. They're the stable contract; the unversioned `/api/…` paths are what the website uses and may change.

## Privacy

Nothing is stored. A scan lives in the server's memory only. It's gone after `DELETE`, a server restart, or the free server sleeping. Store the portfolio `id` only for as long as you're showing results.

## Networks

| Network | Address formats | Notes |
|---|---|---|
| Bitcoin | `bc1…`, `1…`, `3…` | |
| Ethereum | `0x…` | Also scanned automatically on **Base, Arbitrum, Optimism, Polygon and Robinhood Chain** |
| Solana | base58 | NFTs and cNFTs |
| Cardano | `addr1…`, `stake1…`, `Ae2…`, `$handle` | Whole wallet (every address under the stake key) |
| XRP Ledger | `r…` | Tokens, DEX/AMM, XLS-20 NFTs |
| Dogecoin | `D…`, `A…`, `9…` | Doginals when the server has a Maestro key |

`name.eth`, `name.sol` and `$handle` (ADA Handle) are resolved for you.

## Authentication and limits

You don't need a key for normal use. Limits are per person (per IP), and the defaults are:

| What | Limit |
|---|---|
| Any API call | 240 per minute (bursts of 120) |
| Starting a scan or refresh | 12 per hour (bursts of 6) |
| Name lookups | 30 per minute |

A partner app can get **20× higher limits** by sending `X-API-Key: <key>`. Keys are issued by the ChainScope operator; each key is limited separately. Keep the key on your server, not in a public app bundle.

When a limit is hit, the API returns **429** with a `Retry-After` header (in seconds) and a message to show the user:

```json
{ "detail": "You've started a lot of scans in a short time. You can start another in about 4 minutes." }
```

### Calling from a browser

Browser calls from another website need that site's origin on the server's allow list (`CORS_ORIGINS`). Native mobile apps and server-to-server calls don't need it.

## Typical flow

1. `POST /wallets/scan` → `{ "id": "…", "status": "queued" }`
2. Poll `GET /wallets/{id}` every 2 seconds. `status` moves through `queued → scanning → pricing → valuing → ready` (or `error`). From `pricing` on, transactions and holdings are available, and dollar values fill in by `ready`.
3. Read `/holdings`, `/transactions` and `/performance`.
4. `DELETE /wallets/{id}` when you're done.

Any read endpoint takes `?wallet=<address>` to show a single address of a multi-address portfolio as if it were scanned alone.

## Endpoints

### `POST /wallets/scan`
Starts a scan. Body:
```json
{ "addresses": ["bc1q…", "0xd8dA…", "madboy.sol"], "labels": { "0xd8dA…": "Main" }, "method": "fifo" }
```
`addresses`: 1–20 addresses or names. `method`: `fifo` or `average` (cost basis). It answers **202** with `{ "id", "status" }`, and **400** for an invalid address or something that looks like a private key or seed phrase (which is never accepted).

### `GET /wallets/{id}`
Status plus a summary:
```json
{
  "id": "…", "status": "ready", "error": null, "method": "fifo", "updated_at": "…",
  "addresses": [{ "chain": "ethereum", "address": "0x…", "label": "Main", "scanned": 412 }],
  "network_errors": [{ "chain": "polygon", "address": "0x…", "error": "…" }],
  "summary": {
    "portfolio_value": "54921.45", "nft_value": "740.00", "nfts_valued": 1, "nfts_held": 3,
    "realized_pnl": "3959.85", "pnl_complete": false, "transactions": 412,
    "fees_paid": "61.20", "warnings": ["…"], "spam_hidden": { "transactions": 12, "assets": 5 },
    "highlights": { "best_trade": {…}, "worst_trade": {…}, "best_flip": {…}, "worst_flip": {…} },
    "highlights_by_range": { "30": {…}, "90": {…}, "366": {…}, "all": {…} }
  }
}
```
Money values are strings in US dollars, so precision is never lost. Treat them as decimals.

### `GET /wallets/{id}/holdings`
Coins, tokens and NFTs held now: `asset`, `name`, `chain`, `quantity`, `price`, `value`, `cost_basis`, `unrealized_pnl`, `nft`, `image`, `collection`, `floor` (`amount`, `currency`, `source`, `url`, `usd`), and `untracked` (an NFT held but missing from the history), `unknown_basis_qty` and `spam`.

### `GET /wallets/{id}/transactions`
Query parameters:
- `type`: a transaction type, e.g. `swap`, `nft_purchase`, `nft_sale` or `transfer_in`
- `chain`: one network
- `nft=true`: only NFT activity
- `include_spam=true`: include filtered spam
- `offset` and `limit` (up to 500)

It returns `{ "total", "items": [...] }`. Each item has `hash`, `chain`, `date`, `type`, `label`, `legs` (asset, amount, USD value, image), `fee`, `fee_usd`, `realized_pnl`, `source`, `success` and an `explorer` link.

### `GET /transactions/{hash}?portfolio={id}`
One transaction in full.

### `GET /wallets/{id}/performance`
`value_over_time`: daily portfolio value of coins and tokens (`[{ "date", "value" }]`, up to a year), plus `realized_over_time`.

### `GET /wallets/{id}/pnl?method=fifo|average`
Profit/loss by asset under either cost-basis method.

### `PUT /wallets/{id}/cost-basis`
Fill in what was paid for an incoming transfer: `{ "tx_hash": "…", "total_cost_usd": "1250.00" }`.

### `POST /wallets/{id}/refresh`
Re-scan, fetching only what's new.

### `DELETE /wallets/{id}`
Forget the scan now. Answers **204**.

### `GET /resolve?name=madboy.sol`
Resolves `name.eth`, `name.sol` or `$handle` to `{ "chain", "address" }`.

### `POST /demo`
Creates a private example portfolio, for trying the API without real addresses.

### `GET /health`
`{ "ok": true }`. This isn't rate limited. The free server sleeps when idle, so the first call after a quiet spell can take 30–60 seconds; retry on 502/503.

## Errors

Errors are JSON shaped like `{ "detail": "message for the user" }`.

| Status | Meaning |
|---|---|
| 400 | Invalid input |
| 404 | Unknown portfolio (it may have expired) |
| 429 | Rate limited; see `Retry-After` |
| 502/503 | Server waking up or a data provider down; retry |
