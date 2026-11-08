"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import NftThumb from "@/components/NftThumb";
import Highlights from "@/components/Highlights";
import NftGallery from "@/components/NftGallery";
import ValueChart from "@/components/ValueChart";
import { BrandMark, Monkey, Say, moodFor } from "@/components/Brand";
import { CHAIN_LABEL, Chain, shorten } from "@/lib/address";
import { api, Holding, Method, money, Tx, WalletStatus } from "@/lib/api";

const TYPES: [string, string][] = [
  ["", "All activity"], ["buy,nft_purchase", "Buys (coins + NFTs)"], ["sell,nft_sale", "Sales (coins + NFTs)"],
  ["swap", "Swaps"],
  ["transfer_in", "Transfers in"], ["transfer_out", "Transfers out"], ["internal_transfer", "Between my wallets"],
  ["bridge", "Bridges"], ["nft_purchase", "NFT purchases"], ["nft_sale", "NFT sales"],
  ["contract_interaction", "Contract calls"], ["network_fee", "Fees only"],
];
const PAGE = 250;

// One tab per address. An 0x address is scanned on Ethereum and five other networks
// Chain, so it gets a single tab covering both.
type WalletTab = { address: string; label: string | null; chains: Chain[]; scanned: number };
function walletTabs(addresses: WalletStatus["addresses"]): WalletTab[] {
  const out: WalletTab[] = [];
  for (const a of addresses) {
    const same = out.find((t) => t.address === a.address || (a.address.startsWith("0x") && t.address.toLowerCase() === a.address.toLowerCase()));
    if (same) { same.chains.push(a.chain as Chain); same.scanned += a.scanned; same.label ??= a.label; }
    else out.push({ address: a.address, label: a.label, chains: [a.chain as Chain], scanned: a.scanned });
  }
  return out;
}
const chainsLabel = (cs: Chain[]) =>
  cs.includes("ethereum") && cs.length > 2 ? `Ethereum + ${cs.length - 1} networks`
  : cs.map((c) => (c === "robinhood" ? "Robinhood" : CHAIN_LABEL[c])).join(" + ");

export default function WalletPage({ params }: { params: { id: string } }) {
  const id = params.id;
  const [st, setSt] = useState<WalletStatus | null>(null);
  const [txs, setTxs] = useState<Tx[]>([]);
  const [total, setTotal] = useState(0);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [type, setType] = useState("");
  const [chain, setChain] = useState("");
  const [nftOnly, setNftOnly] = useState(false);
  const [showSpam, setShowSpam] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sentinel = useRef<HTMLLIElement | null>(null);
  const [wallet, setWallet] = useState<string | null>(null);   // null = all wallets
  const walletRef = useRef<string | null>(null);
  walletRef.current = wallet;
  const filterKey = `${type}|${chain}|${nftOnly}|${showSpam}|${wallet ?? ""}`;
  const activeKey = useRef(filterKey);

  // First page plus holdings; the rest of the history streams in below.
  const loadData = useCallback(async () => {
    activeKey.current = filterKey;
    const w = wallet ?? undefined;
    const [t, h] = await Promise.all([api.transactions(id, { type, chain, nft: nftOnly, include_spam: showSpam, wallet: w, offset: 0, limit: PAGE }), api.holdings(id, wallet)]);
    if (activeKey.current !== filterKey) return;
    setTxs(t.items); setTotal(t.total); setHoldings(h);
  }, [id, type, chain, nftOnly, showSpam, wallet, filterKey]);

  const loadMore = useCallback(async () => {
    if (loadingMore || txs.length >= total) return;
    setLoadingMore(true);
    const key = filterKey;
    try {
      const t = await api.transactions(id, { type, chain, nft: nftOnly, include_spam: showSpam, wallet: wallet ?? undefined, offset: txs.length, limit: PAGE });
      if (activeKey.current === key) setTxs((prev) => [...prev, ...t.items]);
    } finally { setLoadingMore(false); }
  }, [id, type, chain, nftOnly, showSpam, wallet, filterKey, txs.length, total, loadingMore]);

  // Keep loading as the reader nears the end, until every transaction is shown
  useEffect(() => {
    const el = sentinel.current;
    if (!el || txs.length >= total) return;
    const io = new IntersectionObserver((e) => e[0].isIntersecting && loadMore(), { rootMargin: "800px" });
    io.observe(el);
    return () => io.disconnect();
  }, [loadMore, txs.length, total]);

  // Poll while the scan runs. Transactions and NFTs are shown as soon as the
  // wallet has been read; dollar values and floors fill in when they arrive.
  const loadedAt = useRef<string | null>(null);
  const [range, setRange] = useState(90);   // shared by the value chart and the highlights
  // Runs again whenever a filter or the wallet tab changes (loadData changes),
  // so that wallet's numbers and activity are fetched fresh.
  useEffect(() => {
    let stop = false;
    loadedAt.current = null;
    async function tick() {
      try {
        const s = await api.status(id, walletRef.current);
        if (stop) return;
        setSt(s);
        const hasData = s.status === "pricing" || s.status === "valuing" || s.status === "ready";
        if (hasData && loadedAt.current !== s.status) { loadedAt.current = s.status; await loadData(); }
        if (s.status !== "ready" && s.status !== "error") setTimeout(tick, 2000);
      } catch (e) { setError((e as Error).message); }
    }
    tick();
    return () => { stop = true; };
  }, [id, loadData]);

  async function changeMethod(m: Method) {
    await api.pnl(id, m);
    setSt(await api.status(id, wallet));
    await loadData();
  }

  async function addCost(hash: string) {
    const el = document.getElementById(`cost-${hash}`) as HTMLInputElement | null;
    const v = Number(el?.value);
    if (!el || !Number.isFinite(v) || v < 0) return;
    await api.setCost(id, hash, v);
    setSt(await api.status(id, wallet));
    await loadData();
  }

  if (error) return (
    <main className="wrap" style={{ display: "grid", gap: 14 }}>
      <Say mood="mad" size="lg" className="err" role="alert">{error}</Say>
      <Link href="/" className="btn primary" style={{ justifySelf: "start", textDecoration: "none" }}>Start a new scan</Link>
    </main>
  );
  if (!st) return <main className="wrap"><div className="scanning"><Say mood="shh" size="lg">Opening your portfolio…</Say></div></main>;

  const sm = st.summary;
  const scanning = st.status !== "ready" && st.status !== "error";
  const filling = scanning && !!sm && (st.status === "pricing" || st.status === "valuing");
  const tabs = walletTabs(st.addresses);
  const current = tabs.find((t) => t.address === wallet) ?? null;

  return (
    <>
    <header className="masthead slim">
      <div className="in row" style={{ justifyContent: "space-between", display: "flex" }}>
        <Link href="/"><BrandMark /></Link>
        <div className="row">
          {!st.demo && <button className="btn" disabled={scanning} onClick={async () => { await api.refresh(id); setSt({ ...st, status: "queued" }); location.reload(); }}>Refresh</button>}
          <button className="btn" onClick={async () => { await api.remove(id); location.href = "/"; }}>Delete portfolio</button>
        </div>
      </div>
    </header>
    <main className="wrap" style={{ display: "grid", gap: 20 }}>

      {st.demo && (
        <div className="panel row" style={{ justifyContent: "space-between", background: "var(--sunk)" }}>
          <span className="row" style={{ gap: 12, flexWrap: "nowrap" }}><Monkey mood="smirk" /><span><strong>Example wallet.</strong> Every address, amount and NFT here is invented, so you can explore without a real wallet.</span></span>
          <Link href="/" className="btn primary" style={{ textDecoration: "none" }}>Scan a real wallet</Link>
        </div>
      )}

      {tabs.length > 1 ? (
        <nav className="wtabs" role="tablist" aria-label="Wallets">
          <button type="button" role="tab" aria-selected={wallet === null} onClick={() => setWallet(null)}>
            <strong>All wallets</strong>
            <span className="sub">{tabs.length} addresses</span>
          </button>
          {tabs.map((t) => (
            <button key={t.address} type="button" role="tab" aria-selected={wallet === t.address} onClick={() => setWallet(t.address)}>
              <span className={`chip ${t.chains[0]}`}>{chainsLabel(t.chains)}</span>
              <span className="mono addr">{t.label ?? shorten(t.address, 5)}</span>
              {scanning && <span className="sub">{t.scanned.toLocaleString()} txs</span>}
            </button>
          ))}
        </nav>
      ) : (
      <section className="row">
        {st.addresses.map((a) => (
          <span key={a.address} className="row" style={{ gap: 6 }}>
            <span className={`chip ${a.chain}`}>{CHAIN_LABEL[a.chain as Chain]}</span>
            {a.label && <strong>{a.label}</strong>}
            <span className="mono" style={{ fontSize: 13, color: a.label ? "var(--muted)" : undefined }}>{shorten(a.address, a.label ? 4 : 6)}</span>
            {scanning && <span className="eyebrow">{a.scanned.toLocaleString()} txs</span>}
          </span>
        ))}
      </section>
      )}
      {current && (
        <p style={{ margin: "-8px 0 0", color: "var(--muted)", fontSize: 13 }}>
          Showing <span className="mono">{current.label ?? shorten(current.address, 6)}</span> only. Coins it received from your other wallets count as transfers in here, with no purchase price; the All wallets tab carries what you paid across.
        </p>
      )}

      {filling && (
        <div className="panel row" role="status" style={{ gap: 12, flexWrap: "nowrap", background: "var(--sunk)" }}>
          <Monkey mood="cash" size="sm" />
          <span>
            <strong>{st.status === "valuing" ? "Checking NFT floor prices…" : "Adding up dollar values…"}</strong>{" "}
            <span style={{ color: "var(--muted)" }}>Your activity is below. Totals and profit/loss update by themselves in a moment.</span>
          </span>
        </div>
      )}
      {scanning && !filling && (
        <div className="panel scanning" role="status">
          <Say mood={st.status === "pricing" || st.status === "valuing" ? "cash" : "shh"} size="lg">
            <strong>{st.status === "valuing" ? "Checking NFT floor prices…" : st.status === "pricing" ? "Adding up the money…" : "Reading the blockchain quietly…"}</strong>
            <p style={{ color: "var(--muted)", margin: "4px 0 0" }}>
              {st.status === "valuing" ? "Looking up what each NFT collection is selling for on Magic Eden and OpenSea."
                : st.status === "pricing" ? "Looking up what everything was worth on the day it moved."
                : "Large wallets take a few minutes. You can leave this page open; it updates by itself."}
            </p>
          </Say>
        </div>
      )}
      {st.status === "error" && <div className="panel"><Say mood="mad" className="err">Scan failed: {st.error}</Say></div>}
      {st.status === "ready" && (st.network_errors?.length ?? 0) > 0 && (
        <div className="panel">
          <Say mood="mad">
            <strong>Some networks couldn&apos;t be read this time:</strong>{" "}
            {st.network_errors!.map((n) => `${CHAIN_LABEL[n.chain as Chain] ?? n.chain} (${shorten(n.address, 4)})`).join(", ")}.
            {" "}Everything else is shown. Tap Refresh to try those again.
          </Say>
        </div>
      )}

      {sm && (
        <>
          <section className="grid4">
            <div className="panel stat"><Monkey mood="cash" size="sm" /><span className="eyebrow">Portfolio value</span><div className="v">{money(sm.portfolio_value)}</div><div className="s">{Number(sm.nft_value) > 0 ? `Incl. ${money(sm.nft_value)} in NFTs at floor` : "At current prices"}</div></div>
            <div className="panel stat">
              <span className="eyebrow">Realized P&amp;L</span>
              <div className={`v ${Number(sm.realized_pnl) >= 0 ? "gain" : "loss"}`}>{money(sm.realized_pnl, true)}</div>
              <div className={`s ${sm.pnl_complete ? "" : "warn"}`}>{sm.pnl_complete ? "Cost basis complete" : "Partial: some purchase prices unknown"}</div>
              <Monkey mood={moodFor(Number(sm.realized_pnl))} size="sm" />
            </div>
            <div className="panel stat"><span className="eyebrow">Transactions</span><div className="v">{sm.transactions.toLocaleString()}</div><div className="s">All-time activity</div></div>
            <div className="panel stat"><span className="eyebrow">Fees paid</span><div className="v">{money(sm.fees_paid)}</div><div className="s">{sm.assets_sold} assets sold</div></div>
          </section>

          {st.status === "ready" && <ValueChart id={id} wallet={wallet} refreshKey={st.updated_at} range={range} onRange={setRange} />}

          <Highlights all={sm.highlights} byRange={sm.highlights_by_range} range={range} onRange={setRange} />

          <NftGallery holdings={holdings} />

          {sm.warnings.length > 0 && (
            <details className="panel">
              <summary className="warn" style={{ cursor: "pointer", fontWeight: 600 }}>
                <Monkey mood="shh" size="xs" />{sm.warnings.length} cost-basis gaps. What you paid is a secret the blockchain doesn&apos;t know, so profit/loss leaves these out until you add it.
              </summary>
              <ul style={{ margin: "10px 0 0", paddingLeft: 18, color: "var(--muted)", fontSize: 14 }}>{sm.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            </details>
          )}

          <section className="panel" style={{ display: "grid", gap: 12 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2>Coins &amp; tokens</h2>
              <label className="row" style={{ gap: 8, fontSize: 14 }}>Cost basis
                <select value={st.method} onChange={(e) => changeMethod(e.target.value as Method)} style={{ width: "auto" }}>
                  <option value="fifo">FIFO</option><option value="average">Average cost</option>
                </select>
              </label>
            </div>
            <div className="scroll">
              <table>
                <thead><tr><th>Asset</th><th className="r">Quantity</th><th className="r">Price</th><th className="r">Value</th><th className="r">Cost basis</th><th className="r">Unrealized</th></tr></thead>
                <tbody>
                  {holdings.filter((h) => !h.nft).map((h) => (
                    <tr key={h.key}>
                      <td><span className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                        {h.nft ? <NftThumb src={h.image} name={h.name ?? h.asset} spam={h.spam} size={36} /> : <span className={`chip ${h.chain}`}>{h.chain.slice(0, 3)}</span>}
                        {h.nft ? (h.name ?? h.asset) : h.asset}
                        {h.compressed && <span className="chip plain">cNFT</span>}
                        {h.untracked && <span className="chip plain" title="You own this, but how it arrived isn't in the scanned history, so it has no purchase price">Not in history</span>}
                      </span></td>
                      <td className="r">{Number(h.quantity).toLocaleString(undefined, { maximumFractionDigits: 6 })}</td>
                      <td className="r">{money(h.price)}</td>
                      <td className="r">{money(h.value)}</td>
                      <td className="r">{money(h.cost_basis)}{Number(h.unknown_basis_qty) > 0 && <span className="warn" title="Part of this holding has no known purchase price"> *</span>}</td>
                      <td className={`r ${Number(h.unrealized_pnl) >= 0 ? "gain" : "loss"}`}>{money(h.unrealized_pnl, true)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="panel" style={{ display: "grid", gap: 12 }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h2>Transaction history</h2>
              <div className="row">
                <select aria-label="Filter by type" value={type} onChange={(e) => setType(e.target.value)} style={{ width: "auto" }}>
                  {TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                <select aria-label="Filter by network" value={chain} onChange={(e) => setChain(e.target.value)} style={{ width: "auto" }}>
                  <option value="">All networks</option><option value="bitcoin">Bitcoin</option><option value="ethereum">Ethereum</option><option value="solana">Solana</option><option value="base">Base</option><option value="arbitrum">Arbitrum</option><option value="optimism">Optimism</option><option value="polygon">Polygon</option><option value="robinhood">Robinhood Chain</option><option value="cardano">Cardano</option><option value="xrp">XRP Ledger</option><option value="dogecoin">Dogecoin</option>
                </select>
                <label className="row" style={{ gap: 6, fontSize: 14 }}>
                  <input type="checkbox" checked={nftOnly} onChange={(e) => setNftOnly(e.target.checked)} style={{ width: "auto" }} />
                  NFTs only
                </label>
                {(sm.spam_hidden.transactions ?? 0) > 0 && (
                  <label className="row" style={{ gap: 6, fontSize: 14 }}>
                    <input type="checkbox" checked={showSpam} onChange={(e) => setShowSpam(e.target.checked)} style={{ width: "auto" }} />
                    Show hidden ({sm.spam_hidden.transactions})
                  </label>
                )}
              </div>
            </div>
            <ul className="tl">
              {txs.map((t) => {
                const value = Number(t.usd_in) || Number(t.usd_out);
                const nfts = t.legs.filter((l) => l.nft);
                return (
                  <li key={t.hash + t.wallet}>
                    <div style={{ minWidth: 0, display: "flex", gap: 12, alignItems: "flex-start" }}>
                      {nfts.length > 0 && (
                        <div className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
                          {nfts.slice(0, 3).map((l) => <NftThumb key={l.token_id ?? l.asset} src={l.image} name={l.name ?? l.asset} spam={l.spam} />)}
                          {nfts.length > 3 && <span className="eyebrow">+{nfts.length - 3}</span>}
                        </div>
                      )}
                      <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 600 }}>
                        {t.spam && <span className="chip" style={{ color: "var(--warn)", marginRight: 6 }}>Spam</span>}
                        {t.label}{!t.success && <span className="loss"> · failed</span>}
                      </div>
                      {nfts.length > 0 && (
                        <div style={{ fontSize: 14, overflowWrap: "anywhere" }}>
                          {nfts.map((l) => l.name ?? `${l.asset} #${l.token_id}`).join(", ")}
                          {nfts.some((l) => l.compressed) && <span className="chip plain" style={{ marginLeft: 6 }}>cNFT</span>}
                        </div>
                      )}
                      <div className="meta">
                        {new Date(t.date).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" })} · {t.source ?? CHAIN_LABEL[t.chain as Chain]} ·{" "}
                        <a href={t.explorer} target="_blank" rel="noreferrer" className="mono">{shorten(t.hash, 5)}</a>
                      </div>
                      {t.type === "transfer_in" && (
                        <div className="row" style={{ marginTop: 8, gap: 6, flexWrap: "nowrap" }}>
                          <input id={`cost-${t.hash}`} type="number" min="0" step="0.01" placeholder="What you paid, USD" style={{ maxWidth: 200, padding: "6px 8px", fontSize: 13 }} />
                          <button className="btn" style={{ padding: "6px 10px", fontSize: 13 }} onClick={() => addCost(t.hash)}>Set cost</button>
                        </div>
                      )}
                      </div>
                    </div>
                    <div className="amt">
                      <div>{value ? money(String(value)) : "—"}</div>
                      {t.has_realized && (
                        <div className={t.realized_pnl === null ? "warn" : Number(t.realized_pnl) >= 0 ? "gain" : "loss"} style={{ fontSize: 13 }}>
                          <Monkey mood={moodFor(t.realized_pnl === null ? null : Number(t.realized_pnl))} size="xs" />
                          {t.realized_pnl === null ? "P&L unknown" : `${money(t.realized_pnl, true)} P&L`}
                        </div>
                      )}
                      {t.fee_usd && Number(t.fee_usd) > 0 && <div className="meta">fee {money(t.fee_usd)}</div>}
                    </div>
                  </li>
                );
              })}
              {txs.length === 0 && <li><Say mood="smirk"><span style={{ color: "var(--muted)" }}>Nothing matches these filters. Try another type or network.</span></Say></li>}
              {txs.length < total && (
                <li ref={sentinel} style={{ display: "block", textAlign: "center" }}>
                  <button className="btn" disabled={loadingMore} onClick={loadMore}>
                    {loadingMore ? "Loading…" : `Load ${Math.min(PAGE, total - txs.length).toLocaleString()} more`}
                  </button>
                </li>
              )}
            </ul>
            <span style={{ color: "var(--muted)", fontSize: 13 }}>
              {(sm.spam_hidden.transactions ?? 0) > 0 && !showSpam && <Monkey mood="shh" size="xs" />}
              {total ? `Showing ${txs.length.toLocaleString()} of ${total.toLocaleString()} transactions, newest first` : ""}
              {(sm.spam_hidden.transactions ?? 0) > 0 && !showSpam ? ` · ${sm.spam_hidden.transactions} hidden as likely spam` : ""}
            </span>
          </section>
        </>
      )}
    </main>
    </>
  );
}
