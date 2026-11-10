"use client";

import { useState } from "react";

/* Palette: validated reference categorical slots 1 (blue) and 2 (orange); see globals.css --series-*. */

type BarDatum = { label: string; value: number; tooltip?: string };

export function BarChart({ data, height = 140, goal, unit = "", ariaLabel }: { data: BarDatum[]; height?: number; goal?: number; unit?: string; ariaLabel: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(goal ?? 0, ...data.map((d) => d.value), 1);
  const w = 100 / data.length;
  const h = height;
  return (
    <div className="relative">
      <svg viewBox={`0 0 100 ${h}`} preserveAspectRatio="none" className="w-full" style={{ height: h }} role="img" aria-label={ariaLabel}>
        <line x1="0" x2="100" y1={h - 0.5} y2={h - 0.5} className="stroke-border" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {goal !== undefined && (
          <line x1="0" x2="100" y1={h - (goal / max) * (h - 8)} y2={h - (goal / max) * (h - 8)} className="stroke-muted-foreground/50" strokeDasharray="3 3" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        )}
        {data.map((d, i) => {
          const bh = (d.value / max) * (h - 8);
          return (
            <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => setHover(i)}>
              <rect x={i * w} y={0} width={w} height={h} fill="transparent" />
              {d.value > 0 && (
                <rect x={i * w + w * 0.15} y={h - bh} width={w * 0.7} height={bh} rx="0.8" fill="var(--series-1)" opacity={hover === null || hover === i ? 1 : 0.55} />
              )}
            </g>
          );
        })}
      </svg>
      {goal !== undefined && <span className="absolute right-0 text-[10px] text-muted-foreground" style={{ top: h - (goal / max) * (h - 8) - 14 }}>目標 {goal}{unit}</span>}
      {hover !== null && (
        <div
          className="pointer-events-none absolute -top-2 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-lg border bg-popover px-2 py-1 text-xs shadow-sm"
          style={{ left: `${(hover + 0.5) * w}%` }}
        >
          <div className="text-muted-foreground">{data[hover].label}</div>
          <div className="font-medium tabular">{data[hover].tooltip ?? `${data[hover].value}${unit}`}</div>
        </div>
      )}
    </div>
  );
}

type Series = { key: string; label: string; color: string; values: (number | null)[] };

export function LineChart({ labels, series, height = 150, ariaLabel }: { labels: string[]; series: Series[]; height?: number; ariaLabel: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const h = height;
  const n = Math.max(1, labels.length - 1);
  const x = (i: number) => (labels.length === 1 ? 50 : (i / n) * 100);
  const y = (v: number) => h - 6 - v * (h - 16);
  return (
    <div>
      <div className="mb-2 flex gap-4 text-xs text-muted-foreground">
        {series.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
      <div className="relative">
        <svg
          viewBox={`0 0 100 ${h}`}
          preserveAspectRatio="none"
          className="w-full"
          style={{ height: h }}
          role="img"
          aria-label={ariaLabel}
          onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            setHover(Math.round(((e.clientX - r.left) / r.width) * n));
          }}
        >
          {[0, 0.5, 1].map((g) => (
            <line key={g} x1="0" x2="100" y1={y(g)} y2={y(g)} className="stroke-border" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          ))}
          {hover !== null && <line x1={x(hover)} x2={x(hover)} y1="0" y2={h} className="stroke-muted-foreground/40" strokeWidth="1" vectorEffect="non-scaling-stroke" />}
          {series.map((s) => {
            const pts = s.values.map((v, i) => (v == null ? null : `${x(i)},${y(v)}`)).filter(Boolean);
            return <polyline key={s.key} points={pts.join(" ")} fill="none" stroke={s.color} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />;
          })}
        </svg>
        {/* markers drawn in HTML so they stay round under non-uniform scaling */}
        {series.map((s) =>
          s.values.map((v, i) =>
            v == null ? null : (
              <span
                key={`${s.key}-${i}`}
                className="pointer-events-none absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-card"
                style={{ left: `${x(i)}%`, top: y(v), background: s.color }}
              />
            ),
          ),
        )}
        <span className="absolute left-0 top-0 text-[10px] text-muted-foreground">100%</span>
        {hover !== null && labels[hover] && (
          <div className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-lg border bg-popover px-2 py-1 text-xs shadow-sm" style={{ left: `${x(hover)}%` }}>
            <div className="text-muted-foreground">{labels[hover]}</div>
            {series.map((s) => (
              <div key={s.key} className="flex items-center gap-1.5">
                <span className="size-2 rounded-full" style={{ background: s.color }} />
                {s.label} <span className="font-medium tabular">{s.values[hover] == null ? "–" : `${Math.round((s.values[hover] as number) * 100)}%`}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function Meter({ value, className }: { value: number; className?: string }) {
  return (
    <div className={`h-2 overflow-hidden rounded-full bg-muted ${className ?? ""}`}>
      <div className="h-full rounded-full" style={{ width: `${Math.round(value * 100)}%`, background: "var(--series-1)" }} />
    </div>
  );
}
