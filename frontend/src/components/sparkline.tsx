import { useId } from "react";

/** Tiny inline trend (no axes). Values are drawn as-is; an empty series renders nothing. */
export function Sparkline({ values, color = "var(--accent)", height = 28, width = 96, label }: {
  values: number[]; color?: string; height?: number; width?: number; label?: string;
}) {
  const id = useId();
  const v = values.filter((x) => Number.isFinite(x));
  if (v.length < 2) return null;
  const min = Math.min(...v);
  const max = Math.max(...v);
  const span = max - min || 1;
  const step = width / (v.length - 1);
  const pts = v.map((y, i) => [i * step, height - 2 - ((y - min) / span) * (height - 4)] as const);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const area = `${line} L${width},${height} L0,${height} Z`;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label ?? "trend"} className="overflow-visible">
      <defs>
        <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="2" fill={color} />
    </svg>
  );
}

/** Percent change between the first and last quarter of a series (null when not meaningful). */
export function trendDelta(values: number[]): number | null {
  const v = values.filter((x) => Number.isFinite(x));
  if (v.length < 8) return null;
  const q = Math.max(1, Math.floor(v.length / 4));
  const a = v.slice(0, q).reduce((s, x) => s + x, 0) / q;
  const b = v.slice(-q).reduce((s, x) => s + x, 0) / q;
  if (!a) return null;
  return ((b - a) / a) * 100;
}
