// Instant client-side checks. The API re-validates with full checksums.
export type Chain = "bitcoin" | "ethereum" | "solana" | "robinhood" | "cardano" | "xrp" | "dogecoin"
  | "base" | "arbitrum" | "optimism" | "polygon";

// Every 0x address is also scanned on these Ethereum-style networks
export const EVM_EXTRAS: Chain[] = ["base", "arbitrum", "optimism", "polygon", "robinhood"];

export const CHAIN_LABEL: Record<Chain, string> = {
  bitcoin: "Bitcoin",
  ethereum: "Ethereum",
  solana: "Solana",
  robinhood: "Robinhood Chain",
  base: "Base",
  arbitrum: "Arbitrum",
  optimism: "Optimism",
  polygon: "Polygon",
  cardano: "Cardano",
  xrp: "XRP Ledger",
  dogecoin: "Dogecoin",
};

const B58 = "[1-9A-HJ-NP-Za-km-z]";

export function detectChain(raw: string): Chain | null {
  const a = raw.trim();
  if (/^0x[0-9a-fA-F]{40}$/.test(a)) return "ethereum";
  if (/^(bc1q[02-9ac-hj-np-z]{38}|bc1q[02-9ac-hj-np-z]{58}|bc1p[02-9ac-hj-np-z]{58})$/i.test(a)) return "bitcoin";
  if (new RegExp(`^[13]${B58}{25,34}$`).test(a)) return "bitcoin";
  if (/^(addr1|stake1)[02-9ac-hj-np-z]{50,}$/i.test(a)) return "cardano";
  if (new RegExp(`^(Ae2${B58}{52,62}|DdzFF${B58}{90,125})$`).test(a)) return "cardano";   // legacy Byron
  // XRP Ledger: r… and 25–35 characters. Solana addresses are almost always
  // 43–44, and the server checks the XRP checksum before scanning.
  if (new RegExp(`^r${B58}{24,34}$`).test(a)) return "xrp";
  // Dogecoin: D… (or A…/9…), 34 characters. Solana addresses are almost always
  // 43–44; the server checks the Dogecoin checksum before scanning.
  if (new RegExp(`^[DA9]${B58}{33}$`).test(a)) return "dogecoin";
  if (new RegExp(`^${B58}{32,44}$`).test(a)) return "solana";
  return null;
}

// name.eth (ENS), name.sol (Solana Name Service) or $handle (ADA Handle)
export function isName(raw: string): boolean {
  const t = raw.trim();
  return /^[^\s/\\]{1,250}\.(eth|sol)$/i.test(t) || /^\$[a-z0-9_.-]{1,15}$/i.test(t);
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
