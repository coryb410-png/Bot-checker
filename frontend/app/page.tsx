"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { CHAIN_LABEL, Chain, detectChain, isName, looksLikeSecret, shorten } from "@/lib/address";
import { api, Method } from "@/lib/api";
import { BrandMark, Monkey, Mood, Say } from "@/components/Brand";

interface Entry { address: string; chain: Chain; label?: string }

export default function Home() {
  const router = useRouter();
  const [input, setInput] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [method, setMethod] = useState<Method>("fifo");
  const [error, setErrorText] = useState<string | null>(null);
  const [errorMood, setErrorMood] = useState<Mood>("mad");
  const setError = (text: string | null, mood: Mood = "mad") => { setErrorText(text); setErrorMood(mood); };
  const [busy, setBusy] = useState(false);

  // Prefill from a link like /?addresses=bc1q…,0x…,So1… (used by the MADBOY preview page).
  // Only valid public addresses are accepted; anything else in the link is ignored.
  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get("addresses");
    if (!raw) return;
    const seen = new Set<string>();
    const found: Entry[] = [];
    for (const part of raw.split(",").slice(0, 20)) {
      const a = part.trim();
      const chain = detectChain(a);
      if (!chain || looksLikeSecret(a) || seen.has(a.toLowerCase())) continue;
      seen.add(a.toLowerCase());
      found.push({ address: a, chain });
    }
    if (found.length) setEntries(found);
  }, []);

  const detected = input.trim() ? detectChain(input) : null;
  const typedName = !detected && isName(input) ? input.trim().toLowerCase() : null;

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const a = input.trim();
    setError(null);
    if (!a) return;
    if (looksLikeSecret(a)) {
      setInput("");
      setError("Shh. That looks like a seed phrase or private key. It was cleared and not sent anywhere. Enter a public address instead.", "shh");
      return;
    }
    let address = a, label: string | undefined, chain = detectChain(a);
    if (!chain && isName(a)) {
      setBusy(true);
      try {
        const r = await api.resolve(a);
        address = r.address; label = r.name; chain = r.chain;
      } catch (err) {
        return setError((err as Error).message);
      } finally {
        setBusy(false);
      }
    }
    if (!chain) return setError("That isn't a valid Bitcoin, Ethereum, Solana or Cardano address, or a name like name.eth, name.sol or $handle.");
    if (entries.some((x) => x.address.toLowerCase() === address.toLowerCase())) return setError("That address is already in this portfolio.");
    setEntries([...entries, { address, chain, label }]);
    setInput("");
  }

  async function tryExample() {
    setBusy(true);
    setError(null);
    try {
      const { id } = await api.demo(method);
      router.push(`/wallet/${id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  async function scan() {
    setBusy(true);
    setError(null);
    try {
      const labels = Object.fromEntries(entries.filter((e) => e.label).map((e) => [e.address, e.label as string]));
      const { id } = await api.scan(entries.map((e) => e.address), method, labels);
      router.push(`/wallet/${id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <>
    <header className="masthead">
      <div className="in">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <BrandMark />
          <span className="chip">Read-only · no wallet connection</span>
        </div>
        <section className="hero">
          <div style={{ display: "grid", gap: 12, minWidth: 0 }}>
            <span className="eyebrow">Wallet analytics · BTC · ETH · SOL · ADA</span>
            <h1>Your entire crypto history. One place.</h1>
            <p>Add public addresses to rebuild every purchase, sale, swap and transfer with historical USD values and profit or loss. No signatures, no keys.</p>
          </div>
          <Monkey mood="cash" size="xl" bare />
        </section>
      </div>
    </header>
    <main className="wrap" style={{ display: "grid", gap: 28, paddingTop: 0 }}>
      <section className="panel lift" style={{ display: "grid", gap: 14 }}>
        <form onSubmit={add} style={{ display: "grid", gap: 10 }}>
          <label htmlFor="addr" className="eyebrow">Public wallet address</label>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input id="addr" value={input} onChange={(e) => setInput(e.target.value)} autoComplete="off" spellCheck={false}
              placeholder="Address, name.eth, name.sol or $handle" />
            <button className="btn" type="submit" disabled={busy}>{busy && typedName ? "Looking up…" : "Add"}</button>
          </div>
          <div className="row" style={{ fontSize: 13, color: "var(--muted)", justifyContent: "space-between" }}>
            <span>
              {detected ? <span className={`chip ${detected}`}>{CHAIN_LABEL[detected]}{detected === "ethereum" ? " + Robinhood Chain" : ""} detected</span>
                : typedName ? (() => {
                    const kind = typedName.startsWith("$") ? ["cardano", "ADA Handle"] : typedName.endsWith(".sol") ? ["solana", "Solana name"] : ["ethereum", "ENS name"];
                    return <span className={`chip ${kind[0]}`}>{kind[1]}</span>;
                  })()
                : "Network is detected automatically. Names like name.eth, name.sol and $handle work too."}
            </span>
            <span className="row" style={{ gap: 8, flexWrap: "nowrap" }}><Monkey mood="shh" size="sm" />Keep your keys secret. ChainScope only needs public addresses.</span>
          </div>
        </form>
        {error && <Say mood={errorMood} className={errorMood === "mad" ? "err" : ""} role="alert">{error}</Say>}

        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 14, display: "grid", gap: 10 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>Portfolio addresses</h2>
            <span className="eyebrow">{entries.length} added</span>
          </div>
          {entries.length === 0 ? (
            <Say mood="smirk"><span style={{ color: "var(--muted)" }}>No wallets yet. Add one address per network and they combine into one portfolio.</span></Say>
          ) : (
            <ul className="tl">
              {entries.map((e) => (
                <li key={e.address}>
                  <span className="row" style={{ minWidth: 0 }}>
                    <span className={`chip ${e.chain}`}>{CHAIN_LABEL[e.chain]}{e.chain === "ethereum" ? " + Robinhood" : ""}</span>
                    {e.label && <strong>{e.label}</strong>}
                    <span className="mono" title={e.address} style={e.label ? { color: "var(--muted)", fontSize: 13 } : undefined}>{shorten(e.address, e.label ? 4 : 8)}</span>
                  </span>
                  <button className="btn" onClick={() => setEntries(entries.filter((x) => x !== e))}>Remove</button>
                </li>
              ))}
            </ul>
          )}
          <div className="row" style={{ justifyContent: "space-between" }}>
            <label className="row" style={{ gap: 8, fontSize: 14 }}>
              Cost basis
              <select value={method} onChange={(e) => setMethod(e.target.value as Method)} style={{ width: "auto" }}>
                <option value="fifo">FIFO</option>
                <option value="average">Average cost</option>
              </select>
            </label>
            <button className="btn primary" disabled={!entries.length || busy} onClick={scan}>
              {busy ? "Starting…" : "Scan portfolio"}
            </button>
          </div>
        </div>
      </section>

      <section className="panel row" style={{ justifyContent: "space-between", gap: 14, background: "var(--sunk)" }}>
        <div className="row" style={{ gap: 14, flexWrap: "nowrap", minWidth: 0 }}>
          <Monkey mood="smirk" size="lg" />
          <div style={{ display: "grid", gap: 4, minWidth: 0 }}>
            <h2>No wallet handy?</h2>
            <p style={{ margin: 0, color: "var(--muted)" }}>Load a made-up portfolio with Bitcoin, Ethereum, Solana and NFT activity to see how ChainScope works.</p>
          </div>
        </div>
        <button className="btn primary" disabled={busy} onClick={tryExample}>Try an example wallet</button>
      </section>
    </main>
    </>
  );
}
