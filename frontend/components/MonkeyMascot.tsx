"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

// The MADBOY monkey crew. Purely decorative: clicks pass straight through,
// screen readers skip it, it pauses while the tab is hidden, and with "reduce
// motion" switched on there is just one monkey sitting still in the corner.
//
//  • Walkers roam freely, each on its own clock, walking and stopping as they please.
//  • Every so often a swinger crosses the screen hand-over-hand along a row of
//    gold chains hanging from the top (blockchain, not vines). Each chain it
//    lets go of keeps swinging and settles; the row fades once it's gone.
//
// The head is the MADBOY logo art (filled); the body is drawn to match it.
type Mood = "mad" | "smirk" | "cash" | "shh";
const MOODS: Mood[] = ["smirk", "smirk", "cash", "cash", "mad", "shh"];   // weighted: mostly smirk/cash
const pick = () => MOODS[Math.floor(Math.random() * MOODS.length)];
const small = () => window.innerWidth < 560;
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// While a swinger is crossing, the band it swings through (from the top of the
// screen down to the bottom of its feet) is off-limits to walkers. That's the
// only thing the monkeys coordinate on; otherwise each does its own thing.
const crew = { swingBottom: 0 };   // 0 = nobody swinging

export default function MonkeyMascot() {
  const path = usePathname();
  const [mode, setMode] = useState<"off" | "still" | "full">("off");
  const [walkers, setWalkers] = useState(2);
  useEffect(() => {
    setMode(reducedMotion() ? "still" : "full");
    const fit = () => setWalkers(small() ? 1 : 2);
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  if (mode === "off" || path?.startsWith("/admin")) return null;   // no monkeys on the admin panel
  if (mode === "still") return <Walker index={0} still />;
  return (
    <>
      {Array.from({ length: walkers }, (_, i) => <Walker key={i} index={i} />)}
      <Swingers />
    </>
  );
}

/* ------------------------------------------------------------------ walkers */
// Each walker roams freely on its own clock: it sets off whenever it likes, at
// its own pace (an amble or a trot), goes near or far, and stops for as long
// as it likes (mostly short stops, now and then a long one).
const rand = (a: number, b: number) => a + Math.random() * (b - a);

function Walker({ index, still = false }: { index: number; still?: boolean }) {
  const pos = useRef({ x: 0, y: 0 });
  const [mood, setMood] = useState<Mood>(index ? "cash" : "smirk");
  const [facing, setFacing] = useState<1 | -1>(index ? 1 : -1);
  const [moving, setMoving] = useState(false);
  const [dur, setDur] = useState(0);
  const [xy, setXy] = useState<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const w = () => (small() ? 54 : 72), h = () => (small() ? 80 : 106);
    pos.current = index === 0
      ? { x: window.innerWidth - w() - 16, y: window.innerHeight - h() - 12 }   // bottom right
      : { x: 16, y: window.innerHeight - h() - 12 };                           // bottom left
    setXy(pos.current);
    if (still) return;

    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const bounds = () => {
      const pad = 12, maxY = Math.max(pad, window.innerHeight - h() - pad);
      return { pad, maxX: Math.max(pad, window.innerWidth - w() - pad), maxY,
               minY: crew.swingBottom ? Math.min(maxY, crew.swingBottom + 8) : pad };
    };

    // Anywhere on screen, or sometimes just a short shuffle nearby
    function target() {
      const b = bounds();
      const near = Math.random() < 0.35;
      const x = near ? pos.current.x + rand(-160, 160) : rand(b.pad, b.maxX);
      const y = near ? pos.current.y + rand(-120, 120) : rand(b.minY, b.maxY);
      return { x: Math.min(b.maxX, Math.max(b.pad, x)), y: Math.min(b.maxY, Math.max(b.minY, y)) };
    }

    function walkTo(to: { x: number; y: number }, speed: number) {
      clearTimeout(timer);
      const seconds = Math.min(8, Math.max(0.5, Math.hypot(to.x - pos.current.x, to.y - pos.current.y) / speed));
      if (to.x !== pos.current.x) setFacing(to.x < pos.current.x ? -1 : 1);
      setDur(seconds);
      setMoving(true);
      setXy(to);
      pos.current = to;
      timer = setTimeout(() => {   // arrived: stop for as long as it likes, then go again
        setMoving(false);
        if (Math.random() < 0.6) setMood(pick());
        timer = setTimeout(wander, -Math.log(1 - Math.random()) * 2600 + 300);
      }, seconds * 1000);
    }

    function wander() {
      if (stopped) return;
      if (document.hidden) { timer = setTimeout(wander, 1500); return; }
      walkTo(target(), rand(60, 180));   // its own pace each time
    }
    timer = setTimeout(wander, rand(400, 3500));   // each starts in its own time

    // A swinger is coming: hurry straight down out of the band it swings through
    const clearOut = () => {
      if (stopped || !crew.swingBottom || pos.current.y >= crew.swingBottom + 8) return;
      const b = bounds();
      walkTo({ x: pos.current.x, y: rand(b.minY, b.maxY) }, 320);
    };
    window.addEventListener("monkey-swing-start", clearOut);

    const keepInside = () => {
      pos.current = { x: Math.min(pos.current.x, window.innerWidth - w() - 12), y: Math.min(pos.current.y, window.innerHeight - h() - 12) };
      setDur(0);
      setXy({ ...pos.current });
    };
    window.addEventListener("resize", keepInside);
    return () => {
      stopped = true; clearTimeout(timer);
      window.removeEventListener("resize", keepInside);
      window.removeEventListener("monkey-swing-start", clearOut);
    };
  }, [index, still]);

  if (!xy) return null;
  return (
    <div className="mascot" aria-hidden="true"
      style={{ transform: `translate(${xy.x}px, ${xy.y}px)`, transitionDuration: `${dur}s` }}>
      <div className={`mascot-body${moving ? " walk" : ""}`} style={{ ["--face" as string]: facing }}>
        <svg className="mascot-svg" viewBox="0 0 120 176" width="100%" height="100%" aria-hidden="true">
          <MonkeyParts mood={mood} />
        </svg>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- swingers */
const THETA = (40 * Math.PI) / 180;   // how far each swing goes either side of straight down
const SWING_S = 0.95;                 // seconds per swing, chain to chain
const LINK = 13;                      // chain link spacing (px)

interface Run {
  dir: 1 | -1;          // 1 = left to right
  mood: Mood;
  L: number;            // chain length
  anchors: number[];    // x of each chain's top
  start: number;        // ms
  releasedAt: number[]; // ms when each chain was let go (NaN = not yet)
}

function Swingers() {
  const [run, setRun] = useState<Run | null>(null);
  const [now, setNow] = useState(0);
  const [vw, setVw] = useState(0);

  // Launch a crossing every 9–18 s (one at a time)
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const launch = () => {
      if (stopped) return;
      if (document.hidden) { timer = setTimeout(launch, 3000); return; }
      const w = window.innerWidth, h = window.innerHeight;
      const L = Math.max(110, Math.min(220, h * 0.26));
      const step = 2 * L * Math.sin(THETA);       // end of one swing = start of the next
      const first = -70 + L * Math.sin(THETA);    // monkey starts just off the edge
      const anchors: number[] = [];
      for (let x = first; x - L * Math.sin(THETA) < w + 70; x += step) anchors.push(x);
      setVw(w);
      // Reserve the band: chain length + a hanging monkey + room for the swing
      crew.swingBottom = L + 176 * (small() ? 0.42 : 0.56) + 16;
      window.dispatchEvent(new Event("monkey-swing-start"));
      // give the walkers a moment to get clear before the swinger appears
      timer = setTimeout(() => {
        if (stopped) return;
        setRun({ dir: Math.random() < 0.5 ? 1 : -1, mood: pick(), L, anchors, start: performance.now(),
                 releasedAt: anchors.map(() => NaN) });
      }, 1200);
    };
    timer = setTimeout(launch, 6000);
    const again = () => { timer = setTimeout(launch, 9000 + Math.random() * 9000); };
    window.addEventListener("monkey-run-done", again);
    return () => { stopped = true; clearTimeout(timer); crew.swingBottom = 0; window.removeEventListener("monkey-run-done", again); };
  }, []);

  // Animate the current crossing
  useEffect(() => {
    if (!run) return;
    let raf = 0;
    const tick = (t: number) => {
      setNow(t);
      const total = run.anchors.length * SWING_S * 1000;
      if (t - run.start > total + 1600) {      // finished, and the chains have faded
        setRun(null);
        crew.swingBottom = 0;   // the top of the screen is free again
        window.dispatchEvent(new Event("monkey-run-done"));
        return;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [run]);

  if (!run) return null;
  const elapsed = (now - run.start) / 1000;
  const total = run.anchors.length * SWING_S;
  const on = Math.min(run.anchors.length - 1, Math.floor(elapsed / SWING_S));
  const t = Math.min(1, (elapsed - on * SWING_S) / SWING_S);
  const fade = elapsed > total ? Math.max(0, 1 - (elapsed - total) / 1.4) : Math.min(1, elapsed / 0.4);
  const s = small() ? 0.42 : 0.56;   // monkey scale inside the chain group

  // Angle of each chain (degrees, 0 = straight down, positive = swung forward)
  const angleOf = (i: number) => {
    if (i === on && elapsed < total) return (-THETA * Math.cos(Math.PI * t)) * 180 / Math.PI;      // carrying the monkey
    if (i < on || elapsed >= total) {                                                                // let go: damped swing
      const since = elapsed - (i + 1) * SWING_S;
      return THETA * Math.exp(-since / 1.1) * Math.cos((2 * Math.PI * since) / 1.5) * 180 / Math.PI;
    }
    return 0;                                                                                        // waiting, straight down
  };

  return (
    <svg className="swing-layer" aria-hidden="true" width="100%" height="100%" style={{ opacity: fade }}>
      <g transform={run.dir === 1 ? undefined : `translate(${vw} 0) scale(-1 1)`}>
        {run.anchors.map((ax, i) => (
          <g key={i} transform={`translate(${ax} -6) rotate(${-angleOf(i)})`}>
            <Chain length={run.L} />
            {i === on && elapsed < total && (
              <g transform={`translate(${-90 * s} ${run.L - 2 * s}) scale(${s})`}>
                <MonkeyParts mood={run.mood} pose="hang" />
              </g>
            )}
          </g>
        ))}
      </g>
    </svg>
  );
}

// Gold chain: links alternate face-on (ring) and edge-on (bar), like a real chain.
function Chain({ length }: { length: number }) {
  const n = Math.max(2, Math.floor(length / LINK));
  return (
    <g>
      {Array.from({ length: n }, (_, k) => k % 2 === 0
        ? <ellipse key={k} cx={0} cy={k * LINK + LINK / 2} rx={4.6} ry={7.6} fill="none" stroke="var(--gold)" strokeWidth={2.6} />
        : <rect key={k} x={-1.6} y={k * LINK - 1} width={3.2} height={LINK + 2} rx={1.6} fill="#c79a22" />)}
      <circle cx={0} cy={length} r={3} fill="var(--gold)" />
    </g>
  );
}

/* ------------------------------------------------------------------ figure */
// Limbs are drawn twice along the same path: a thick white stroke for the
// outline and a thinner red one on top, so they read as outlined tubes like the logo.
function Limb({ d }: { d: string }) {
  return (
    <>
      <path d={d} stroke="#fff" strokeWidth={13} strokeLinecap="round" fill="none" />
      <path d={d} stroke="var(--brand)" strokeWidth={7} strokeLinecap="round" fill="none" />
    </>
  );
}

// Figure in a 120 × 176 box. "hang" raises the back arm over the head to grip a
// chain at (90, -4) and lets the legs dangle.
function MonkeyParts({ mood, pose = "stand" }: { mood: Mood; pose?: "stand" | "hang" }) {
  const hang = pose === "hang";
  return (
    <g className={hang ? "m-hang" : undefined}>
      <g className="m-tail">
        <path d="M40 132 C18 134 8 110 20 100 C30 92 40 104 30 110" stroke="#fff" strokeWidth={11} strokeLinecap="round" fill="none" />
        <path d="M40 132 C18 134 8 110 20 100 C30 92 40 104 30 110" stroke="var(--brand)" strokeWidth={5} strokeLinecap="round" fill="none" />
      </g>
      <g className="m-leg m-leg-l">
        <Limb d={hang ? "M50 138 L44 162" : "M50 138 L46 160"} />
        <ellipse cx={hang ? 41 : 43} cy={hang ? 167 : 164} rx="10" ry="6" fill="#fff" />
      </g>
      <g className="m-leg m-leg-r">
        <Limb d={hang ? "M70 138 L78 160" : "M70 138 L74 160"} />
        <ellipse cx={hang ? 81 : 77} cy={hang ? 165 : 164} rx="10" ry="6" fill="#fff" />
      </g>
      {/* back arm: down by the side, or up over the head gripping the chain (drawn behind the head) */}
      <g className="m-arm m-arm-r">
        {hang
          ? <><Limb d="M80 106 C98 84 104 40 90 -2" /><circle cx="90" cy="-4" r="7.5" fill="#fff" /></>
          : <><Limb d="M82 104 L92 130" /><circle cx="93" cy="134" r="7" fill="#fff" /></>}
      </g>
      <ellipse cx="60" cy="118" rx="27" ry="29" fill="var(--brand)" stroke="#fff" strokeWidth={5} />
      <ellipse cx="60" cy="124" rx="15" ry="18" fill="#fff" />
      <g className="m-arm m-arm-l">
        <Limb d={hang ? "M38 104 L22 124" : "M38 104 L28 130"} />
        <circle cx={hang ? 20 : 27} cy={hang ? 128 : 134} r="7" fill="#fff" />
      </g>
      <image className="m-head" href={`/brand/${mood}-full.png`} x="8" y="0" width="104" height="96" />
    </g>
  );
}
