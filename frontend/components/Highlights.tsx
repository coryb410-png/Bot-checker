"use client";

import NftThumb from "@/components/NftThumb";
import { Monkey, moodFor } from "@/components/Brand";
import { explorerUrl, FlipHighlight, Highlights as H, money, TradeHighlight } from "@/lib/api";

const day = (d: string) => new Date(d).toLocaleDateString(undefined, { month: "short", day: "2-digit", year: "numeric" });
const heldFor = (n: number | null) => n === null ? "" : n === 0 ? "same day" : n === 1 ? "1 day" : `${n.toLocaleString()} days`;

function Card({ title, realized, children, href }: { title: string; realized: string; children: React.ReactNode; href: string }) {
  const n = Number(realized);
  return (
    <a className="panel hl" href={href} target="_blank" rel="noreferrer">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
        <span className="eyebrow">{title}</span>
        <Monkey mood={moodFor(n)} size="sm" />
      </div>
      <div className={`hl-v ${n >= 0 ? "gain" : "loss"}`}>{money(realized, true)}</div>
      {children}
    </a>
  );
}

function Trade({ title, t }: { title: string; t: TradeHighlight }) {
  return (
    <Card title={title} realized={t.realized} href={explorerUrl(t.chain, t.tx_hash)}>
      <div style={{ fontWeight: 600 }}>{t.label}</div>
      <div className="meta">{day(t.date)} · {t.source ?? t.chain}</div>
    </Card>
  );
}

function Flip({ title, f }: { title: string; f: FlipHighlight }) {
  return (
    <Card title={title} realized={f.realized} href={explorerUrl(f.chain, f.tx_hash)}>
      <div className="row" style={{ gap: 10, flexWrap: "nowrap", minWidth: 0 }}>
        <NftThumb src={f.image} name={f.name} spam={f.spam} size={48} link={false} />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600, overflowWrap: "anywhere" }}>{f.name}</div>
          <div className="meta">Bought {money(f.cost)} · sold {money(f.proceeds)}</div>
          <div className="meta">{f.held_days !== null ? `Held ${heldFor(f.held_days)} · ` : ""}{day(f.date)}</div>
        </div>
      </div>
    </Card>
  );
}

export default function Highlights({ h }: { h: H }) {
  if (!h || (!h.best_trade && !h.best_flip)) return null;
  return (
    <section style={{ display: "grid", gap: 10 }}>
      <h2>Highlights</h2>
      <div className="hl-grid">
        {h.best_trade && <Trade title="Best trade" t={h.best_trade} />}
        {h.worst_trade && <Trade title="Worst trade" t={h.worst_trade} />}
        {h.best_flip && <Flip title="Best NFT flip" f={h.best_flip} />}
        {h.worst_flip && <Flip title="Worst NFT flip" f={h.worst_flip} />}
      </div>
    </section>
  );
}
