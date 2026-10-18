import { useMemo, useState } from "react";
import { Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { MetricPoint } from "@/api/types";
import { metricValue, timeLabel } from "@/lib/format";

export interface ChartSeries { key: string; label: string; points: MetricPoint[]; color?: string }

const PALETTE = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];

function merge(series: ChartSeries[]) {
  const rows = new Map<string, Record<string, number | string>>();
  for (const s of series) {
    for (const p of s.points) {
      const r = rows.get(p.t) ?? { t: p.t };
      r[s.key] = p.v;
      rows.set(p.t, r);
    }
  }
  return [...rows.values()].sort((a, b) => String(a.t).localeCompare(String(b.t)));
}

function ChartTooltip({ active, payload, label, unit, labels }: { active?: boolean; payload?: { dataKey: string; value: number; color: string }[]; label?: string; unit?: string; labels: Record<string, string> }) {
  if (!active || !payload?.length || !label) return null;
  return (
    <div className="rounded-md border border-border bg-surface px-2.5 py-2 text-[12px] shadow-md">
      <div className="mb-1 text-muted">{new Date(label).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}</div>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2">
          <span className="size-2 rounded-full" style={{ background: p.color }} />
          <span className="text-text-2">{labels[p.dataKey] ?? p.dataKey}</span>
          <span className="tabular ml-auto pl-3 font-medium">{metricValue(unit, p.value)}</span>
        </div>
      ))}
    </div>
  );
}

/** Summary of a series for card headers: latest, average and peak (null when empty). */
export function seriesSummary(points: MetricPoint[]): { last: number; avg: number; peak: number } | null {
  const v = points.map((p) => p.v).filter((x) => Number.isFinite(x));
  if (!v.length) return null;
  return { last: v[v.length - 1], avg: v.reduce((a, b) => a + b, 0) / v.length, peak: Math.max(...points.map((p) => (Number.isFinite(p.max) ? p.max : p.v))) };
}

export function MetricChart({ series, unit, height = 200, area = true, yDomain, syncId, onZoom }: {
  series: ChartSeries[]; unit?: string; height?: number; area?: boolean; yDomain?: [number | "auto", number | "auto"];
  /** charts with the same syncId share the hover cursor */
  syncId?: string;
  /** drag across the chart to select a time window */
  onZoom?: (startIso: string, endIso: string) => void;
}) {
  const data = useMemo(() => merge(series), [series]);
  const [sel, setSel] = useState<{ a?: string; b?: string }>({});
  const labels = Object.fromEntries(series.map((s) => [s.key, s.label]));
  const span = data.length > 1 ? (new Date(String(data[data.length - 1].t)).getTime() - new Date(String(data[0].t)).getTime()) / 1000 : 0;
  if (!data.length) {
    return <div className="flex items-center justify-center text-[12.5px] text-muted" style={{ height }}>No samples in this time range</div>;
  }
  const finishZoom = () => {
    if (onZoom && sel.a && sel.b && sel.a !== sel.b) {
      const [x, y] = [sel.a, sel.b].sort();
      onZoom(x, y);
    }
    setSel({});
  };
  const common = {
    data,
    syncId,
    margin: { top: 6, right: 8, bottom: 0, left: 0 },
    ...(onZoom ? {
      onMouseDown: (e: { activeLabel?: string } | null) => e?.activeLabel && setSel({ a: String(e.activeLabel) }),
      onMouseMove: (e: { activeLabel?: string } | null) => sel.a && e?.activeLabel && setSel((p) => ({ ...p, b: String(e.activeLabel) })),
      onMouseUp: finishZoom,
    } : {}),
  };
  // Recharts only discovers axes among direct children, so these are an array (not a fragment).
  const axes = [
    <CartesianGrid key="grid" stroke="var(--chart-grid)" vertical={false} />,
    <XAxis key="x" dataKey="t" tickFormatter={(t) => timeLabel(String(t), span)} tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={false} minTickGap={48} />,
    <YAxis key="y" width={60} tickFormatter={(v) => metricValue(unit, Number(v))} tick={{ fontSize: 11, fill: "var(--muted)" }} tickLine={false} axisLine={false} domain={yDomain ?? ["auto", "auto"]} />,
    <Tooltip key="tip" content={<ChartTooltip unit={unit} labels={labels} />} cursor={{ stroke: "var(--border-strong)" }} />,
  ];
  if (sel.a && sel.b) axes.push(<ReferenceArea key="sel" x1={sel.a} x2={sel.b} fill="var(--accent)" fillOpacity={0.12} stroke="var(--accent)" strokeOpacity={0.4} />);
  if (series.length > 1) {
    axes.push(<Legend key="legend" verticalAlign="top" align="right" height={22} iconType="circle" iconSize={7}
      formatter={(v: string) => <span style={{ color: "var(--text-2)", fontSize: 11.5 }}>{labels[v] ?? v}</span>} />);
  }
  return (
    <div style={{ height }} role="img" aria-label={`Chart of ${series.map((s) => s.label).join(", ")}`} className={onZoom ? "cursor-crosshair select-none" : undefined}>
      <ResponsiveContainer width="100%" height="100%">
        {area ? (
          <AreaChart {...common}>
            <defs>
              {series.map((s, i) => (
                <linearGradient key={s.key} id={`g-${s.key.replace(/[^a-z0-9]/gi, "")}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={s.color ?? PALETTE[i % PALETTE.length]} stopOpacity={0.18} />
                  <stop offset="100%" stopColor={s.color ?? PALETTE[i % PALETTE.length]} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            {axes}
            {series.map((s, i) => (
              <Area key={s.key} type="monotone" dataKey={s.key} stroke={s.color ?? PALETTE[i % PALETTE.length]} strokeWidth={1.6}
                fill={`url(#g-${s.key.replace(/[^a-z0-9]/gi, "")})`} isAnimationActive={false} connectNulls dot={false} />
            ))}
          </AreaChart>
        ) : (
          <LineChart {...common}>
            {axes}
            {series.map((s, i) => (
              <Line key={s.key} type="monotone" dataKey={s.key} stroke={s.color ?? PALETTE[i % PALETTE.length]} strokeWidth={1.6} dot={false} isAnimationActive={false} connectNulls />
            ))}
          </LineChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}

export function Sparkline({ points, color = "var(--chart-1)", height = 32 }: { points: MetricPoint[]; color?: string; height?: number }) {
  if (!points.length) return <div style={{ height }} />;
  return (
    <div style={{ height }} aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 2, bottom: 2, left: 0, right: 0 }}>
          <YAxis hide domain={["auto", "auto"]} />
          <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.4} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function Meter({ value, max = 100, tone }: { value: number | null | undefined; max?: number; tone?: string }) {
  const p = value === null || value === undefined ? 0 : Math.min(100, (value / max) * 100);
  const color = tone ?? (p >= 95 ? "var(--crit)" : p >= 80 ? "var(--warn)" : "var(--accent)");
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3" role="meter" aria-valuenow={value ?? 0} aria-valuemax={max}>
      <div className="h-full rounded-full" style={{ width: `${p}%`, background: color }} />
    </div>
  );
}
