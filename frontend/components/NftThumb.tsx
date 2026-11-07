"use client";

import { useState } from "react";

// NFT images come from whoever minted the token. They load with no referrer,
// fall back to a placeholder if broken, and likely-spam items stay hidden
// until the viewer chooses to reveal them.
// `link={false}` renders a plain image, for use inside another link or button.
export default function NftThumb({ src, name, spam, size = 56, link = true }: { src: string | null; name: string; spam?: boolean; size?: number; link?: boolean }) {
  const [broken, setBroken] = useState(false);
  const [show, setShow] = useState(!spam);
  const box: React.CSSProperties = {
    width: size, height: size, borderRadius: 8, flex: "none", background: "var(--sunk)",
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
  const img = <img src={src} alt={name} loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)}
    style={{ width: "100%", height: "100%", objectFit: "cover" }} />;
  if (!link) return <span style={box}>{img}</span>;
  return (
    <a href={src} target="_blank" rel="noreferrer noopener" style={box} title={name}>
      <img src={src} alt={name} loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)}
        style={{ width: "100%", height: "100%", objectFit: "cover" }} />
    </a>
  );
}
