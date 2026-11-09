"use client";

import { useCallback, useEffect, useState } from "react";
import { BrandMark, Say } from "@/components/Brand";

// ChainScope admin panel. Everything shown is live from the server's memory;
// nothing is stored, and scans are listed without wallet addresses.
// The password (ADMIN_TOKEN in Render) is kept for this browser tab only.

type Overview = {
  started_at: string; uptime_seconds: number; version: string | null;
  maintenance: { on: boolean; message: string };
  scans: { in_memory: number; by_status: Record<string, number>; transactions_in_memory: number;
           started: number; finished: number; failed: number };
  networks: Record<string, number>;
  provider_errors: Record<string, number>;
  rate_limited: Record<string, number>;
  limits: Record<string, { per_minute: number; burst: number }>;
  partner_multiplier: number;
  keys: { name: string; what: string; set: boolean }[];
  partners: string[];
  cors_origins: string[];
};
type ScanRow = { id: string; status: string; demo: boolean; created_at: string; networks: Record<string, number>;
                 addresses: number; transactions: number; nfts: number; network_errors: number; error: string | null };
type Event = { at: string; kind: string; message: string };
type Check = { name: string; ok: boolean; ms: number; error?: string };

const TOKEN_KEY = "chainscope-admin";
const LIMIT_NAMES: Record<string, string> = {
  request: "Any API call", scan: "Starting a scan", resolve: "Name lookups", demo: "Example wallet",
};

function since(iso: string) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
}
function duration(s: number) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

export default function AdminPage() {
  const [token, setToken] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ov, setOv] = useState<Overview | null>(null);
  const [scans, setScans] = useState<ScanRow[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [checks, setChecks] = useState<Check[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [partner, setPartner] = useState("");
  const [newKey, setNewKey] = useState<{ name: string; key: string; api_client_keys: string } | null>(null);
  const [maintMsg, setMaintMsg] = useState("");

  useEffect(() => {
    try { setToken(sessionStorage.getItem(TOKEN_KEY)); } catch { /* private mode: just ask again */ }
  }, []);

  const call = useCallback(async <T,>(path: string, init: RequestInit = {}): Promise<T> => {
    const r = await fetch(`/api/admin/${path}`, {
      ...init, cache: "no-store",
      headers: { "content-type": "application/json", "x-admin-token": token ?? "", ...(init.headers ?? {}) },
    });
    if (r.status === 204) return undefined as T;
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      if (r.status === 401) { try { sessionStorage.removeItem(TOKEN_KEY); } catch {} setToken(null); }
      throw new Error(body.detail ?? `Error ${r.status}`);
    }
    return body as T;
  }, [token]);

  const load = useCallback(async () => {
    if (!token) return;
    try {
      const [o, s, e] = await Promise.all([call<Overview>("overview"), call<ScanRow[]>("scans"), call<Event[]>("events")]);
      setOv(o); setScans(s); setEvents(e); setError(null);
      setMaintMsg((m) => m || o.maintenance.message);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [token, call]);

  useEffect(() => {
    if (!token) return;
    load();
    const t = setInterval(load, 10_000);   // live: refresh every 10 seconds
    return () => clearInterval(t);
  }, [token, load]);

  function signIn(e: React.FormEvent) {
    e.preventDefault();
    const t = typed.trim();
    if (!t) return;
    try { sessionStorage.setItem(TOKEN_KEY, t); } catch {}
    setToken(t); setTyped(""); setError(null);
  }
  function signOut() {
    try { sessionStorage.removeItem(TOKEN_KEY); } catch {}
    setToken(null); setOv(null);
  }
  async function act(fn: () => Promise<unknown>, done: string) {
    try { await fn(); setNotice(done); await load(); } catch (err) { setNotice((err as Error).message); }
    setTimeout(() => setNotice(null), 5000);
  }

  if (!token) {
    return (
      <main className="wrap" style={{ display: "grid", gap: 20, maxWidth: 440, paddingTop: 60 }}>
        <BrandMark />
        <form className="panel" onSubmit={signIn} style={{ display: "grid", gap: 12 }}>
          <h1 style={{ fontSize: 26 }}>Admin</h1>
          <label className="eyebrow" htmlFor="pw">Admin password</label>
          <input id="pw" type="password" autoComplete="current-password" value={typed} onChange={(e) => setTyped(e.target.value)}
            placeholder="The ADMIN_TOKEN you set in Render" />
          {error && <p className="loss" style={{ margin: 0 }}>{error}</p>}
          <button className="btn primary" type="submit">Sign in</button>
          <p style={{ color: "var(--muted)", fontSize: 13, margin: 0 }}>Remembered for this tab only.</p>
        </form>
      </main>
    );
  }

  const busy = ov ? ["queued", "scanning", "pricing", "valuing"].reduce((n, s) => n + (ov.scans.by_status[s] ?? 0), 0) : 0;

  return (
    <main className="wrap" style={{ display: "grid", gap: 20, paddingBlock: 28 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row" style={{ gap: 14 }}><BrandMark /><span className="chip">Admin</span></div>
        <div className="row" style={{ gap: 8 }}>
          <button className="btn" onClick={load}>Refresh</button>
          <button className="btn" onClick={signOut}>Sign out</button>
        </div>
      </div>

      {error && <div className="panel" role="alert"><Say mood="mad">{error}</Say></div>}
      {notice && <div className="panel" role="status" style={{ borderColor: "var(--gold)" }}>{notice}</div>}
      {!ov && !error && <div className="panel"><Say mood="shh">Loading…</Say></div>}

      {ov && <>
        {ov.maintenance.on && (
          <div className="panel" style={{ borderColor: "var(--warn)" }}>
            <strong>Maintenance mode is ON.</strong> New scans are paused. Visitors see: “{ov.maintenance.message}”
          </div>
        )}

        <section className="grid4">
          <Stat label="Up for" value={duration(ov.uptime_seconds)} sub={`since ${new Date(ov.started_at).toLocaleString()}${ov.version ? ` · build ${ov.version}` : ""}`} />
          <Stat label="Scans in memory" value={String(ov.scans.in_memory)} sub={`${busy} running · ${ov.scans.transactions_in_memory.toLocaleString()} transactions`} />
          <Stat label="Scans since start" value={String(ov.scans.started)} sub={`${ov.scans.finished} finished · ${ov.scans.failed} failed`} />
          <Stat label="Rate limited" value={String(Object.values(ov.rate_limited).reduce((a, b) => a + b, 0))}
            sub={Object.entries(ov.rate_limited).map(([k, v]) => `${LIMIT_NAMES[k] ?? k}: ${v}`).join(" · ") || "nobody yet"} />
        </section>

        <section className="panel" style={{ display: "grid", gap: 12 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <h2>Data providers</h2>
            <button className="btn" disabled={checking}
              onClick={async () => { setChecking(true); try { setChecks(await call<Check[]>("health", { method: "POST" })); } catch (e) { setNotice((e as Error).message); } setChecking(false); }}>
              {checking ? "Checking…" : "Run health check"}
            </button>
          </div>
          {checks ? (
            <div className="scroll"><table>
              <thead><tr><th>Provider</th><th>Status</th><th className="r">Time</th></tr></thead>
              <tbody>{checks.map((c) => (
                <tr key={c.name}><td>{c.name}</td>
                  <td>{c.ok ? <span className="gain">● Working</span> : <span className="loss">● {c.error ?? "Failed"}</span>}</td>
                  <td className="r num">{c.ms} ms</td></tr>
              ))}</tbody>
            </table></div>
          ) : <p style={{ color: "var(--muted)", margin: 0 }}>Checks every network and price source in about 10 seconds.</p>}
          {Object.keys(ov.provider_errors).length > 0 && (
            <p style={{ margin: 0, color: "var(--muted)" }}>Errors during scans since start: {Object.entries(ov.provider_errors).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>
          )}
        </section>

        <section className="panel" style={{ display: "grid", gap: 12 }}>
          <h2>Settings and keys</h2>
          <div className="scroll"><table>
            <thead><tr><th>Setting</th><th>Used for</th><th>Status</th></tr></thead>
            <tbody>{ov.keys.map((k) => (
              <tr key={k.name}><td className="mono">{k.name}</td><td>{k.what}</td>
                <td>{k.set ? <span className="gain">Set</span> : <span style={{ color: "var(--muted)" }}>Not set</span>}</td></tr>
            ))}</tbody>
          </table></div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
            Values are never shown. Change them in Render → madboy-chainscope-api → Environment.
            Websites allowed to call the API: {ov.cors_origins.length ? ov.cors_origins.join(", ") : "none listed"}.
          </p>
        </section>

        <section className="panel" style={{ display: "grid", gap: 12 }}>
          <h2>Scans</h2>
          {Object.keys(ov.networks).length > 0 && (
            <div className="row" style={{ gap: 6 }}>
              {Object.entries(ov.networks).map(([n, c]) => <span key={n} className={`chip ${n}`}>{n} · {c}</span>)}
            </div>
          )}
          {scans.length === 0 ? <p style={{ color: "var(--muted)", margin: 0 }}>No scans in memory right now.</p> : (
            <div className="scroll" style={{ maxHeight: 420 }}><table>
              <thead><tr><th>Started</th><th>Status</th><th>Networks</th><th className="r">Addresses</th><th className="r">Transactions</th><th className="r">NFTs</th><th></th></tr></thead>
              <tbody>{scans.map((s) => (
                <tr key={s.id}>
                  <td>{since(s.created_at)}{s.demo ? " · example" : ""}</td>
                  <td>{s.status}{s.network_errors ? ` · ${s.network_errors} network issue${s.network_errors > 1 ? "s" : ""}` : ""}{s.error ? <div className="loss" style={{ fontSize: 12 }}>{s.error}</div> : null}</td>
                  <td>{Object.keys(s.networks).join(", ")}</td>
                  <td className="r num">{s.addresses}</td>
                  <td className="r num">{s.transactions.toLocaleString()}</td>
                  <td className="r num">{s.nfts}</td>
                  <td className="r"><button className="btn" onClick={() => act(() => call(`scans/${s.id}`, { method: "DELETE" }), "Scan deleted.")}>Delete</button></td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
          <div className="row" style={{ gap: 8 }}>
            <button className="btn" onClick={() => act(() => call("scans", { method: "DELETE" }), "Finished scans cleared.")}>Clear finished scans</button>
            <button className="btn" onClick={() => { if (confirm("Delete every scan, including ones running now?")) act(() => call("scans?include_running=true", { method: "DELETE" }), "All scans cleared."); }}>Clear all scans</button>
          </div>
        </section>

        <section style={{ display: "grid", gap: 20, gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))" }}>
          <div className="panel" style={{ display: "grid", gap: 12, alignContent: "start" }}>
            <h2>Rate limits</h2>
            <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>Per person. Changes last until the server restarts; make them permanent with the RATE_ settings in Render. Partner keys get {ov.partner_multiplier}× these.</p>
            {Object.entries(ov.limits).filter(([k]) => k !== "admin_auth").map(([k, r]) => (
              <LimitRow key={k} kind={k} label={LIMIT_NAMES[k] ?? k} rule={r}
                onSave={(per_minute, burst) => act(() => call("limits", { method: "PUT", body: JSON.stringify({ kind: k, per_minute, burst }) }), "Limit updated.")} />
            ))}
          </div>

          <div className="panel" style={{ display: "grid", gap: 12, alignContent: "start" }}>
            <h2>Controls</h2>
            <label className="eyebrow" htmlFor="maint">Maintenance message</label>
            <input id="maint" value={maintMsg} onChange={(e) => setMaintMsg(e.target.value)} maxLength={300} style={{ fontFamily: "var(--body)" }} />
            <button className="btn primary" onClick={() => act(() => call("maintenance", { method: "PUT", body: JSON.stringify({ on: !ov.maintenance.on, message: maintMsg }) }),
              ov.maintenance.on ? "Maintenance mode off: scans are open again." : "Maintenance mode on: new scans paused.")}>
              {ov.maintenance.on ? "Turn maintenance off" : "Pause new scans"}
            </button>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn" onClick={() => act(() => call("cache/clear", { method: "POST", body: JSON.stringify({ target: "prices" }) }), "Price cache cleared.")}>Clear price cache</button>
              <button className="btn" onClick={() => act(() => call("cache/clear", { method: "POST", body: JSON.stringify({ target: "floors" }) }), "NFT floor cache cleared.")}>Clear floor cache</button>
            </div>
          </div>

          <div className="panel" style={{ display: "grid", gap: 12, alignContent: "start" }}>
            <h2>Partner API keys</h2>
            <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
              Current partners: {ov.partners.length ? ov.partners.join(", ") : "none"}. A new key isn't saved anywhere: copy the line below into API_CLIENT_KEYS in Render.
            </p>
            <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
              <input value={partner} onChange={(e) => setPartner(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))} placeholder="partner name, e.g. wallet-app" />
              <button className="btn" disabled={partner.length < 2}
                onClick={async () => { try { setNewKey(await call("partner-key", { method: "POST", body: JSON.stringify({ name: partner }) })); } catch (e) { setNotice((e as Error).message); } }}>
                Create
              </button>
            </div>
            {newKey && (
              <div style={{ display: "grid", gap: 6 }}>
                <span className="eyebrow">Key for {newKey.name} (shown once)</span>
                <code className="mono" style={{ wordBreak: "break-all", background: "var(--sunk)", padding: 10, borderRadius: 8 }}>{newKey.key}</code>
                <span className="eyebrow">New API_CLIENT_KEYS value</span>
                <code className="mono" style={{ wordBreak: "break-all", background: "var(--sunk)", padding: 10, borderRadius: 8 }}>{newKey.api_client_keys}</code>
              </div>
            )}
          </div>
        </section>

        <section className="panel" style={{ display: "grid", gap: 12 }}>
          <h2>Recent activity</h2>
          {events.length === 0 ? <p style={{ color: "var(--muted)", margin: 0 }}>Nothing yet since the server started.</p> : (
            <div className="scroll" style={{ maxHeight: 360 }}><table>
              <tbody>{events.map((e, i) => (
                <tr key={i}><td className="num" style={{ whiteSpace: "nowrap", color: "var(--muted)" }}>{since(e.at)}</td>
                  <td><span className="chip" style={e.kind === "scan_failed" || e.kind === "provider_error" ? { color: "var(--loss)" } : undefined}>{e.kind.replace(/_/g, " ")}</span></td>
                  <td>{e.message}</td></tr>
              ))}</tbody>
            </table></div>
          )}
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>Kept in memory only, without wallet addresses. Clears when the server restarts.</p>
        </section>
      </>}
    </main>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="panel stat">
      <span className="eyebrow">{label}</span>
      <div className="v">{value}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  );
}

function LimitRow({ kind, label, rule, onSave }: { kind: string; label: string; rule: { per_minute: number; burst: number };
  onSave: (perMinute: number, burst: number) => void }) {
  const hourly = kind === "scan";
  const [rate, setRate] = useState(String(hourly ? Math.round(rule.per_minute * 60) : rule.per_minute));
  const [burst, setBurst] = useState(String(rule.burst));
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <span style={{ fontWeight: 500 }}>{label}</span>
      <div className="row" style={{ gap: 8, flexWrap: "nowrap", alignItems: "center" }}>
        <input aria-label={`${label} per ${hourly ? "hour" : "minute"}`} value={rate} onChange={(e) => setRate(e.target.value.replace(/[^0-9.]/g, ""))} style={{ width: 0, flex: "1 1 56px", minWidth: 0 }} />
        <span style={{ color: "var(--muted)", fontSize: 13, whiteSpace: "nowrap", flex: "none" }}>per {hourly ? "hour" : "min"}, burst</span>
        <input aria-label={`${label} burst`} value={burst} onChange={(e) => setBurst(e.target.value.replace(/[^0-9]/g, ""))} style={{ width: 0, flex: "1 1 48px", minWidth: 0 }} />
        <button className="btn" style={{ flex: "none" }} onClick={() => {
          const r = Number(rate), b = Number(burst);
          if (r > 0 && b > 0) onSave(hourly ? r / 60 : r, b);
        }}>Save</button>
      </div>
    </div>
  );
}
