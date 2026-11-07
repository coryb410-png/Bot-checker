const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    try {
      const body = await r.json();
      msg = typeof body.detail === "string" ? body.detail : msg;
    } catch {}
    throw new Error(msg);
  }
  return r.status === 204 ? (undefined as T) : r.json();
}

export type Method = "fifo" | "average";

export interface Summary {
  portfolio_value: string | null;
  realized_pnl: string | null;
  pnl_complete: boolean;
  transactions: number;
  assets_sold: number;
  fees_paid: string | null;
  warnings: string[];
  spam_hidden: { transactions?: number; assets?: number };
  highlights: Highlights;
}
export interface TradeHighlight {
  tx_hash: string; date: string; label: string; chain: string; source: string | null; realized: string; proceeds: string;
}
export interface FlipHighlight {
  tx_hash: string; buy_tx: string | null; date: string; bought_at: string | null; held_days: number | null;
  name: string; image: string | null; spam: boolean; chain: string; source: string | null;
  cost: string; proceeds: string; realized: string;
}
export interface Highlights {
  best_trade: TradeHighlight | null; worst_trade: TradeHighlight | null;
  best_flip: FlipHighlight | null; worst_flip: FlipHighlight | null;
  trade_count?: number; flip_count?: number;
}
export interface WalletStatus {
  id: string;
  status: "queued" | "scanning" | "pricing" | "ready" | "error";
  error: string | null;
  method: Method;
  demo: boolean;
  addresses: { chain: string; address: string; label: string | null; scanned: number }[];
  updated_at: string | null;
  summary: Summary | null;
}
export interface Leg {
  asset: string; name: string | null; nft: boolean; token_id: string | null; image: string | null; spam: boolean;
  amount: string; usd: string | null; counterparty: string | null;
}
export interface Tx {
  hash: string; chain: string; wallet: string; date: string; type: string; label: string; spam: boolean;
  source: string | null; success: boolean; legs: Leg[]; fee: string; fee_usd: string | null;
  usd_in: string; usd_out: string; has_realized: boolean; realized_pnl: string | null; explorer: string;
}
export interface Holding {
  asset: string; chain: string; key: string; quantity: string;
  nft: boolean; name: string | null; image: string | null; spam: boolean; price: string | null; value: string | null;
  cost_basis: string | null; unrealized_pnl: string | null; unknown_basis_qty: string;
}

export const api = {
  scan: (addresses: string[], method: Method, labels: Record<string, string> = {}) =>
    call<{ id: string }>("/api/wallets/scan", { method: "POST", body: JSON.stringify({ addresses, method, labels }) }),
  resolve: (name: string) =>
    call<{ name: string; chain: "ethereum" | "solana"; address: string }>(`/api/resolve?name=${encodeURIComponent(name.trim())}`),
  demo: (method: Method) => call<{ id: string }>(`/api/demo?method=${method}`, { method: "POST" }),
  status: (id: string) => call<WalletStatus>(`/api/wallets/${id}`),
  refresh: (id: string) => call(`/api/wallets/${id}/refresh`, { method: "POST" }),
  remove: (id: string) => call(`/api/wallets/${id}`, { method: "DELETE" }),
  transactions: (id: string, params: { type?: string; chain?: string; nft?: boolean; include_spam?: boolean; offset?: number; limit?: number }) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== "" && v !== false).map(([k, v]) => [k, String(v)]),
    );
    return call<{ total: number; items: Tx[] }>(`/api/wallets/${id}/transactions?${q}`);
  },
  holdings: (id: string) => call<Holding[]>(`/api/wallets/${id}/holdings`),
  pnl: (id: string, method: Method) => call(`/api/wallets/${id}/pnl?method=${method}`),
  setCost: (id: string, tx_hash: string, total_cost_usd: number) =>
    call(`/api/wallets/${id}/cost-basis`, { method: "PUT", body: JSON.stringify({ tx_hash, total_cost_usd }) }),
};

export function explorerUrl(chain: string, hash: string): string {
  return chain === "bitcoin" ? `https://mempool.space/tx/${hash}`
    : chain === "solana" ? `https://solscan.io/tx/${hash}` : `https://etherscan.io/tx/${hash}`;
}

export function money(v: string | null | undefined, signed = false): string {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  const s = Math.abs(n).toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (!signed) return n < 0 ? `−${s}` : s;
  return n > 0 ? `+${s}` : n < 0 ? `−${s}` : s;
}
