"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Say } from "@/components/Brand";
import { api, money, ValuePoint } from "@/lib/api";

// One series (portfolio value), so no legend: the title names it.
// 2px line + light area from a zero baseline, recessive grid, emphasised end
// point, crosshair + readout on hover/touch. Colors come from theme tokens.
// The range is owned by the page so the highlights can follow it. 0 = all.
export const RANGES: [string, number][] = [["30D", 30], ["90D", 90], ["1Y", 366], ["All", 0]];

export function RangePicker({ range, onRange }: { range: number; onRange: (d: number) => void }) {
  return (
    <div className="seg" role="group" aria-label="Date range">
      {RANGES.map(([label, days]) => (
        <button key={label} type="button" aria-pressed={range === days} onClick={() => onRange(days)}>{label}</button>
      ))}
    </div>
  );
}
const H = 220, PAD = { top: 12, right: 12, bottom: 26, left: 58 };

function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

const short = (n: number) =>
  n >= 1e6 ? `$${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M` : n >= 1e3 ? `$${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}k` : `$${Math.round(n)}`;
const fmtDay = (d: string, withYear = false) =>
  new Date(d + "T12:00:00Z").toLocaleDateString(undefined, { month: "short", day: "numeric", ...(withYear ? { year: "numeric" } : {}) });

export default function ValueChart({ id, wallet, refreshKey, range, onRange }: {
  id: string; wallet?: string | null; refreshKey?: string | null; range: number; onRange: (d: number) => void;
}) {
  const [points, setPoints] = useState<ValuePoint[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const [width, setWidth] = useState(640);
  const box = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let stop = false;
    setPoints(null); setFailed(false);
    api.performance(id, wallet).then((p) => !stop && setPoints(p.value_over_time ?? [])).catch(() => !stop && setFailed(true));
    return () => { stop = true; };
  }, [id, wallet, refreshKey]);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [points]);

  const data = useMemo(() => (range ? (points ?? []).slice(-range) : (points ?? [])).map((p) => ({ date: p.date, v: Number(p.value) })), [points, range]);

  if (failed) return null;
  if (!points) {
    return <section className="panel"><Say mood="shh"><span style={{ color: "var(--muted)" }}>Drawing your value chart…</span></Say></section>;
  }
  if (data.length < 2) {
    return (
      <section className="panel">
        <Say mood="smirk"><span style={{ color: "var(--muted)" }}>Not enough price history for a chart yet. It appears once this portfolio has a few days of priced activity.</span></Say>
      </section>
    );
  }

  const innerW = width - PAD.left - PAD.right, innerH = H - PAD.top - PAD.bottom;
  const max = Math.max(...data.map((d) => d.v));
  const ticks = niceTicks(max);
  const top = ticks[ticks.length - 1] || 1;
  const x = (i: number) => PAD.left + (data.length === 1 ? 0 : (i / (data.length - 1)) * innerW);
  const y = (v: number) => PAD.top + innerH - (v / top) * innerH;
  const line = data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.v).toFixed(1)}`).join("");
  const area = `${line}L${x(data.length - 1).toFixed(1)},${y(0)}L${x(0).toFixed(1)},${y(0)}Z`;

  const shown = hover ?? data.length - 1;
  const first = data[0].v, cur = data[shown].v;
  const change = cur - first, pct = first ? (change / first) * 100 : 0;
  const xLabels = [0, Math.floor((data.length - 1) / 2), data.length - 1];

  function onMove(e: React.PointerEvent<SVGRectElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    const rel = (e.clientX - r.left) / r.width;
    setHover(Math.max(0, Math.min(data.length - 1, Math.round(rel * (data.length - 1)))));
  }

  return (
    <section className="panel" style={{ display: "grid", gap: 10 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ display: "grid", gap: 4, minWidth: 0 }}>
          <span className="eyebrow">Portfolio value · coins and tokens</span>
          <div className="row" style={{ gap: 10, alignItems: "baseline" }}>
            <span className="num" style={{ fontSize: 26, fontWeight: 600 }}>{money(String(cur))}</span>
            <span className={`num ${change >= 0 ? "gain" : "loss"}`} style={{ fontSize: 14 }}>
              {money(String(change), true)} ({pct >= 0 ? "+" : ""}{pct.toFixed(1)}%)
            </span>
          </div>
          <span style={{ color: "var(--muted)", fontSize: 13 }}>
            {hover === null ? `Today · change since ${fmtDay(data[0].date, true)}` : `${fmtDay(data[shown].date, true)} · change since ${fmtDay(data[0].date)}`}
          </span>
        </div>
        <RangePicker range={range} onRange={(d) => { onRange(d); setHover(null); }} />
      </div>

      <div ref={box} style={{ width: "100%", minWidth: 0 }}>
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img"
          aria-label={`Portfolio value from ${fmtDay(data[0].date, true)} (${money(String(first))}) to ${fmtDay(data[data.length - 1].date, true)} (${money(String(data[data.length - 1].v))})`}
          style={{ display: "block", maxWidth: "100%", touchAction: "pan-y" }}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={width - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeWidth={1} />
              <text x={PAD.left - 8} y={y(t)} dy="0.32em" textAnchor="end" fill="var(--muted)" fontSize={11} fontFamily="var(--mono)">{short(t)}</text>
            </g>
          ))}
          {xLabels.map((i, k) => (
            <text key={k} x={x(i)} y={H - 6} textAnchor={k === 0 ? "start" : k === 2 ? "end" : "middle"} fill="var(--muted)" fontSize={11} fontFamily="var(--mono)">
              {fmtDay(data[i].date)}
            </text>
          ))}
          <path d={area} fill="var(--accent)" fillOpacity={0.1} stroke="none" />
          <path d={line} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {hover !== null && (
            <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} stroke="var(--muted)" strokeWidth={1} strokeDasharray="3 3" />
          )}
          <circle cx={x(shown)} cy={y(data[shown].v)} r={5} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
          <rect x={PAD.left} y={PAD.top} width={innerW} height={innerH} fill="transparent"
            onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={() => setHover(null)} />
        </svg>
      </div>

      <details>
        <summary style={{ cursor: "pointer", color: "var(--muted)", fontSize: 13 }}>Show as table</summary>
        <div className="scroll" style={{ maxHeight: 240, marginTop: 8 }}>
          <table>
            <thead><tr><th>Date</th><th className="r">Value</th></tr></thead>
            <tbody>
              {[...data].reverse().map((d) => (
                <tr key={d.date}><td>{fmtDay(d.date, true)}</td><td className="r">{money(String(d.v))}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <span style={{ color: "var(--muted)", fontSize: 12 }}>
        Daily value of your coins and tokens at each day&apos;s price. NFTs are counted at today&apos;s floor in the total above, not in this line.
      </span>
    </section>
  );
}
