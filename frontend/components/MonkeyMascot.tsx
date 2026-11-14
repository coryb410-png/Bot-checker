"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

// The MADBOY monkey crew. Purely decorative: the whole layer is click-through
// (pointer-events: none), screen readers skip it, it pauses while the tab is
// hidden, and with "reduce motion" switched on there is just one monkey
// standing still in the corner.
//
//  • Walkers roam freely, each on its own clock: they walk, trot, stop, sit,
//    scratch, look around, wave, stretch and watch the cursor whenever they
//    like. Tap one and it hops (the tap still reaches the page underneath).
//  • Every so often a swinger crosses the screen hand-over-hand along a row of
//    gold chains (blockchain, not vines). That part is a small physics sim:
//    the chains are link-by-link ropes, the monkey is a weight on a pendulum
//    that pumps, lets go, flies and catches the next chain.
//  • While a swinger is about, the band it swings through is off-limits to
//    walkers. That's the only thing the monkeys coordinate on.
//
// One requestAnimationFrame loop drives everything and writes straight to the
// DOM, so React never re-renders per frame.
type Mood = "mad" | "smirk" | "cash" | "shh";
const MOODS: Mood[] = ["smirk", "smirk", "cash", "cash", "mad", "shh"];   // weighted: mostly smirk/cash
const pick = () => MOODS[Math.floor(Math.random() * MOODS.length)];
const small = () => window.innerWidth < 560;
const rand = (a: number, b: number) => a + Math.random() * (b - a);
const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));
const expo = (mean: number) => -Math.log(1 - Math.random()) * mean;        // "whenever it likes"
const f1 = (n: number) => n.toFixed(1);

// Bottom edge (px from the top) of the band reserved for a swinger; 0 = nobody swinging.
const crew = { swingBottom: 0 };

export default function MonkeyMascot() {
  const path = usePathname();
  const admin = !!path?.startsWith("/admin");
  const root = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<"off" | "still" | "full">("off");
  const [count, setCount] = useState(1);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const fit = () => {
      setMode(mq.matches ? "still" : "full");
      setCount(mq.matches ? 1 : window.innerWidth < 560 ? 1 : window.innerWidth < 1100 ? 2 : 3);
    };
    fit();
    window.addEventListener("resize", fit);
    mq.addEventListener?.("change", fit);
    return () => { window.removeEventListener("resize", fit); mq.removeEventListener?.("change", fit); };
  }, []);

  useEffect(() => {
    if (mode === "off" || admin || !root.current) return;
    return startCrew(root.current, mode === "still");
  }, [mode, count, admin]);

  if (mode === "off" || admin) return null;   // no monkeys on the admin panel
  return (
    <div className="mascot-crew" aria-hidden="true" ref={root}>
      {Array.from({ length: count }, (_, i) => (
        <div className="mascot" key={i}>
          <svg className="mascot-svg" viewBox="0 0 120 176" width="100%" height="100%">
            <ellipse data-p="shadow" cx="60" cy="170" rx="30" ry="6" fill="#000" opacity=".3" />
            <g data-p="flip"><Figure mood={i === 1 ? "cash" : "smirk"} /></g>
          </svg>
        </div>
      ))}
      {mode === "full" && (
        <svg className="swing-layer" width="100%" height="100%">
          <g data-p="mirror">
            <g data-p="chains" />
            <g data-p="swinger" display="none"><Figure mood="smirk" /></g>
          </g>
        </svg>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ figure */
// The head is the MADBOY logo art; the body is drawn to match it in a
// 120 × 176 box. Limbs are bendy tubes (white outline, red core) drawn from a
// joint to wherever the hand or foot should be, so any pose is just a list of
// hand/foot positions plus a few angles (see the pose indices below).
function Tube({ p }: { p: string }) {
  return (
    <>
      <path data-p={p + "o"} stroke="#fff" strokeWidth={13} strokeLinecap="round" fill="none" />
      <path data-p={p + "f"} stroke="var(--brand)" strokeWidth={7} strokeLinecap="round" fill="none" />
    </>
  );
}

function Figure({ mood }: { mood: Mood }) {
  return (
    <g>
      <g data-p="back">
        <g data-p="tail">
          <path d="M40 132 C18 134 8 110 20 100 C30 92 40 104 30 110" stroke="#fff" strokeWidth={11} strokeLinecap="round" fill="none" />
          <path d="M40 132 C18 134 8 110 20 100 C30 92 40 104 30 110" stroke="var(--brand)" strokeWidth={5} strokeLinecap="round" fill="none" />
        </g>
      </g>
      <Tube p="ll" /><ellipse data-p="lle" rx="10" ry="6" fill="#fff" />
      <Tube p="lr" /><ellipse data-p="lre" rx="10" ry="6" fill="#fff" />
      <Tube p="ar" />
      <g data-p="torso">
        <ellipse cx="60" cy="118" rx="27" ry="29" fill="var(--brand)" stroke="#fff" strokeWidth={5} />
        <ellipse cx="60" cy="124" rx="15" ry="18" fill="#fff" />
      </g>
      <Tube p="al" />
      <g data-p="head">
        {/* the face is un-mirrored when the monkey turns round, so the $ eyes never read backwards */}
        <g data-p="face">
        <image data-p="img" href={`/brand/${mood}-full.png`} x="8" y="0" width="104" height="96" />
        {/* eyelids: two patches in the art's own pixel space, shown for a blink */}
        <g data-p="lids" display="none">
          <ellipse data-p="lid0" fill="#fff" /><ellipse data-p="lid1" fill="#fff" />
          <path data-p="lash" stroke="var(--brand)" strokeWidth={7} strokeLinecap="round" fill="none" />
        </g>
        </g>
      </g>
      <circle data-p="ale" r="7" fill="#fff" />
      <circle data-p="are" r="7" fill="#fff" />
    </g>
  );
}

// Pose = 15 numbers. Hands are in the torso's frame (they ride its bob and
// lean); feet are in the figure's frame (they stay planted).
const TY = 0, TR = 1, HR = 2, HX = 3, HY = 4, TAIL = 5, LHX = 6, LHY = 7, RHX = 8, RHY = 9,
      LFX = 10, LFY = 11, RFX = 12, RFY = 13, SQ = 14;
const REST = [0, 0, 0, 0, 0, 0, 24, 139, 96, 139, 43, 164, 77, 164, 1];
const ARM = 48, LEG = 27;

// Where the eyes are in each head image (image pixels): [height, x1, x2, y, rx, ry, tilt°]
const EYES: Record<Mood, number[]> = {
  smirk: [230, 90, 155, 140, 27, 15, 24], mad: [234, 90, 157, 141, 27, 15, 24],
  shh: [242, 73, 137, 138, 22, 14, 24], cash: [230, 87, 153, 145, 23, 30, 0],
};

interface Rig {
  el: Record<string, SVGElement>;
  mood: Mood;
}

function rigOf(rootEl: Element): Rig {
  const el: Record<string, SVGElement> = {};
  rootEl.querySelectorAll<SVGElement>("[data-p]").forEach((n) => { el[n.dataset.p as string] = n; });
  const rig: Rig = { el, mood: "smirk" };
  setMood(rig, ((el.img.getAttribute("href") || "").match(/(\w+)-full/)?.[1] as Mood) || "smirk");
  return rig;
}

function setMood(rig: Rig, mood: Mood) {
  rig.mood = mood;
  rig.el.img.setAttribute("href", `/brand/${mood}-full.png`);
  const [h, x1, x2, y, rx, ry, tilt] = EYES[mood];
  const s = Math.min(104 / 256, 96 / h);   // how the browser fits the image into its 104 × 96 box
  rig.el.lids.setAttribute("transform", `translate(${f1(8 + (104 - 256 * s) / 2)} ${f1((96 - h * s) / 2)}) scale(${s.toFixed(3)})`);
  [x1, x2].forEach((x, i) => {
    const lid = rig.el["lid" + i];
    lid.setAttribute("cx", String(x)); lid.setAttribute("cy", String(y));
    lid.setAttribute("rx", String(rx)); lid.setAttribute("ry", String(ry));
    lid.setAttribute("transform", `rotate(${i ? -tilt : tilt} ${x} ${y})`);
  });
  const t = Math.tan((tilt * Math.PI) / 180) * 16, w = mood === "cash" ? 15 : 17;
  rig.el.lash.setAttribute("d", `M${x1 - w} ${y - t + 3}L${x1 + w} ${y + t + 3}M${x2 - w} ${y + t + 3}L${x2 + w} ${y - t + 3}`);
}

// Draw one bendy limb from a joint to a target. `side` (-1 left, +1 right) says
// which way the elbow or knee bows. Returns where the hand or foot ended up.
function limb(rig: Rig, p: string, rx: number, ry: number, ex: number, ey: number, len: number, side: number, stretch = false) {
  let dx = ex - rx, dy = ey - ry, d = Math.hypot(dx, dy) || 0.001;
  if (d > len && !stretch) { ex = rx + (dx / d) * len; ey = ry + (dy / d) * len; dx = ex - rx; dy = ey - ry; d = len; }
  const bow = d < len ? Math.sqrt(len * len - d * d) * 0.6 : 0;
  let nx = -dy / d, ny = dx / d;
  if (nx * side + ny * 0.3 < 0) { nx = -nx; ny = -ny; }
  const s = `M${f1(rx)} ${f1(ry)}Q${f1((rx + ex) / 2 + nx * bow)} ${f1((ry + ey) / 2 + ny * bow)} ${f1(ex)} ${f1(ey)}`;
  rig.el[p + "o"].setAttribute("d", s);
  rig.el[p + "f"].setAttribute("d", s);
  const e = rig.el[p + "e"];
  e.setAttribute("cx", f1(ex)); e.setAttribute("cy", f1(ey));
}

function applyPose(rig: Rig, P: number[], grip = 0 /* -1 left hand, +1 right hand holds a chain */) {
  const a = (P[TR] * Math.PI) / 180, c = Math.cos(a), s = Math.sin(a), ty = P[TY];
  const tx = (x: number, y: number) => 60 + (x - 60) * c - (y - 140) * s;
  const tY = (x: number, y: number) => 140 + (x - 60) * s + (y - 140) * c + ty;
  const torso = `translate(0 ${f1(ty)}) rotate(${f1(P[TR])} 60 140)`;
  rig.el.torso.setAttribute("transform", torso);
  rig.el.back.setAttribute("transform", torso);
  rig.el.tail.setAttribute("transform", `rotate(${f1(P[TAIL])} 40 132)`);
  rig.el.head.setAttribute("transform", `${torso} translate(${f1(P[HX])} ${f1(P[HY])}) rotate(${f1(P[HR])} 60 88)`);
  limb(rig, "al", tx(38, 104), tY(38, 104), tx(P[LHX], P[LHY]), tY(P[LHX], P[LHY]), ARM, -1, grip < 0);
  limb(rig, "ar", tx(82, 104), tY(82, 104), tx(P[RHX], P[RHY]), tY(P[RHX], P[RHY]), ARM, 1, grip > 0);
  limb(rig, "ll", tx(50, 138), tY(50, 138), P[LFX] + 3, P[LFY] - 4, LEG, -1);
  limb(rig, "lr", tx(70, 138), tY(70, 138), P[RFX] - 3, P[RFY] - 4, LEG, 1);
  // feet sit just outside the ankle
  rig.el.lle.setAttribute("cx", f1(+rig.el.lle.getAttribute("cx")! - 3)); rig.el.lle.setAttribute("cy", f1(+rig.el.lle.getAttribute("cy")! + 4));
  rig.el.lre.setAttribute("cx", f1(+rig.el.lre.getAttribute("cx")! + 3)); rig.el.lre.setAttribute("cy", f1(+rig.el.lre.getAttribute("cy")! + 4));
}

// Ease a pose toward a target, the same amount per second at any frame rate
function ease(P: number[], to: number[], dt: number, rate: number) {
  const k = 1 - Math.exp(-dt * rate);
  for (let i = 0; i < P.length; i++) P[i] += (to[i] - P[i]) * k;
}

/* -------------------------------------------------------------------- crew */
type Idle = "stand" | "sit" | "scratch" | "look" | "wave" | "stretch" | "watch";

interface Walker {
  box: HTMLElement; rig: Rig; w: number; h: number;
  x: number; y: number; tx: number; ty: number;      // where it is, where it's headed (px)
  walking: boolean; top: number; v: number;          // top speed for this trip, current speed
  idle: Idle; it: number; idur: number;              // what it's doing while stopped, for how long
  face: number; faceS: number; phase: number;        // facing target and eased value, stride phase
  z: number; vz: number;                             // hop height (figure units)
  blink: number; lid: number; age: number;
  P: number[];
}

const G = 1150;          // gravity for the swing, px/s²
const STEP = 1 / 120;    // physics step
const LINK = 13;         // chain link spacing, px
const AY = -6;           // where the chains hang from (just above the top edge)

interface Chain { ax: number; x: number[]; y: number[]; px: number[]; py: number[]; links: SVGElement[]; tip: SVGElement; }
interface Swing {
  stage: "clear" | "drop" | "swing" | "retract"; t: number;
  w: number; dir: 1 | -1; s: number; arm: number; n: number; L: number; ay: number;
  chains: Chain[];
  m: { x: number; y: number; px: number; py: number };   // the monkey's shoulders
  on: number; j: number; next: number;                   // chain it holds (-1 = in the air), which link, chain it's after
  hand: number; held: number; phi: number; phiV: number; flip: number; flipRate: number;
  lagX: number; lagY: number; P: number[];
}

function startCrew(root: HTMLElement, still: boolean) {
  const boxes = Array.from(root.querySelectorAll<HTMLElement>(".mascot"));
  const mouse = { x: -9999, y: -9999, at: -99 };
  let clock = 0;

  /* ---- walkers ---- */
  const walkers: Walker[] = boxes.map((box, i) => {
    const w = box.offsetWidth, h = box.offsetHeight;
    const x = i === 0 ? window.innerWidth - w - 16 : i === 1 ? 16 : rand(0.3, 0.6) * window.innerWidth;
    const y = window.innerHeight - h - 12;
    return { box, rig: rigOf(box), w, h, x, y, tx: x, ty: y, walking: false, top: 100, v: 0,
             idle: "stand", it: 0, idur: rand(0.4, 3.5), face: i === 0 ? -1 : 1, faceS: i === 0 ? -1 : 1, phase: 0,
             z: 0, vz: 0, blink: rand(1, 4), lid: 0, age: rand(0, 9), P: REST.slice() };
  });

  const bounds = (k: Walker) => {
    const pad = 12, maxY = Math.max(pad, window.innerHeight - k.h - pad);
    return { pad, maxX: Math.max(pad, window.innerWidth - k.w - pad), maxY,
             minY: crew.swingBottom ? Math.min(maxY, crew.swingBottom) : pad };
  };

  // Set off somewhere: anywhere on screen, or just a short shuffle nearby, at its own pace
  function setOff(k: Walker) {
    const b = bounds(k), near = Math.random() < 0.35;
    k.tx = clamp(near ? k.x + rand(-160, 160) : rand(b.pad, b.maxX), b.pad, b.maxX);
    k.ty = clamp(near ? k.y + rand(-120, 120) : rand(b.minY, b.maxY), b.minY, b.maxY);
    k.top = rand(60, 180);
    k.walking = true;
  }

  // Stop and do whatever it fancies, for as long as it fancies
  function stop(k: Walker, what?: Idle) {
    const r = Math.random(), cursorNear = clock - mouse.at < 4 && Math.hypot(mouse.x - k.x, mouse.y - k.y) < 420;
    k.idle = what || (cursorNear && r < 0.3 ? "watch" : r < 0.4 ? "stand" : r < 0.55 ? "look" : r < 0.68 ? "sit"
                      : r < 0.8 ? "scratch" : r < 0.9 ? "wave" : "stretch");
    k.idur = k.idle === "stand" ? expo(2.6) + 0.3 : k.idle === "sit" ? rand(3, 8) : k.idle === "stretch" ? 2.2
           : k.idle === "watch" ? rand(2, 4.5) : rand(1.3, 2.8);
    k.it = 0; k.walking = false;
    if (!what && Math.random() < 0.4) setMood(k.rig, pick());
  }

  function hop(k: Walker) {
    if (k.z > 0) return;
    k.vz = 330; k.z = 0.01; k.P[SQ] = 1.12;
    if (!k.walking) stop(k, "wave");
  }

  function idlePose(k: Walker, T: number[]) {
    const t = k.it;
    switch (k.idle) {
      case "sit":
        T[TY] = 15 + Math.sin(k.age * 2) * 0.6; T[LFX] = 25; T[RFX] = 95; T[LFY] = T[RFY] = 167;
        T[LHX] = 30; T[RHX] = 90; T[LHY] = T[RHY] = 139; T[HR] = Math.sin(t * 0.9) * 4; T[TAIL] = Math.sin(t * 1.5) * 18;
        break;
      case "look": {
        const a = Math.sin(t * 1.9);
        T[HR] = a * 9; T[HX] = a * 4; T[TR] = a * 2;
        break;
      }
      case "scratch":
        T[RHX] = 99 + Math.sin(t * 24) * 3; T[RHY] = 66 + Math.cos(t * 24) * 3; T[HR] = 7; T[TR] = 2; T[HY] = 1;
        break;
      case "wave":
        T[LHX] = 8 + Math.sin(t * 12) * 8; T[LHY] = 70 - Math.abs(Math.cos(t * 12)) * 3; T[HR] = -5;
        T[TY] = -Math.abs(Math.sin(t * 6)) * 2;
        break;
      case "stretch": {
        const u = Math.sin(Math.PI * clamp(t / k.idur, 0, 1)) ** 0.6;
        T[LHX] = 24 - 16 * u; T[RHX] = 96 + 16 * u; T[LHY] = T[RHY] = 139 - 79 * u;
        T[TY] = -4 * u; T[HR] = -7 * u; T[HY] = -2 * u; T[TAIL] = 14 * u;
        break;
      }
      case "watch":   // turn to the cursor; wave if it comes close
        if (clock - mouse.at < 5) {
          k.face = mouse.x < k.x + k.w / 2 ? -1 : 1;
          if (Math.hypot(mouse.x - k.x - k.w / 2, mouse.y - k.y - k.h / 2) < 150) {
            T[LHX] = 8 + Math.sin(t * 12) * 8; T[LHY] = 70;
          }
        }
        break;
    }
  }

  function stepWalker(k: Walker, dt: number) {
    k.age += dt;
    const b = bounds(k), band = crew.swingBottom;

    // A swinger is coming (or here): get out of its band, at a run, and stay out
    if (band && (k.y < b.minY - 0.5 || k.ty < b.minY)) {
      if (k.ty < b.minY || !k.walking) { k.tx = clamp(k.x + rand(-50, 50), b.pad, b.maxX); k.ty = rand(b.minY, Math.max(b.minY, Math.min(b.maxY, b.minY + 160))); }
      if (k.y < b.minY - 0.5) k.top = 330;
      k.walking = true;
    }

    if (k.walking) {
      const dx = k.tx - k.x, dy = k.ty - k.y, d = Math.hypot(dx, dy);
      const want = Math.min(k.top, Math.sqrt(2 * 420 * d) + 8);            // ease in to a stop
      k.v += clamp(want - k.v, -900 * dt, 520 * dt);
      const go = Math.min(d, k.v * dt);
      if (d > 0.01) { k.x += (dx / d) * go; k.y += (dy / d) * go; }
      if (Math.abs(dx) > 6) k.face = dx < 0 ? -1 : 1;
      k.phase += (go / ((20 + k.v * 0.13) * (k.w / 72))) * Math.PI;         // stride grows with speed
      if (d < 1.5) { k.v = 0; stop(k); }
    } else {
      k.it += dt;
      if (k.it >= k.idur) { if (Math.random() < 0.68) setOff(k); else stop(k); }
    }

    // hop
    if (k.z > 0) {
      k.z += k.vz * dt; k.vz -= 1150 * dt;
      if (k.z <= 0) { k.z = 0; k.vz = 0; k.P[SQ] = 0.84; }
    }

    // pose
    const T = REST.slice(), br = Math.sin(k.age * 2.6);
    T[TY] = br * 0.8; T[HY] = Math.sin(k.age * 2.6 - 0.6) * 0.9; T[TAIL] = Math.sin(k.age * 2.1) * 10;
    if (k.walking) {
      const run = clamp(k.v / 180, 0.45, 1.7), s = Math.sin(k.phase), c = Math.cos(k.phase);
      T[LFX] = 43 + 8 * run * c; T[LFY] = 164 - 10 * run * Math.max(0, s);
      T[RFX] = 77 - 8 * run * c; T[RFY] = 164 - 10 * run * Math.max(0, -s);
      T[TY] = -1 - Math.abs(c) * 3.2 * run; T[TR] = 3 + 4 * run + s * 2.5;
      T[LHX] = 25 - 10 * run * c; T[LHY] = 137 - 5 * run * Math.abs(c);
      T[RHX] = 95 + 10 * run * c; T[RHY] = 137 - 5 * run * Math.abs(c);
      T[HR] = -s * 3.5; T[HY] = Math.abs(s) * 1.5; T[TAIL] = -6 + s * 14;
    } else {
      idlePose(k, T);
      // glance toward the cursor when it's moving about nearby
      const mx = mouse.x - (k.x + k.w / 2), my = mouse.y - (k.y + k.h * 0.3);
      if (clock - mouse.at < 2.5 && Math.hypot(mx, my) < 380 && k.idle !== "stretch") {
        T[HR] += clamp(mx / 22, -10, 10) * (k.faceS < 0 ? -1 : 1); T[HY] += clamp(my / 60, -2, 3);
      }
    }
    if (k.z > 0) {   // in the air: arms up, knees tucked
      T[LHX] = 12; T[RHX] = 108; T[LHY] = T[RHY] = 82; T[LFY] = T[RFY] = 156; T[LFX] = 40; T[RFX] = 80;
    }
    ease(k.P, T, dt, k.walking ? 22 : 12);
    k.faceS += (k.face - k.faceS) * (1 - Math.exp(-dt * 16));

    // blink
    k.blink -= dt;
    if (k.blink <= 0) { k.lid = 0.13; k.blink = Math.random() < 0.2 ? 0.3 : rand(1.6, 5.5); }
    if (k.lid > 0) { k.lid -= dt; k.rig.el.lids.setAttribute("display", k.lid > 0 ? "inline" : "none"); }

    drawWalker(k);
  }

  function drawWalker(k: Walker) {
    const sq = k.P[SQ], f = Math.abs(k.faceS) < 0.08 ? 0.08 * (k.faceS < 0 ? -1 : 1) : k.faceS;
    applyPose(k.rig, k.P);
    k.rig.el.face.setAttribute("transform", f < 0 ? "translate(120 0) scale(-1 1)" : "");
    k.rig.el.flip.setAttribute("transform", `translate(60 ${f1(170 - k.z)}) scale(${(f / Math.sqrt(sq)).toFixed(3)} ${sq.toFixed(3)}) translate(-60 -170)`);
    const lift = clamp(k.z / 60, 0, 1);
    k.rig.el.shadow.setAttribute("rx", f1(30 - 12 * lift + (k.idle === "sit" && !k.walking ? 6 : 0)));
    k.rig.el.shadow.setAttribute("opacity", (0.3 - 0.16 * lift).toFixed(2));
    k.box.style.transform = `translate3d(${f1(k.x)}px, ${f1(k.y)}px, 0)`;
  }

  const fitWalkers = () => {
    for (const k of walkers) {
      k.w = k.box.offsetWidth; k.h = k.box.offsetHeight;
      const b = bounds(k);
      k.x = clamp(k.x, b.pad, b.maxX); k.y = clamp(k.y, b.pad, b.maxY);
      k.tx = clamp(k.tx, b.pad, b.maxX); k.ty = clamp(k.ty, b.pad, b.maxY);
      drawWalker(k);
    }
  };

  walkers.forEach(drawWalker);
  if (still) {   // reduced motion: one monkey, standing still, nothing ticking
    window.addEventListener("resize", fitWalkers);
    return () => window.removeEventListener("resize", fitWalkers);
  }

  /* ---- swinger ---- */
  const layer = root.querySelector<SVGElement>(".swing-layer")!;
  const mirror = layer.querySelector<SVGElement>('[data-p="mirror"]')!;
  const chainsG = layer.querySelector<SVGElement>('[data-p="chains"]')!;
  const swingerG = layer.querySelector<SVGElement>('[data-p="swinger"]')!;
  const sRig = rigOf(swingerG);
  let sw: Swing | null = null;
  let wait = 6;        // seconds until the next crossing
  let acc = 0;

  function endSwing() {
    sw = null; crew.swingBottom = 0; wait = rand(9, 18);
    chainsG.textContent = ""; swingerG.setAttribute("display", "none");
  }

  // Reserve the band and ask the walkers to clear it; the chains only drop once they have.
  function planSwing() {
    const w = window.innerWidth, h = window.innerHeight, s = small() ? 0.46 : 0.62, arm = 112 * s;
    const n = Math.round(clamp(Math.min(h * 0.24, w * 0.32), 96, 200) / LINK), L = n * LINK;
    const bottom = AY + L + arm + 76 * s + 26;                    // chain + arm + dangling legs + a margin
    const tallest = Math.max(...walkers.map((k) => k.h));
    if (h - bottom < tallest + 36) { wait = rand(9, 18); return; }  // screen too short to share: skip
    const gap = (L + arm) * 1.06;                                  // a full swing plus a short flight apart
    const chains: Chain[] = [];
    for (let i = 0; i === 0 || chains[i - 1].ax < w + 40; i++) {   // first and last hang off-screen
      const ax = -50 + i * gap, sway = rand(-1.5, 1.5);
      const c: Chain = { ax, x: [], y: [], px: [], py: [], links: [], tip: null as unknown as SVGElement };
      for (let k = 0; k <= n; k++) { c.x.push(ax); c.y.push(-L - 40 + k * LINK); c.px.push(ax - sway * (k / n)); c.py.push(c.y[k]); }
      chains.push(c);
    }
    crew.swingBottom = bottom;
    sw = { stage: "clear", t: 0, w, dir: Math.random() < 0.5 ? 1 : -1, s, arm, n, L, ay: -L - 40, chains,
           m: { x: 0, y: 0, px: 0, py: 0 }, on: 0, j: n, next: 1, hand: 1, held: 0, phi: 0, phiV: 0, flip: 0, flipRate: 0,
           lagX: 0, lagY: 0, P: REST.slice() };
  }

  const NS = "http://www.w3.org/2000/svg";
  function make(tag: string, attrs: Record<string, string | number>) {
    const e = document.createElementNS(NS, tag);
    for (const a in attrs) e.setAttribute(a, String(attrs[a]));
    chainsG.appendChild(e);
    return e as SVGElement;
  }

  // Gold chain: links alternate face-on (ring) and edge-on (bar), like a real chain.
  function buildChains(S: Swing) {
    mirror.setAttribute("transform", S.dir === 1 ? "" : `translate(${S.w} 0) scale(-1 1)`);
    for (const c of S.chains) {
      for (let k = 0; k < S.n; k++) {
        c.links.push(k % 2 === 0
          ? make("ellipse", { rx: 4.6, ry: 7.6, fill: "none", stroke: "var(--gold)", "stroke-width": 2.6 })
          : make("rect", { x: -1.6, y: -LINK / 2 - 1.5, width: 3.2, height: LINK + 3, rx: 1.6, fill: "#c79a22" }));
      }
      c.tip = make("circle", { r: 3.4, fill: "var(--gold)" });
    }
    setMood(sRig, pick());
    sRig.el.face.setAttribute("transform", S.dir < 0 ? "translate(120 0) scale(-1 1)" : "");
  }

  function physics(S: Swing) {
    const h = STEP, m = S.m, flying = S.on < 0, swinging = S.stage === "swing";

    // free motion
    for (const c of S.chains) {
      for (let k = 1; k <= S.n; k++) {
        const vx = (c.x[k] - c.px[k]) * 0.994, vy = (c.y[k] - c.py[k]) * 0.994;
        c.px[k] = c.x[k]; c.py[k] = c.y[k];
        c.x[k] += vx; c.y[k] += vy + G * h * h;
      }
    }
    if (swinging) {
      let vx = m.x - m.px, vy = m.y - m.py, axl = 0, ayl = G;
      if (!flying) {   // pump like a kid on a swing: push along the arc while going forward
        const c = S.chains[S.on], R = S.j * LINK + S.arm, dx = m.x - c.ax, dy = m.y - S.ay, r = Math.hypot(dx, dy) || 1;
        const e = (vx * vx + vy * vy) / (2 * h * h) + G * (R - dy);
        S.held += h;
        if (vx / h > -30 && e < G * R * (0.52 + 0.2 * Math.min(1, S.held / 5))) { axl += (dy / r) * 340; ayl += (-dx / r) * 340; }
      }
      m.px = m.x; m.py = m.y;
      m.x += vx + axl * h * h; m.y += vy + ayl * h * h;
    }

    // keep everything linked up
    for (let it = 0; it < 10; it++) {
      for (let ci = 0; ci < S.chains.length; ci++) {
        const c = S.chains[ci], held = swinging && ci === S.on;
        c.x[0] = c.ax; c.y[0] = S.ay;
        for (let k = 1; k <= S.n; k++) {
          const dx = c.x[k] - c.x[k - 1], dy = c.y[k] - c.y[k - 1], d = Math.hypot(dx, dy) || 0.001, q = (d - LINK) / d;
          if (k === 1) { c.x[k] -= dx * q; c.y[k] -= dy * q; }
          else { c.x[k] -= dx * q * 0.5; c.y[k] -= dy * q * 0.5; c.x[k - 1] += dx * q * 0.5; c.y[k - 1] += dy * q * 0.5; }
        }
        if (held) {
          // the arm: the monkey is heavy, so the chain comes to it far more than it goes to the chain
          const j = S.j;
          let dx = m.x - c.x[j], dy = m.y - c.y[j], d = Math.hypot(dx, dy) || 0.001;
          if (d > S.arm) { const q = (d - S.arm) / d; c.x[j] += dx * q * 0.97; c.y[j] += dy * q * 0.97; m.x -= dx * q * 0.03; m.y -= dy * q * 0.03; }
          // and the chain can't stretch: the held link and the monkey stay within reach of the anchor
          dx = c.x[j] - c.ax; dy = c.y[j] - S.ay; d = Math.hypot(dx, dy);
          if (d > j * LINK) { c.x[j] = c.ax + (dx / d) * j * LINK; c.y[j] = S.ay + (dy / d) * j * LINK; }
          const R = j * LINK + S.arm;
          dx = m.x - c.ax; dy = m.y - S.ay; d = Math.hypot(dx, dy);
          if (d > R) { m.x = c.ax + (dx / d) * R; m.y = S.ay + (dy / d) * R; }
        }
      }
    }
    if (!swinging) return;

    const vx = (m.x - m.px) / h, vy = (m.y - m.py) / h, reach = S.arm * 0.95, jMin = Math.ceil(S.n * 0.45);
    if (!flying) {
      const c = S.chains[S.on], R = S.j * LINK + S.arm;
      if (S.on === S.chains.length - 1) return;                       // last chain: just ride it off the screen
      // let go when the flight from here would carry it to a good grab on the next chain
      const nx = S.chains[S.on + 1].ax, t = (nx - reach * 0.75 - m.x) / Math.max(1, vx);
      const gy = m.y + vy * t + 0.5 * G * t * t - reach * 0.55 - S.ay;  // where the hand would meet the chain
      const lo = 0.64 - 0.2 * Math.min(1, S.held / 5);                  // the longer it's been stuck here, the less fussy
      if (vx > 80 && vy < 0 && m.x - c.ax > R * 0.3 && t > 0.12 && t < 0.7 && gy > S.L * lo && gy < S.L * 0.95) {
        S.next = S.on + 1; S.on = -1; S.hand = -S.hand;
        if (t > 0.3 && Math.random() < 0.3) { S.flip = 2 * Math.PI; S.flipRate = (2 * Math.PI) / (t * 0.85); }  // now and then, a flip
      }
    } else {
      const c = S.chains[S.next];
      let best = -1, bd = reach;
      for (let j = S.n; j >= jMin; j--) { const d = Math.hypot(c.x[j] - m.x, c.y[j] - m.y); if (d < bd) { bd = d; best = j; } }
      if (best < 0 && (m.x > c.ax + 4 || m.y > S.ay + S.L + S.arm * 0.7)) {   // nearly missed: lunge for the closest link
        bd = 1e9;
        for (let j = S.n; j >= jMin; j--) { const d = Math.hypot(c.x[j] - m.x, c.y[j] - m.y); if (d < bd) { bd = d; best = j; } }
      }
      if (best >= 0) { S.on = S.next; S.j = best; S.held = 0; }
    }
  }

  function stepSwing(S: Swing, dt: number) {
    S.t += dt;
    if (S.stage === "clear") {
      // wait for every walker to be out of the band (they run); give up politely if one can't
      const clear = walkers.every((k) => k.y >= bounds(k).minY - 0.5);
      if (clear) { S.stage = "drop"; S.t = 0; buildChains(S); }
      else if (S.t > 4) { endSwing(); }
      return;
    }
    if (S.stage === "drop") {
      const u = clamp(S.t / 0.6, 0, 1);
      S.ay = -S.L - 40 + (AY + S.L + 40) * (1 - (1 - u) ** 3);
      if (S.t > 1.0) {
        // the swinger arrives on the first chain, which hangs off-screen, already swung back
        S.stage = "swing"; S.t = 0;
        const c = S.chains[0], a = (-54 * Math.PI) / 180;
        for (let k = 0; k <= S.n; k++) { c.x[k] = c.px[k] = c.ax + Math.sin(a) * k * LINK; c.y[k] = c.py[k] = S.ay + Math.cos(a) * k * LINK; }
        const R = S.n * LINK + S.arm;
        S.m.x = S.m.px = c.ax + Math.sin(a) * R; S.m.y = S.m.py = S.ay + Math.cos(a) * R;
        S.phi = -a; S.phiV = 0;
        swingerG.setAttribute("display", "inline");
      }
    } else if (S.stage === "retract") {
      const u = clamp(S.t / 0.7, 0, 1);
      S.ay = AY - (S.L + 50) * u * u;
      if (u >= 1) { endSwing(); return; }
    } else if (S.t > 45 || S.m.x > S.w + 80) {   // across (or something went very wrong): pull the chains up
      S.stage = "retract"; S.t = 0; swingerG.setAttribute("display", "none");
    }

    acc = Math.min(acc + dt, 0.1);
    while (acc >= STEP) { physics(S); acc -= STEP; }
    drawSwing(S, dt);
  }

  function drawSwing(S: Swing, dt: number) {
    for (const c of S.chains) {
      for (let k = 0; k < S.n; k++) {
        const dx = c.x[k + 1] - c.x[k], dy = c.y[k + 1] - c.y[k];
        c.links[k].setAttribute("transform", `translate(${f1((c.x[k] + c.x[k + 1]) / 2)} ${f1((c.y[k] + c.y[k + 1]) / 2)}) rotate(${f1((Math.atan2(dy, dx) * 180) / Math.PI - 90)})`);
      }
      c.tip.setAttribute("cx", f1(c.x[S.n])); c.tip.setAttribute("cy", f1(c.y[S.n]));
    }
    if (S.stage !== "swing") return;

    const m = S.m, vx = (m.x - m.px) / STEP, vy = (m.y - m.py) / STEP, flying = S.on < 0;
    // Body angle: hang from the gripping hand, with a springy lag so it swings through; lean into a flight
    let want: number;
    if (!flying) { const c = S.chains[S.on]; want = Math.atan2(-(m.x - c.x[S.j]), m.y - c.y[S.j]); }
    else want = 0.3;
    S.phiV += ((want - S.phi) * 130 - S.phiV * 13) * dt;
    S.phi += S.phiV * dt;
    if (S.flip > 0) S.flip = Math.max(0, S.flip - S.flipRate * dt);
    const rot = S.phi + S.flip, cs = Math.cos(rot), sn = Math.sin(rot);
    // world point -> the figure's own coordinates (shoulders sit at 60,100)
    const fx = (x: number, y: number) => 60 + ((x - m.x) * cs + (y - m.y) * sn) / S.s;
    const fy = (x: number, y: number) => 100 + (-(x - m.x) * sn + (y - m.y) * cs) / S.s;

    // legs and tail trail behind the motion
    const lx = clamp((-(vx * cs + vy * sn) / S.s) * 0.028, -17, 17), ly = clamp(((vx * sn - vy * cs) / S.s) * 0.012, -7, 5);
    const k = 1 - Math.exp(-dt * 9);
    S.lagX += (lx - S.lagX) * k; S.lagY += (ly - S.lagY) * k;

    const T = REST.slice(), tuck = flying ? (S.flip > 0 ? 18 : 9) : 0;
    T[LFX] = 42 + S.lagX * 1.15 + (flying ? 4 : 0); T[LFY] = 166 + S.lagY - tuck;
    T[RFX] = 80 + S.lagX * 0.85 - (flying ? 4 : 0); T[RFY] = 164 + S.lagY - tuck * 0.8;
    T[TAIL] = clamp(S.lagX * 2.2, -34, 34); T[HR] = clamp(-S.phiV * 3.5, -12, 12);

    // the holding arm goes straight to its link; the other trails, then reaches for the next chain
    const hold = S.hand, grip = flying ? 0 : hold;
    const nc = S.chains[Math.min(S.chains.length - 1, flying ? S.next : S.on + 1)];
    const reaching = flying || (vx > 60 && m.x > S.chains[Math.max(0, S.on)].ax);
    const rx = fx(nc.x[S.n - 1], nc.y[S.n - 1]), ry = fy(nc.x[S.n - 1], nc.y[S.n - 1]);
    const rd = Math.hypot(rx - 60, ry - 104) || 1, rl = Math.min(rd, 96);
    const free = reaching ? [60 + ((rx - 60) / rd) * rl, 104 + ((ry - 104) / rd) * rl] : null;
    const set = (side: number, pt: number[] | null, restX: number) => {
      const X = side < 0 ? LHX : RHX, Y = X + 1;
      if (pt) { T[X] = pt[0]; T[Y] = pt[1]; } else { T[X] = restX + S.lagX * 0.8; T[Y] = 130 + S.lagY; }
    };
    if (flying) {
      // in the air: the hand that will catch (S.hand) reaches, the one that just let go trails
      set(hold, free, hold < 0 ? 24 : 96); set(-hold, null, hold < 0 ? 96 : 24);
    } else {
      const c = S.chains[S.on];
      set(hold, [fx(c.x[S.j], c.y[S.j]), fy(c.x[S.j], c.y[S.j])], 0);
      set(-hold, free, hold < 0 ? 96 : 24);
    }
    ease(S.P, T, dt, 16);
    if (grip) { const X = grip < 0 ? LHX : RHX; S.P[X] = T[X]; S.P[X + 1] = T[X + 1]; }   // the grip never lags
    applyPose(sRig, S.P, grip);
    swingerG.setAttribute("transform", `translate(${f1(m.x)} ${f1(m.y)}) rotate(${f1((rot * 180) / Math.PI)}) scale(${S.s}) translate(-60 -100)`);
  }

  /* ---- the one loop ---- */
  let raf = 0, last = 0;
  const frame = (now: number) => {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (dt <= 0) return;
    clock += dt;
    if (sw) stepSwing(sw, dt);
    else if ((wait -= dt) <= 0) planSwing();
    for (const k of walkers) stepWalker(k, dt);
  };
  const run = () => { cancelAnimationFrame(raf); if (!document.hidden) { last = performance.now(); raf = requestAnimationFrame(frame); } };
  const onMove = (e: PointerEvent) => { if (e.pointerType !== "touch") { mouse.x = e.clientX; mouse.y = e.clientY; mouse.at = clock; } };
  // A tap on a monkey makes it hop. We only listen; the tap still lands on whatever is underneath.
  const onDown = (e: PointerEvent) => {
    for (const k of walkers) if (e.clientX > k.x && e.clientX < k.x + k.w && e.clientY > k.y && e.clientY < k.y + k.h) hop(k);
  };
  const onResize = () => { if (sw) endSwing(); fitWalkers(); };

  for (const mood of ["mad", "smirk", "cash", "shh"]) new Image().src = `/brand/${mood}-full.png`;   // no flicker on a change of face
  document.addEventListener("visibilitychange", run);
  window.addEventListener("pointermove", onMove, { passive: true });
  window.addEventListener("pointerdown", onDown, { passive: true });
  window.addEventListener("resize", onResize);
  run();
  return () => {
    cancelAnimationFrame(raf);
    endSwing();
    document.removeEventListener("visibilitychange", run);
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerdown", onDown);
    window.removeEventListener("resize", onResize);
  };
}
