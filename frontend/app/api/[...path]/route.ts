// Same-origin proxy: the browser calls /api/* on this site, and the server
// forwards to the ChainScope backend. The backend address is read at runtime
// (API_URL), so it never has to be baked into the build, and there are no
// cross-site (CORS) requests from the browser.
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BACKEND = (process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || "").replace(/\/+$/, "");

async function forward(req: NextRequest, { params }: { params: { path: string[] } }) {
  if (!BACKEND) {
    return Response.json({ detail: "The site isn't connected to its backend yet (API_URL is not set)." }, { status: 503 });
  }
  const url = `${BACKEND}/api/${params.path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers: Record<string, string> = { "content-type": req.headers.get("content-type") ?? "application/json" };
  // Pass the visitor's IP in our own header (hosts rewrite x-forwarded-for)
  const ip = (req.headers.get("x-forwarded-for") ?? req.ip ?? "").split(",")[0].trim();
  if (ip) headers["x-chainscope-client"] = ip;

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
        { status: r.status },
      );
    }
    return new Response(r.status === 204 ? null : text, {
      status: r.status,
      headers: { "content-type": r.headers.get("content-type") ?? "application/json" },
    });
  } catch (e) {
    console.error(`[proxy] ${req.method} ${url} failed:`, e);
    return Response.json(
      { detail: "The ChainScope server is waking up or unreachable. Wait about 30 seconds and try again." },
      { status: 502 },
    );
  }
}

export { forward as GET, forward as POST, forward as PUT, forward as DELETE };
