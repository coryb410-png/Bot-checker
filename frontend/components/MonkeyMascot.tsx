"use client";

import { useEffect, useRef, useState } from "react";

// A full-body MADBOY monkey that roams the screen: it walks to a random spot
// every few seconds (arms and legs swinging, tail swaying), faces the way it's
// heading, and changes mood when it stops. The head is the MADBOY logo art; the
// body is drawn to match it (red fill, thick white outline, white belly).
// Purely decorative: clicks pass straight through it, screen readers skip it,
// and with "reduce motion" switched on it just sits in the corner.
type Mood = "mad" | "smirk" | "cash" | "shh";
const MOODS: Mood[] = ["smirk", "smirk", "cash", "cash", "mad", "shh"];   // weighted: mostly smirk/cash
const SPEED = 110;   // pixels per second (a walk, not a dash)

export default function MonkeyMascot() {
  const pos = useRef({ x: 0, y: 0 });
  const [mood, setMood] = useState<Mood>("smirk");
  const [facing, setFacing] = useState<1 | -1>(-1);
  const [moving, setMoving] = useState(false);
  const [dur, setDur] = useState(0);
  const [xy, setXy] = useState<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const size = () => (window.innerWidth < 560 ? 54 : 72);   // width
    const tall = () => (window.innerWidth < 560 ? 80 : 106);
    const corner = () => ({ x: window.innerWidth - size() - 16, y: window.innerHeight - tall() - 12 });
    pos.current = corner();
    setXy(pos.current);

    const still = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (still.matches) return;

    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;

    function wander() {
      if (stopped) return;
      if (document.hidden) { timer = setTimeout(wander, 2000); return; }
      const s = size(), pad = 12;
      const to = {
        x: pad + Math.random() * Math.max(0, window.innerWidth - s - pad * 2),
        y: pad + Math.random() * Math.max(0, window.innerHeight - tall() - pad * 2),
      };
      const dist = Math.hypot(to.x - pos.current.x, to.y - pos.current.y);
      const seconds = Math.min(6, Math.max(1.2, dist / SPEED));
      setFacing(to.x < pos.current.x ? -1 : 1);
      setDur(seconds);
      setMoving(true);
      setXy(to);
      pos.current = to;
      timer = setTimeout(() => {
        setMoving(false);
        setMood(MOODS[Math.floor(Math.random() * MOODS.length)]);
        timer = setTimeout(wander, 2500 + Math.random() * 3500);   // pause, then go again
      }, seconds * 1000);
    }

    timer = setTimeout(wander, 2500);
    const keepInside = () => {
      const s = size();
      pos.current = {
        x: Math.min(pos.current.x, window.innerWidth - s - 12),
        y: Math.min(pos.current.y, window.innerHeight - tall() - 12),
      };
      setDur(0);
      setXy({ ...pos.current });
    };
    window.addEventListener("resize", keepInside);
    return () => { stopped = true; clearTimeout(timer); window.removeEventListener("resize", keepInside); };
  }, []);

  if (!xy) return null;
  return (
    <div
      className="mascot"
      aria-hidden="true"
      style={{ transform: `translate(${xy.x}px, ${xy.y}px)`, transitionDuration: `${dur}s` }}
    >
      <div className={`mascot-body${moving ? " walk" : ""}`} style={{ ["--face" as string]: facing }}>
        <MonkeyFigure mood={mood} />
      </div>
    </div>
  );
}

// Limbs are drawn twice along the same path: a thick white stroke for the
// outline and a thinner red one on top, so they read as outlined tubes like the logo.
function Limb({ d, cls }: { d: string; cls: string }) {
  return (
    <g className={cls}>
      <path d={d} stroke="#fff" strokeWidth={13} strokeLinecap="round" fill="none" />
      <path d={d} stroke="var(--brand)" strokeWidth={7} strokeLinecap="round" fill="none" />
    </g>
  );
}

function MonkeyFigure({ mood }: { mood: Mood }) {
  return (
    <svg className="mascot-svg" viewBox="0 0 120 176" width="100%" height="100%" aria-hidden="true">
      {/* tail */}
      <g className="m-tail">
        <path d="M40 132 C18 134 8 110 20 100 C30 92 40 104 30 110" stroke="#fff" strokeWidth={11} strokeLinecap="round" fill="none" />
        <path d="M40 132 C18 134 8 110 20 100 C30 92 40 104 30 110" stroke="var(--brand)" strokeWidth={5} strokeLinecap="round" fill="none" />
      </g>
      {/* legs + feet */}
      <g className="m-leg m-leg-l">
        <Limb d="M50 138 L46 160" cls="" />
        <ellipse cx="43" cy="164" rx="10" ry="6" fill="#fff" />
      </g>
      <g className="m-leg m-leg-r">
        <Limb d="M70 138 L74 160" cls="" />
        <ellipse cx="77" cy="164" rx="10" ry="6" fill="#fff" />
      </g>
      {/* back arm */}
      <g className="m-arm m-arm-r">
        <Limb d="M82 104 L92 130" cls="" />
        <circle cx="93" cy="134" r="7" fill="#fff" />
      </g>
      {/* torso */}
      <ellipse cx="60" cy="118" rx="27" ry="29" fill="var(--brand)" stroke="#fff" strokeWidth={5} />
      <ellipse cx="60" cy="124" rx="15" ry="18" fill="#fff" />
      {/* front arm */}
      <g className="m-arm m-arm-l">
        <Limb d="M38 104 L28 130" cls="" />
        <circle cx="27" cy="134" r="7" fill="#fff" />
      </g>
      {/* head: the MADBOY logo, filled */}
      <image className="m-head" href={`/brand/${mood}-full.png`} x="8" y="0" width="104" height="96" />
    </svg>
  );
}
