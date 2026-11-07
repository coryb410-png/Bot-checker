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
  // Keep the visitor's IP so per-visitor rate limits still work
  const ip = req.headers.get("x-forwarded-for") ?? req.ip;
  if (ip) headers["x-forwarded-for"] = ip;

  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.text();
  try {
    const r = await fetch(url, { method: req.method, headers, body, cache: "no-store" });
    return new Response(r.status === 204 ? null : await r.text(), {
      status: r.status,
      headers: { "content-type": r.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json(
      { detail: "The ChainScope server is waking up or unreachable. Wait a moment and try again." },
      { status: 502 },
    );
  }
}

export { forward as GET, forward as POST, forward as PUT, forward as DELETE };
