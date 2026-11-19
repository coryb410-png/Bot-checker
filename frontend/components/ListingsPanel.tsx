"use client";

import { CHAIN_LABEL, type Chain } from "@/lib/address";
import { money, type Listings } from "@/lib/api";
import NftThumb from "./NftThumb";

// NFTs this portfolio (or the selected wallet) has listed for sale, across
// marketplaces: OpenSea for Ethereum, its layer 2s and Robinhood Chain;
// Magic Eden for Solana.

function price(amount: string | null, currency: string) {
  if (amount == null) return "–";
  const n = Number(amount);
  const shown = n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : n.toLocaleString(undefined, { maximumFractionDigits: n < 1 ? 4 : 2 });
  return `${shown} ${currency}`;
}

function expires(iso: string | null) {
  if (!iso) return "No end date";
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return "Ending now";
  const h = ms / 3_600_000;
  if (h < 1) return `Ends in ${Math.max(1, Math.round(ms / 60_000))}m`;
  if (h < 48) return `Ends in ${Math.round(h)}h`;
  return `Ends ${new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

export default function ListingsPanel({ data, loading, error, onRefresh, scanning = false }:
  { data: Listings | null; loading: boolean; error: string | null; onRefresh: () => void; scanning?: boolean }) {
  const markets = data ? [...new Set(data.listings.map((l) => l.marketplace))] : [];
  return (
    <section className="panel" style={{ display: "grid", gap: 14 }}>
      <div className="row" style={{ justifyContent: "space-between", gap: 10 }}>
        <div style={{ display: "grid", gap: 2 }}>
          <h2 style={{ margin: 0 }}>Listings</h2>
          <span style={{ color: "var(--muted)", fontSize: 13 }}>
            {data ? <>{data.count} listed{Number(data.total_usd) > 0 && <> · asking <strong className="num">{money(data.total_usd)}</strong> in total</>}
              {data.checked_at && <> · checked {new Date(data.checked_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</>}</> : "Checking marketplaces…"}
          </span>
        </div>
        <button className="btn" disabled={loading} onClick={onRefresh}>{loading ? "Checking…" : "Check again"}</button>
      </div>

      {error && <p className="loss" style={{ margin: 0 }}>{error}</p>}
      {scanning && <p style={{ margin: 0, fontSize: 13, color: "var(--muted)" }}>The scan is still running: names, pictures and floor prices fill in when it finishes.</p>}

      {data && data.sources.some((s) => !s.ok) && (
        <p style={{ margin: 0, fontSize: 13, color: "var(--muted)" }}>
          Couldn&apos;t check: {data.sources.filter((s) => !s.ok).map((s) => s.name).join(", ")}. Listings there may be missing.
        </p>
      )}

      {data && data.count === 0 && !loading && (
        <p style={{ margin: 0, color: "var(--muted)" }}>Nothing listed right now on the marketplaces checked.</p>
      )}

      {data && data.count > 0 && (
        <div className="listings">
          {data.listings.map((l) => (
            <a key={`${l.marketplace}:${l.key}:${l.url}`} href={l.url} target="_blank" rel="noreferrer noopener" className="listing">
              <NftThumb src={l.image} name={l.name} size="fill" link={false} />
              <div className="li-body">
                <div className="li-name" title={l.name}>{l.name}</div>
                <div className="row" style={{ gap: 6 }}>
                  <span className={`chip ${l.chain}`}>{CHAIN_LABEL[l.chain as Chain] ?? l.chain}</span>
                  <span className="chip plain">{l.marketplace}</span>
                </div>
                <div className="li-price">
                  <strong className="num">{price(l.price, l.currency)}</strong>
                  {l.price_usd && <span className="num" style={{ color: "var(--muted)" }}> · {money(l.price_usd)}</span>}
                </div>
                <div className="li-meta">
                  {l.floor?.usd
                    ? <span>Floor {money(l.floor.usd)}{l.vs_floor_pct != null &&
                        <span className={l.vs_floor_pct >= 0 ? "gain" : "loss"}> ({l.vs_floor_pct >= 0 ? "+" : ""}{l.vs_floor_pct}%)</span>}</span>
                    : <span>No floor price</span>}
                  {l.paid_usd && <span>Paid {money(l.paid_usd)}</span>}
                  <span>{expires(l.expires_at)}</span>
                  {l.quantity > 1 && <span>×{l.quantity}</span>}
                </div>
              </div>
            </a>
          ))}
        </div>
      )}

      <p style={{ margin: 0, fontSize: 12, color: "var(--muted)" }}>
        {markets.length > 0 && <>Found on {markets.join(", ")}. </>}
        Checks OpenSea for Ethereum, Base, Arbitrum, Optimism, Polygon and Robinhood Chain; Magic Eden, Tensor and the other
        Solana marketplaces Helius recognises for Solana; and the XRP Ledger itself, which covers every XRP marketplace.
        Prices are what each NFT is listed at; tap one to open it.
      </p>
    </section>
  );
}
