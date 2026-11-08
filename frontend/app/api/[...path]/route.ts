// Same-origin proxy: the browser calls /api/* on this site, and the server
// forwards to the ChainScope backend. The backend address is read at runtime
// (API_URL), so it never has to be baked into the build, and there are no
// cross-site (CORS) requests from the browser.
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BACKEND = (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "").replace(/\/+$/, "");
// A sleeping free Render server isn't woken by requests from this server
// (another Render service), so the visitor's browser is told where the backend
// is and pings it directly; that outside visit wakes it.
const WAKING_HEADERS = { "x-chainscope-waking": "1", "x-chainscope-backend": BACKEND };

async function forward(req: NextRequest, { params }: { params: { path: string[] } }) {
  if (!BACKEND) {
    return Response.json({ detail: "The site isn't connected to its backend yet (API_URL is not set)." }, { status: 503 });
  }
  const url = `${BACKEND}/api/${params.path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = { "content-type": req.headers.get("content-type") ?? "application/json" };
  // Tell the backend who the visitor is, for per-person rate limits. The last
  // X-Forwarded-For entry is the one Render's edge added (earlier ones can be
  // forged by the caller), and the shared secret proves the request came from
  // this site, so nobody else can claim to be passing visitors' addresses.
  const hops = (req.headers.get("x-forwarded-for") ?? "").split(",").map((h) => h.trim()).filter(Boolean);
  const ip = hops[hops.length - 1] ?? req.ip ?? "";
  if (ip) headers["x-chainscope-client"] = ip;
  if (process.env.PROXY_SHARED_SECRET) headers["x-chainscope-proxy-key"] = process.env.PROXY_SHARED_SECRET;

  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.text();
  try {
    const r = await fetch(url, { method: req.method, headers, body, cache: "no-store", signal: AbortSignal.timeout(90_000) });
    const text = r.status === 204 ? "" : await r.text();
    const isJson = (r.headers.get("content-type") ?? "").includes("json");
    if (!r.ok && !isJson) {
      // The host answered instead of our backend (asleep, restarting or down)
      console.error(`[proxy] ${req.method} ${url} -> ${r.status}: ${text.slice(0, 300)}`);
      return Response.json(
        { detail: r.status === 502 || r.status === 503
            ? "The ChainScope server is waking up. Wait about 30 seconds and try again."
            : `The ChainScope server answered with an error (${r.status}). Try again in a moment.` },
        { status: r.status, headers: WAKING_HEADERS },
      );
    }
    const out: Record<string, string> = { "content-type": r.headers.get("content-type") ?? "application/json" };
    const retry = r.headers.get("retry-after");
    if (retry) out["retry-after"] = retry;   // "slow down" replies say when to try again
    return new Response(r.status === 204 ? null : text, { status: r.status, headers: out });
  } catch (e) {
    console.error(`[proxy] ${req.method} ${url} failed:`, e);
    return Response.json(
      { detail: "The ChainScope server is waking up or unreachable. Wait about 30 seconds and try again." },
      { status: 502, headers: WAKING_HEADERS },
    );
  }
}

export { forward as GET, forward as POST, forward as PUT, forward as DELETE };
