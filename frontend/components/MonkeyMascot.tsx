"use client";

import { useEffect, useRef, useState } from "react";

// A MADBOY monkey that roams the screen: it hops to a random spot every few
// seconds, faces the way it's heading, and changes mood when it stops.
// Purely decorative: clicks pass straight through it, screen readers skip it,
// and with "reduce motion" switched on it just sits in the corner.
type Mood = "mad" | "smirk" | "cash" | "shh";
const MOODS: Mood[] = ["smirk", "smirk", "cash", "cash", "mad", "shh"];   // weighted: mostly smirk/cash
const SPEED = 140;   // pixels per second

export default function MonkeyMascot() {
  const pos = useRef({ x: 0, y: 0 });
  const [mood, setMood] = useState<Mood>("smirk");
  const [facing, setFacing] = useState<1 | -1>(-1);
  const [moving, setMoving] = useState(false);
  const [dur, setDur] = useState(0);
  const [xy, setXy] = useState<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const size = () => (window.innerWidth < 560 ? 48 : 64);
    const corner = () => ({ x: window.innerWidth - size() - 16, y: window.innerHeight - size() - 16 });
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
        y: pad + Math.random() * Math.max(0, window.innerHeight - s - pad * 2),
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
        y: Math.min(pos.current.y, window.innerHeight - s - 12),
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
      <div className={`mascot-body${moving ? " hop" : ""}`} style={{ ["--face" as string]: facing }}>
        <span className="mascot-face" style={{ backgroundImage: `url(/brand/${mood}.png)` }} />
      </div>
    </div>
  );
}
