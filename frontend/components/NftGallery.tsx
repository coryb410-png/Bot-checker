"use client";

import { useMemo, useState } from "react";
import NftThumb from "@/components/NftThumb";
import { CHAIN_LABEL, Chain } from "@/lib/address";
import { Holding, money } from "@/lib/api";

const PREVIEW = 8;

// "Wegen #4664" -> "Wegen", "Solana Bear#63" -> "Solana Bear", "MONKI 145" -> "MONKI"
const baseName = (n: string | null) => (n ?? "").replace(/\s*#?\s*\d+\s*$/, "").trim();

interface Group { key: string; name: string; chain: string; items: Holding[]; value: number; floor: Holding["floor"] }

function groupNfts(nfts: Holding[]): Group[] {
  const by = new Map<string, Holding[]>();
  for (const h of nfts) {
    const k = h.collection ?? `${h.chain}:${h.asset}`;
    by.set(k, [...(by.get(k) ?? []), h]);
  }
  return [...by.entries()].map(([key, items]) => {
    const counts = new Map<string, number>();
    for (const i of items) { const b = baseName(i.name); if (b) counts.set(b, (counts.get(b) ?? 0) + 1); }
    const name = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || items[0].asset || "Unnamed collection";
    items.sort((a, b) => (a.name ?? "").localeCompare(b.name ?? "", undefined, { numeric: true }));
    return { key, name, chain: items[0].chain, items, floor: items.find((i) => i.floor)?.floor ?? null,
             value: items.reduce((s, i) => s + (Number(i.value) || 0), 0) };
  }).sort((a, b) => b.value - a.value || b.items.length - a.items.length || a.name.localeCompare(b.name));
}

export default function NftGallery({ holdings }: { holdings: Holding[] }) {
  const nfts = useMemo(() => holdings.filter((h) => h.nft && Number(h.quantity) > 0), [holdings]);
  const groups = useMemo(() => groupNfts(nfts), [nfts]);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (!nfts.length) return null;
  const total = groups.reduce((s, g) => s + g.value, 0);
  const valued = nfts.filter((n) => n.value).length;

  return (
    <section className="panel" style={{ display: "grid", gap: 14 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <h2>NFTs</h2>
        <span style={{ color: "var(--muted)", fontSize: 13 }}>
          {nfts.length} NFT{nfts.length === 1 ? "" : "s"} · {groups.length} collection{groups.length === 1 ? "" : "s"}
          {total > 0 && <> · <strong className="num" style={{ color: "var(--ink)" }}>{money(String(total))}</strong> at floor</>}
        </span>
      </div>
      {valued < nfts.length && (
        <span style={{ color: "var(--muted)", fontSize: 12, marginTop: -6 }}>
          {nfts.length - valued} without a floor price (not listed on Magic Eden or OpenSea), so they show “—”.
        </span>
      )}

      {groups.map((g) => {
        const shown = open[g.key] ? g.items : g.items.slice(0, PREVIEW);
        return (
          <div key={g.key} className="gal-group">
            <div className="gal-head">
              <div className="row" style={{ gap: 8, minWidth: 0 }}>
                <strong style={{ overflowWrap: "anywhere" }}>{g.name}</strong>
                <span className="chip plain">×{g.items.length}</span>
                <span className={`chip ${g.chain}`}>{CHAIN_LABEL[g.chain as Chain] ?? g.chain}</span>
              </div>
              <span className="num" style={{ fontSize: 13, color: "var(--muted)" }}>
                {g.floor ? (
                  <>
                    Floor {Number(g.floor.amount).toLocaleString(undefined, { maximumFractionDigits: 3 })} {g.floor.currency}
                    {g.floor.usd && <> ({money(g.floor.usd)})</>}
                    {" · "}
                    {g.floor.url ? <a href={g.floor.url} target="_blank" rel="noreferrer">{g.floor.source}</a> : g.floor.source}
                    {g.value > 0 && <> · <strong style={{ color: "var(--ink)" }}>{money(String(g.value))}</strong></>}
                  </>
                ) : "No floor price"}
              </span>
            </div>
            <div className="gal-grid">
              {shown.map((h) => (
                <div key={h.key} className="gal-tile">
                  <NftThumb src={h.image} name={h.name ?? h.asset} spam={h.spam} size="fill" />
                  <span className="nm" title={h.name ?? h.asset}>{h.name ?? h.asset}</span>
                  <span className="sub">
                    {h.value ? money(h.value) : "—"}
                    {h.cost_basis && Number(h.cost_basis) > 0 && <> · paid {money(h.cost_basis)}</>}
                  </span>
                  {(h.compressed || h.untracked) && (
                    <span className="tags">
                      {h.compressed && <span className="chip plain">cNFT</span>}
                      {h.untracked && <span className="chip plain" title="You own this, but how it arrived isn't in the scanned history, so it has no purchase price">Not in history</span>}
                    </span>
                  )}
                </div>
              ))}
            </div>
            {g.items.length > PREVIEW && (
              <button type="button" className="btn" style={{ justifySelf: "start", padding: "6px 12px", fontSize: 13 }}
                onClick={() => setOpen({ ...open, [g.key]: !open[g.key] })}>
                {open[g.key] ? "Show fewer" : `Show all ${g.items.length}`}
              </button>
            )}
          </div>
        );
      })}
    </section>
  );
}
