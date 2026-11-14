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
  settings_saved: boolean;
  health_interval_minutes: number;
};
type Provider = { name: string; ok: boolean; ms: number | null; error?: string | null; uptime: number;
                  checks: number; outages: number; recent: number[] };
type Health = { at: string | null; interval_minutes: number; providers: Provider[] };
type Usage = { hours: { hour: string; started: number; finished: number; failed: number }[];
               networks: { network: string; scans: number; errors: number; avg_seconds: number | null;
                           slowest_seconds: number | null; typical_seconds: number | null }[] };
type NetSwitch = { network: string; name: string; on: boolean };
type Visitor = { id: string; label: string; requests: number; scans: number; limited: number; last_seen_seconds: number; blocked: boolean };
type Visitors = { visitors: Visitor[]; blocks: { id: string; label: string; minutes_left: number }[] };
type ScanRow = { id: string; status: string; demo: boolean; created_at: string; networks: Record<string, number>;
                 addresses: number; transactions: number; nfts: number; network_errors: number; error: string | null };
type Event = { at: string; kind: string; message: string };

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
  const [health, setHealth] = useState<Health | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [nets, setNets] = useState<NetSwitch[]>([]);
  const [vis, setVis] = useState<Visitors | null>(null);
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
      const [o, s, e, h, u, n, v] = await Promise.all([
        call<Overview>("overview"), call<ScanRow[]>("scans"), call<Event[]>("events"), call<Health>("health"),
        call<Usage>("usage"), call<NetSwitch[]>("networks"), call<Visitors>("visitors")]);
      setOv(o); setScans(s); setEvents(e); setHealth(h); setUsage(u); setNets(n); setVis(v); setError(null);
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

        {!ov.settings_saved && (
          <div className="panel" style={{ borderColor: "var(--warn)", fontSize: 14 }}>
            Settings you change here (limits, maintenance, networks, blocks) <strong>reset when the server restarts</strong>, because the
            Key Value store (CACHE_URL) isn&apos;t reachable right now.
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
              onClick={async () => { setChecking(true); try { setHealth(await call<Health>("health", { method: "POST" })); } catch (e) { setNotice((e as Error).message); } setChecking(false); }}>
              {checking ? "Checking…" : "Check now"}
            </button>
          </div>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
            {health?.interval_minutes ? `Checked automatically every ${health.interval_minutes} minutes` : "Automatic checks are off (HEALTH_CHECK_MINUTES=0)"}
            {health?.at ? ` · last check ${since(health.at)}` : " · first check runs shortly after the server starts"}. Uptime covers the last 24 hours while the server was awake.
          </p>
          {health && health.providers.length > 0 && (
            <div className="scroll"><table>
              <thead><tr><th>Provider</th><th>Now</th><th className="r">Uptime</th><th className="r">Outages</th><th>Recent checks</th><th className="r">Time</th></tr></thead>
              <tbody>{[...health.providers].sort((a, b) => Number(a.ok) - Number(b.ok) || a.uptime - b.uptime).map((c) => (
                <tr key={c.name}><td>{c.name}</td>
                  <td>{c.ok ? <span className="gain">● Working</span> : <span className="loss">● {c.error ?? "Failed"}</span>}</td>
                  <td className="r num">{c.uptime}%</td>
                  <td className="r num">{c.outages}</td>
                  <td><Dots recent={c.recent} /></td>
                  <td className="r num">{c.ms != null ? `${c.ms} ms` : "–"}</td></tr>
              ))}</tbody>
            </table></div>
          )}
          {Object.keys(ov.provider_errors).length > 0 && (
            <p style={{ margin: 0, color: "var(--muted)" }}>Errors during scans since start: {Object.entries(ov.provider_errors).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>
          )}
        </section>

        <section className="panel" style={{ display: "grid", gap: 12 }}>
          <h2>Networks</h2>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
            Turn a network off if its provider is failing. Scans skip it and tell visitors it&apos;s off for now; every other network still scans.
          </p>
          <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))" }}>
            {nets.map((n) => (
              <div key={n.network} className="row" style={{ justifyContent: "space-between", gap: 8, flexWrap: "nowrap",
                padding: "8px 10px", border: "1px solid var(--line)", borderRadius: 10, background: n.on ? undefined : "var(--sunk)" }}>
                <span className={`chip ${n.network}`} style={{ opacity: n.on ? 1 : 0.55 }}>{n.name}</span>
                <span className="seg" role="group" aria-label={`${n.name} on or off`}>
                  <button aria-pressed={n.on} onClick={() => !n.on && act(() => call("networks", { method: "PUT", body: JSON.stringify({ network: n.network, on: true }) }), `${n.name} turned on.`)}>On</button>
                  <button aria-pressed={!n.on} onClick={() => n.on && act(() => call("networks", { method: "PUT", body: JSON.stringify({ network: n.network, on: false }) }), `${n.name} turned off.`)}>Off</button>
                </span>
              </div>
            ))}
          </div>
        </section>

        {usage && <UsagePanel usage={usage} />}

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
            <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>Per person. Partner keys get {ov.partner_multiplier}× these.</p>
            {Object.entries(ov.limits).filter(([k]) => k !== "admin_auth").map(([k, r]) => (
              <LimitRow key={k} kind={k} label={LIMIT_NAMES[k] ?? k} rule={r}
                onSave={(per_minute, burst) => act(() => call("limits", { method: "PUT", body: JSON.stringify({ kind: k, per_minute, burst }) }), "Limit updated.")} />
            ))}
            <button className="btn" style={{ justifySelf: "start" }} onClick={() => act(() => call("limits", { method: "DELETE" }), "Rate limits reset to the defaults.")}>Reset to defaults</button>
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

        {vis && <VisitorsPanel vis={vis}
          onBlock={(v, hours) => act(() => call("blocks", { method: "POST", body: JSON.stringify({ id: v.id, hours }) }),
            `Blocked ${v.label === "Visitor" ? v.id : v.label} for ${hours >= 24 ? `${hours / 24} day${hours > 24 ? "s" : ""}` : `${hours} hour`}.`)}
          onUnblock={(id) => act(() => call(`blocks/${encodeURIComponent(id)}`, { method: "DELETE" }), "Unblocked.")} />}

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

function Dots({ recent }: { recent: number[] }) {
  // One dot per check, oldest on the left; a red dot is a failed check
  return (
    <span aria-label={`${recent.filter((r) => !r).length} failed of the last ${recent.length} checks`}
      style={{ display: "inline-flex", gap: 2, alignItems: "center", flexWrap: "nowrap" }}>
      {recent.map((ok, i) => (
        <span key={i} style={{ width: 4, height: 12, borderRadius: 2, background: ok ? "var(--gain)" : "var(--loss)", opacity: ok ? 0.75 : 1 }} />
      ))}
    </span>
  );
}

function hourLabel(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric" });
}

function UsagePanel({ usage }: { usage: Usage }) {
  const [hover, setHover] = useState<number | null>(null);
  const hours = usage.hours;
  const max = Math.max(1, ...hours.map((h) => h.started));
  const W = 720, H = 160, padL = 28, padB = 22, gap = 2;
  const bw = (W - padL) / hours.length;
  const y = (v: number) => (H - padB) - (v / max) * (H - padB - 8);
  const ticks = max <= 4 ? Array.from({ length: max + 1 }, (_, i) => i) : [0, Math.round(max / 2), max];
  const total = hours.reduce((a, h) => a + h.started, 0);
  const slowest = Math.max(1, ...usage.networks.map((n) => n.typical_seconds ?? 0));
  const h = hover != null ? hours[hover] : null;
  return (
    <section className="panel" style={{ display: "grid", gap: 14 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>Usage</h2>
        <span style={{ color: "var(--muted)", fontSize: 13 }}>{total} scan{total === 1 ? "" : "s"} in the last 24 hours</span>
      </div>
      <div style={{ position: "relative" }}>
        <span className="eyebrow">Scans started per hour</span>
        <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block", marginTop: 6 }}
          role="img" aria-label="Scans started per hour over the last 24 hours" onMouseLeave={() => setHover(null)}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={padL} x2={W} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeWidth={t === 0 ? 1 : 0.6} strokeDasharray={t === 0 ? undefined : "2 4"} />
              <text x={padL - 6} y={y(t) + 4} textAnchor="end" fontSize="10" fill="var(--muted)" fontFamily="var(--mono)">{t}</text>
            </g>
          ))}
          {hours.map((hr, i) => {
            const x = padL + i * bw + gap / 2, w = Math.max(1, bw - gap);
            const top = y(hr.started), base = y(0);
            const failTop = y(hr.failed);
            return (
              <g key={hr.hour}>
                {hr.started > 0 && <path d={`M${x},${base} V${top + 3} Q${x},${top} ${x + 3},${top} H${x + w - 3} Q${x + w},${top} ${x + w},${top + 3} V${base} Z`}
                  fill="var(--gold)" opacity={hover == null || hover === i ? 1 : 0.45} />}
                {hr.failed > 0 && <rect x={x} y={failTop} width={w} height={base - failTop} fill="var(--loss)" opacity={0.9} />}
                {i % 4 === 0 && <text x={x + w / 2} y={H - 6} textAnchor="middle" fontSize="10" fill="var(--muted)" fontFamily="var(--mono)">{hourLabel(hr.hour)}</text>}
                <rect x={padL + i * bw} y={0} width={bw} height={H - padB} fill="transparent" onMouseEnter={() => setHover(i)} onTouchStart={() => setHover(i)} />
              </g>
            );
          })}
        </svg>
        {h && hover != null && (
          <div className="panel" role="status" style={{ position: "absolute", top: 18, left: `${Math.min(80, ((padL + hover * bw) / W) * 100)}%`,
            padding: "8px 10px", fontSize: 12, pointerEvents: "none", whiteSpace: "nowrap", zIndex: 2 }}>
            <strong>{hourLabel(h.hour)}</strong><br />
            {h.started} started · {h.finished} finished{h.failed ? <> · <span className="loss">{h.failed} failed</span></> : null}
          </div>
        )}
        <div className="row" style={{ gap: 14, fontSize: 12, color: "var(--muted)", marginTop: 4 }}>
          <span><span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, background: "var(--gold)", marginRight: 6, verticalAlign: -1 }} />Scans started</span>
          <span><span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, background: "var(--loss)", marginRight: 6, verticalAlign: -1 }} />Of those, failed</span>
        </div>
      </div>
      {usage.networks.length > 0 && (
        <div className="scroll"><table>
          <thead><tr><th>Network</th><th className="r">Scans</th><th>Typical scan time</th><th className="r">Slowest</th><th className="r">Errors</th></tr></thead>
          <tbody>{usage.networks.map((n) => (
            <tr key={n.network}>
              <td><span className={`chip ${n.network}`}>{n.network}</span></td>
              <td className="r num">{n.scans}</td>
              <td>{n.typical_seconds != null ? (
                <span className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                  <span style={{ display: "inline-block", height: 6, borderRadius: 3, background: "var(--gold)", width: `${Math.max(3, (n.typical_seconds / slowest) * 120)}px` }} />
                  <span className="num">{n.typical_seconds}s</span>
                </span>) : <span style={{ color: "var(--muted)" }}>–</span>}</td>
              <td className="r num">{n.slowest_seconds != null ? `${n.slowest_seconds}s` : "–"}</td>
              <td className="r num">{n.errors ? <span className="loss">{n.errors}</span> : 0}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>Counts since the server last started; nothing is saved.</p>
    </section>
  );
}

function VisitorsPanel({ vis, onBlock, onUnblock }: { vis: Visitors; onBlock: (v: Visitor, hours: number) => void; onUnblock: (id: string) => void }) {
  const [hours, setHours] = useState(24);
  return (
    <section className="panel" style={{ display: "grid", gap: 12 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2>Visitors</h2>
        <span className="row" style={{ gap: 8, fontSize: 13, color: "var(--muted)" }}>Block for
          <span className="seg" role="group" aria-label="Block length">
            {[[1, "1 hour"], [24, "1 day"], [168, "7 days"]].map(([h, l]) => (
              <button key={h} aria-pressed={hours === h} onClick={() => setHours(h as number)}>{l}</button>
            ))}
          </span>
        </span>
      </div>
      <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
        The busiest visitors in the last hour, by a scrambled ID (never their IP). Blocking stops all their requests until it runs out.
        People on the same Wi-Fi or office network share one ID.
      </p>
      {vis.visitors.length === 0 ? <p style={{ color: "var(--muted)", margin: 0 }}>No visitors in the last hour.</p> : (
        <div className="scroll" style={{ maxHeight: 380 }}><table>
          <thead><tr><th>Visitor</th><th className="r">Requests</th><th className="r">Scans</th><th className="r">Rate limited</th><th className="r">Last seen</th><th></th></tr></thead>
          <tbody>{vis.visitors.map((v) => (
            <tr key={v.id}>
              <td><span className="mono">{v.label === "Visitor" ? v.id : v.label}</span></td>
              <td className="r num">{v.requests}</td>
              <td className="r num">{v.scans}</td>
              <td className="r num">{v.limited ? <span className="loss">{v.limited}</span> : 0}</td>
              <td className="r num">{v.last_seen_seconds < 60 ? "now" : `${Math.round(v.last_seen_seconds / 60)}m ago`}</td>
              <td className="r">{v.blocked ? <span className="loss">Blocked</span> :
                <button className="btn" onClick={() => { if (confirm(`Block ${v.label === "Visitor" ? v.id : v.label}?`)) onBlock(v, hours); }}>Block</button>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      {vis.blocks.length > 0 && (
        <div style={{ display: "grid", gap: 6 }}>
          <span className="eyebrow">Blocked now</span>
          {vis.blocks.map((b) => (
            <div key={b.id} className="row" style={{ justifyContent: "space-between", gap: 8 }}>
              <span><span className="mono">{b.label === "Visitor" ? b.id : b.label}</span>
                <span style={{ color: "var(--muted)", fontSize: 13 }}> · {b.minutes_left >= 120 ? `${Math.round(b.minutes_left / 60)}h` : `${b.minutes_left}m`} left</span></span>
              <button className="btn" onClick={() => onUnblock(b.id)}>Unblock</button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
