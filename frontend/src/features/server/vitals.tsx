import type { ServerDetail } from "@/api/types";
import { seriesSummary } from "@/components/charts";
import { Sparkline, trendDelta } from "@/components/sparkline";
import { metricValue } from "@/lib/format";
import { cn } from "@/lib/utils";
import { pointsFor, useMetrics } from "./hooks";

const VITALS: { metric: string; label: string; unit: string; warn?: (v: number) => boolean; crit?: (v: number) => boolean }[] = [
  { metric: "connections.total", label: "Connections", unit: "count" },
  { metric: "connections.utilization", label: "Connection use", unit: "%", warn: (v) => v >= 80, crit: (v) => v >= 95 },
  { metric: "transactions.per_sec", label: "Transactions/s", unit: "1/s" },
  { metric: "query.mean_latency_ms", label: "Mean latency", unit: "ms" },
  { metric: "cache.hit_ratio", label: "Cache hit ratio", unit: "%", warn: (v) => v < 95, crit: (v) => v < 85 },
  { metric: "sessions.blocked", label: "Blocked sessions", unit: "count", warn: (v) => v > 0, crit: (v) => v >= 5 },
  { metric: "cpu.percent", label: "CPU", unit: "%", warn: (v) => v >= 80, crit: (v) => v >= 95 },
  { metric: "replication.lag_seconds", label: "Replication lag", unit: "s", warn: (v) => v >= 30, crit: (v) => v >= 300 },
];

/** Live metric cards under the server header: the last hour, current value and trend. Cards for
 *  metrics the engine/server does not provide are left out rather than shown empty. */
export function ServerVitals({ server }: { server: ServerDetail }) {
  const q = useMetrics(server.id, VITALS.map((v) => v.metric), { range: "1h" });
  if (q.error) return null;
  const cards = VITALS.map((v) => ({ ...v, points: pointsFor(q.data, v.metric) })).filter((v) => !q.data || v.points.length > 0).slice(0, 6);
  if (q.data && cards.length === 0) return null;
  return (
    <div className="anim-stagger mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6" aria-label="Key metrics, last hour">
      {cards.map((c) => {
        const sum = seriesSummary(c.points);
        const values = c.points.map((p) => p.v);
        const tone = sum && c.crit?.(sum.last) ? "crit" : sum && c.warn?.(sum.last) ? "warn" : undefined;
        const delta = trendDelta(values);
        return (
          <div key={c.metric} className={cn("rounded-[10px] border bg-surface px-3 py-2.5", tone === "crit" ? "border-crit/40" : tone === "warn" ? "border-warn/40" : "border-border")}>
            <div className="flex items-center justify-between gap-1 text-[12px] text-muted">
              <span className="truncate">{c.label}</span>
              {delta !== null && Math.abs(delta) >= 5 && <span className="tabular text-2xs">{delta > 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(0)}%</span>}
            </div>
            <div className="mt-0.5 flex items-end justify-between gap-2">
              {sum ? (
                <span className="tabular text-[19px] font-semibold leading-tight" style={{ color: tone ? `var(--${tone})` : undefined }}>{metricValue(c.unit, sum.last)}</span>
              ) : <span className="skeleton h-6 w-14" />}
              <Sparkline values={values} width={64} height={22} color={tone ? `var(--${tone})` : "var(--chart-1)"} label={`${c.label}, last hour`} />
            </div>
            {sum && <div className="tabular mt-0.5 truncate text-2xs text-muted">avg {metricValue(c.unit, sum.avg)} · peak {metricValue(c.unit, sum.peak)}</div>}
          </div>
        );
      })}
    </div>
  );
}
