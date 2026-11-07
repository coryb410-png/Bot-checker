// Instant client-side checks. The API re-validates with full checksums.
export type Chain = "bitcoin" | "ethereum" | "solana" | "robinhood";

export const CHAIN_LABEL: Record<Chain, string> = {
  bitcoin: "Bitcoin",
  ethereum: "Ethereum",
  solana: "Solana",
  robinhood: "Robinhood Chain",
};

const B58 = "[1-9A-HJ-NP-Za-km-z]";

export function detectChain(raw: string): Chain | null {
  const a = raw.trim();
  if (/^0x[0-9a-fA-F]{40}$/.test(a)) return "ethereum";
  if (/^(bc1q[02-9ac-hj-np-z]{38}|bc1q[02-9ac-hj-np-z]{58}|bc1p[02-9ac-hj-np-z]{58})$/i.test(a)) return "bitcoin";
  if (new RegExp(`^[13]${B58}{25,34}$`).test(a)) return "bitcoin";
  if (new RegExp(`^${B58}{32,44}$`).test(a)) return "solana";
  return null;
}

// name.eth (ENS) or name.sol (Solana Name Service)
export function isName(raw: string): boolean {
  return /^[^\s/\\]{1,250}\.(eth|sol)$/i.test(raw.trim());
}

export function looksLikeSecret(raw: string): boolean {
  const t = raw.trim();
  const words = t.split(/\s+/);
  if ([12, 15, 18, 21, 24].includes(words.length) && words.every((w) => /^[a-z]+$/.test(w))) return true;
  if (/^(0x)?[0-9a-fA-F]{64}$/.test(t)) return true;
  if (/^[5KL]/.test(t) && (t.length === 51 || t.length === 52)) return true;
  if (t.length >= 86 && t.length <= 90) return true;
  return false;
}

export function shorten(a: string, n = 6): string {
  return a.length > n * 2 + 1 ? `${a.slice(0, n)}…${a.slice(-n)}` : a;
}
