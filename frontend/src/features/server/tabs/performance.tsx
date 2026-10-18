import { useState } from "react";
import type { ServerDetail } from "@/api/types";
import { RotateCcw } from "lucide-react";
import { MetricChart, seriesSummary } from "@/components/charts";
import { Button } from "@/components/ui/button";
import { AggregationPicker, AutoRefreshPicker } from "@/components/auto-refresh";
import { TimeRangePicker, type RangeValue } from "@/components/time-range";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { ErrorState } from "@/components/ui/states";
import { formatStep, metricValue } from "@/lib/format";
import { pointsFor, useMetrics } from "../hooks";

const GROUPS: { title: string; unit: string; metrics: [string, string][] }[] = [
  { title: "Connections", unit: "count", metrics: [["connections.total", "Total"], ["connections.active", "Active"], ["connections.idle", "Idle"]] },
  { title: "Connection utilization", unit: "%", metrics: [["connections.utilization", "Utilization"]] },
  { title: "Throughput", unit: "1/s", metrics: [["transactions.per_sec", "Transactions/s"], ["query.calls_per_sec", "Statements/s"]] },
  { title: "Mean statement latency", unit: "ms", metrics: [["query.mean_latency_ms", "Latency"]] },
  { title: "Buffer cache hit ratio", unit: "%", metrics: [["cache.hit_ratio", "Hit ratio"]] },
  { title: "Physical reads", unit: "1/s", metrics: [["io.blocks_read_per_sec", "Blocks read/s"]] },
  { title: "Locking", unit: "count", metrics: [["sessions.blocked", "Blocked sessions"], ["deadlocks.count", "Deadlocks"]] },
  { title: "Temporary spills", unit: "B/s", metrics: [["temp.bytes_per_sec", "Temp written"]] },
  { title: "Replication lag", unit: "s", metrics: [["replication.lag_seconds", "Lag"]] },
  { title: "Database size", unit: "B", metrics: [["storage.database_bytes", "Size"]] },
  { title: "CPU", unit: "%", metrics: [["cpu.percent", "CPU"]] },
  { title: "Memory used", unit: "B", metrics: [["memory.used_bytes", "Used"]] },
];

export function PerformanceTab({ server }: { server: ServerDetail }) {
  const [range, setRange] = useState<RangeValue>({ range: "6h" });
  const [zoomedFrom, setZoomedFrom] = useState<RangeValue | null>(null);
  const zoom = (start: string, end: string) => {
    if (!zoomedFrom) setZoomedFrom(range);
    setRange({ range: "custom", start, end });
  };
  const [agg, setAgg] = useState("avg");
  const all = GROUPS.flatMap((g) => g.metrics.map((m) => m[0]));
  const q = useMetrics(server.id, all, range, { agg });
  const waits = useMetrics(server.id, ["wait.active_sessions"], range, { labeled: true, agg, enabled: !q.error });
  const toolbar = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <TimeRangePicker value={range} onChange={(v) => { setZoomedFrom(null); setRange(v); }} />
      <div className="flex flex-wrap items-center gap-3">
        <AggregationPicker value={agg} onChange={setAgg} />
        {zoomedFrom && <Button size="sm" variant="ghost" onClick={() => { setRange(zoomedFrom); setZoomedFrom(null); }}><RotateCcw /> Reset zoom</Button>}
        <AutoRefreshPicker />
        {q.data && <span className="text-[12px] text-muted">{formatStep(q.data.step_seconds)} resolution</span>}
      </div>
    </div>
  );
  if (q.error) return <div className="space-y-4">{toolbar}<ErrorState error={q.error} onRetry={() => q.refetch()} /></div>;
  const waitSeries = (waits.data?.series ?? []).map((s) => ({ key: s.label ?? "other", label: s.label ?? "other", points: s.points }));
  const visible = GROUPS.filter((g) => g.metrics.some(([m]) => pointsFor(q.data, m).length) || !q.data);
  const missing = GROUPS.filter((g) => q.data && !g.metrics.some(([m]) => pointsFor(q.data, m).length));
  return (
    <div className="space-y-4">
      {toolbar}
      <p className="text-[12px] text-muted">Drag across any chart to zoom into that window; hovering one chart shows the same moment on all of them.</p>
      <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        {visible.map((g) => {
          const sum = seriesSummary(pointsFor(q.data, g.metrics[0][0]));
          return (
          <Card key={g.title} className="anim-fade">
            <CardHeader title={g.title} description={sum ? <span className="tabular">now {metricValue(g.unit, sum.last)} · avg {metricValue(g.unit, sum.avg)} · peak {metricValue(g.unit, sum.peak)}</span> : undefined} />
            <CardBody className="pt-2">
              <MetricChart height={170} unit={g.unit} area={g.metrics.length === 1} syncId={`perf-${server.id}`} onZoom={zoom}
                series={g.metrics.map(([m, label]) => ({ key: m, label, points: pointsFor(q.data, m) }))} />
            </CardBody>
          </Card>
          );
        })}
        {waitSeries.length > 0 && (
          <Card>
            <CardHeader title="Active sessions by wait type" description="Sampled at each collection" />
            <CardBody className="pt-2"><MetricChart height={170} unit="count" area={false} series={waitSeries} /></CardBody>
          </Card>
        )}
      </div>
      {missing.length > 0 && (
        <p className="text-[12.5px] text-muted">
          Not available for this server: {missing.map((g) => g.title.toLowerCase()).join(", ")}.
          {server.unavailable_metrics["cpu.percent"] ? ` CPU: ${server.unavailable_metrics["cpu.percent"]}.` : ""}
        </p>
      )}
    </div>
  );
}
