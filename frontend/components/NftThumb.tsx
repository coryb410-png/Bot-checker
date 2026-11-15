"use client";

import { useEffect, useMemo, useRef, useState } from "react";

// Many NFT pictures live on IPFS, and the free public gateways are often slow
// or refuse requests. For an IPFS picture, try a few gateways in turn: move on
// when one errors or hasn't loaded within a few seconds.
const GATEWAYS = ["https://ipfs.io/ipfs/", "https://w3s.link/ipfs/", "https://nftstorage.link/ipfs/",
                  "https://dweb.link/ipfs/", "https://gateway.pinata.cloud/ipfs/"];
const CID = /^(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{50,})$/;

export function imageCandidates(src: string): string[] {
  let path: string | null = null;
  try {
    const u = new URL(src);
    const sub = u.hostname.match(/^([a-z0-9]+)\.ipfs\./);            // https://<cid>.ipfs.dweb.link/x
    if (sub && CID.test(sub[1])) path = sub[1] + u.pathname.replace(/\/$/, "");
    else {
      const m = u.pathname.match(/\/ipfs\/(.+)$/);                     // https://any.gateway/ipfs/<cid>/x
      if (m && CID.test(m[1].split("/")[0])) path = m[1];
    }
  } catch { return [src]; }
  if (!path) return [src];
  return [src, ...GATEWAYS.map((g) => g + path).filter((u) => u !== src)];
}

// NFT images come from whoever minted the token. They load with no referrer,
// fall back to a placeholder if broken, and likely-spam items stay hidden
// until the viewer chooses to reveal them.
// `link={false}` renders a plain image, for use inside another link or button.
// size="fill" makes a square that fills its container (gallery tiles).
export default function NftThumb({ src, name, spam, size = 56, link = true }: { src: string | null; name: string; spam?: boolean; size?: number | "fill"; link?: boolean }) {
  const candidates = useMemo(() => (src ? imageCandidates(src) : []), [src]);
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const broken = attempt >= candidates.length;
  const url = candidates[attempt];
  const next = () => { setLoaded(false); setAttempt((a) => a + 1); };
  useEffect(() => { setAttempt(0); setLoaded(false); }, [src]);
  // Only time a gateway once the picture is on screen (they load lazily)
  const ref = useRef<HTMLImageElement | null>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || inView) return;
    if (typeof IntersectionObserver === "undefined") { setInView(true); return; }
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { setInView(true); io.disconnect(); } });
    io.observe(el);
    return () => io.disconnect();
  });
  // A gateway that hangs counts as failed after 8 seconds, if there's another to try
  useEffect(() => {
    if (loaded || !url || !inView || attempt >= candidates.length - 1) return;
    const t = setTimeout(next, 8000);
    return () => clearTimeout(t);
  }, [url, loaded, inView, attempt, candidates.length]);
  const [show, setShow] = useState(!spam);
  const box: React.CSSProperties = {
    ...(size === "fill" ? { width: "100%", aspectRatio: "1 / 1", maxWidth: "100%" } : { width: size, height: size }),
    borderRadius: 8, flex: "none", background: "var(--sunk)",
    border: "1px solid var(--line)", overflow: "hidden", display: "grid", placeItems: "center",
  };
  if (!src || broken) {
    return <span style={{ ...box, font: "600 10px var(--mono)", color: "var(--muted)" }} aria-label={`${name}, no image`}>NFT</span>;
  }
  if (!show && !link) {
    return <span style={{ ...box, font: "600 10px var(--mono)", color: "var(--warn)" }} aria-label={`${name}, hidden as likely spam`}>SPAM?</span>;
  }
  if (!show) {
    return (
      <button type="button" onClick={() => setShow(true)} title="Likely spam. Tap to show the image."
        style={{ ...box, font: "600 10px var(--mono)", color: "var(--warn)", cursor: "pointer", padding: 0 }}>
        SPAM?
      </button>
    );
  }
  const img = <img key={url} ref={ref} src={url} alt={name} loading="lazy" referrerPolicy="no-referrer" onError={next}
    onLoad={() => setLoaded(true)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />;
  if (!link) return <span style={box}>{img}</span>;
  return (
    <a href={url} target="_blank" rel="noreferrer noopener" style={box} title={name}>{img}</a>
  );
}
