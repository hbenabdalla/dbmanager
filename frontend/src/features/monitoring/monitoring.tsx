import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { get } from "@/api/client";
import { useAllServers } from "@/api/hooks";
import type { SeriesResponse, Server } from "@/api/types";
import { Meter, MetricChart } from "@/components/charts";
import { PageHeader } from "@/components/page";
import { EngineMark, HealthBadge, SimulatedBadge } from "@/components/status";
import { AutoRefreshPicker } from "@/components/auto-refresh";
import { TimeRangePicker, type RangeValue } from "@/components/time-range";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { bytes, compactNum, duration, ms, pct } from "@/lib/format";
import { refetchFor, useRefreshMs } from "@/lib/refresh";
import { cn } from "@/lib/utils";
import { rangeQuery } from "../server/hooks";
import { ServerScopedPage } from "../shared/server-scoped";
import { LocksTab, SessionsTab } from "../server/tabs/activity";
import { ReplicationTab } from "../server/tabs/infra";

const k = (s: Server, m: string) => s.key_metrics[m];

export function MonitoringOverviewPage() {
  const servers = useAllServers();
  const navigate = useNavigate();
  const rows = (servers.data?.items ?? []).filter((s) => s.implementation !== "planned");
  const columns: ColumnDef<Server, unknown>[] = [
    { header: "Server", accessorKey: "name", cell: ({ row: { original: s } }) => <span className="flex items-center gap-2 whitespace-nowrap"><EngineMark engine={s.engine} /><span className="font-medium">{s.name}</span>{s.is_demo && <SimulatedBadge />}</span> },
    { header: "Health", accessorKey: "health_status", cell: ({ row: { original: s } }) => <HealthBadge health={s.health_status} reasons={s.health_reasons} /> },
    { header: "Conn. use", accessorFn: (s) => k(s, "connections.utilization"), meta: { align: "right" }, cell: ({ row: { original: s } }) => { const v = k(s, "connections.utilization"); return v == null ? "—" : <div className="ml-auto w-20"><div className="text-right text-[12px]">{pct(v, 0)}</div><Meter value={v} /></div>; } },
    { header: "Active", accessorFn: (s) => k(s, "connections.active"), meta: { align: "right" }, cell: ({ getValue }) => compactNum(getValue() as number) },
    { header: "TPS", accessorFn: (s) => k(s, "transactions.per_sec"), meta: { align: "right" }, cell: ({ getValue }) => compactNum(getValue() as number) },
    { header: "Latency", accessorFn: (s) => k(s, "query.mean_latency_ms"), meta: { align: "right" }, cell: ({ getValue }) => ms(getValue() as number) },
    { header: "Cache hit", accessorFn: (s) => k(s, "cache.hit_ratio"), meta: { align: "right" }, cell: ({ getValue }) => { const v = getValue() as number | null; return <span className={cn(v != null && v < 95 && "font-medium text-warn")}>{pct(v)}</span>; } },
    { header: "Blocked", accessorFn: (s) => k(s, "sessions.blocked"), meta: { align: "right" }, cell: ({ getValue }) => { const v = getValue() as number | null; return v ? <Badge tone="crit">{v}</Badge> : (v ?? "—"); } },
    { header: "Lag", accessorFn: (s) => k(s, "replication.lag_seconds"), meta: { align: "right" }, cell: ({ getValue }) => duration(getValue() as number | null) },
    { header: "CPU", accessorFn: (s) => k(s, "cpu.percent"), meta: { align: "right" }, cell: ({ getValue }) => pct(getValue() as number | null, 0) },
    { header: "Size", accessorFn: (s) => k(s, "storage.database_bytes"), meta: { align: "right" }, cell: ({ getValue }) => bytes(getValue() as number) },
  ];
  return (
    <div>
      <PageHeader title="Monitoring overview" description="Latest collected values for every monitored server. Select a row for its full dashboard."
        crumbs={[{ label: "Monitoring" }, { label: "Overview" }]} />
      <Card><DataTable columns={columns} data={rows} loading={servers.isLoading} error={servers.error} dense onRowClick={(s) => navigate(`/servers/${s.id}?tab=performance`)}
        initialSort={[{ id: "health_status", desc: false }]} pageSize={100} /></Card>
    </div>
  );
}

const METRICS: [string, string, string][] = [
  ["connections.total", "Connections", "count"], ["connections.utilization", "Connection utilization", "%"], ["transactions.per_sec", "Transactions per second", "1/s"],
  ["query.mean_latency_ms", "Mean statement latency", "ms"], ["cache.hit_ratio", "Cache hit ratio", "%"], ["io.blocks_read_per_sec", "Physical reads per second", "1/s"],
  ["sessions.blocked", "Blocked sessions", "count"], ["replication.lag_seconds", "Replication lag", "s"], ["cpu.percent", "CPU", "%"], ["storage.database_bytes", "Database size", "B"],
];

export function PerformanceComparePage() {
  const servers = useAllServers();
  const list = (servers.data?.items ?? []).filter((s) => s.implementation !== "planned");
  const [metric, setMetric] = useState(METRICS[0][0]);
  const [range, setRange] = useState<RangeValue>({ range: "24h" });
  const [picked, setPicked] = useState<string[]>([]);
  const chosen = useMemo(() => (picked.length ? picked : list.slice(0, 4).map((s) => s.id)), [picked, list]);
  const refreshMs = useRefreshMs();
  // one request for every selected server (no per-server round trips)
  const q = useQuery({
    queryKey: ["metrics-range", [...chosen].sort(), metric, range],
    queryFn: ({ signal }) =>
      get<SeriesResponse>("/metrics/range", { metric: [metric], server_id: chosen, source: "all", ...rangeQuery(range) }, signal),
    enabled: chosen.length > 0,
    refetchInterval: refetchFor(refreshMs, range.range),
    placeholderData: (p) => p,
  });
  const meta = METRICS.find((m) => m[0] === metric)!;
  const series = chosen.map((id) => ({
    key: id,
    label: list.find((s) => s.id === id)?.name ?? id,
    points: q.data?.series.find((s) => s.server_id === id && !s.label)?.points ?? [],
  }));
  const toggle = (id: string) => setPicked((p) => { const base = p.length ? p : chosen; return base.includes(id) ? base.filter((x) => x !== id) : base.length >= 6 ? base : [...base, id]; });
  return (
    <div>
      <PageHeader title="Performance" description="Compare one metric across up to six servers." crumbs={[{ label: "Monitoring" }, { label: "Performance" }]} />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select aria-label="Metric" className="w-64" value={metric} onChange={(e) => setMetric(e.target.value)}>{METRICS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
        <TimeRangePicker value={range} onChange={setRange} />
        <AutoRefreshPicker />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_280px]">
        <Card><CardHeader title={meta[1]} /><CardBody>
          {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : <MetricChart height={360} area={false} unit={meta[2]} series={series} />}
        </CardBody></Card>
        <Card>
          <CardHeader title="Servers" description="Select up to six" />
          <ul className="scroll-thin max-h-[420px] overflow-y-auto p-2">
            {list.map((s) => (
              <li key={s.id}>
                <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-[13px] hover:bg-surface-2">
                  <input type="checkbox" checked={chosen.includes(s.id)} onChange={() => toggle(s.id)} />
                  <EngineMark engine={s.engine} /> <span className="truncate">{s.name}</span>
                </label>
              </li>
            ))}
            {!list.length && <EmptyState title="No monitored servers" />}
          </ul>
        </Card>
      </div>
    </div>
  );
}

export function SessionsPage() {
  return <ServerScopedPage title="Sessions" description="Live sessions with blocking information. Cancelling or terminating requires session.manage and is audited."
    crumbs={[{ label: "Monitoring" }, { label: "Sessions" }]} perm="session.view" cap="sessions">{(s) => <SessionsTab server={s} />}</ServerScopedPage>;
}

export function LocksPage() {
  return <ServerScopedPage title="Locks" description="Lock requests and the sessions blocking them." crumbs={[{ label: "Monitoring" }, { label: "Locks" }]}
    perm="session.view" cap="locks">{(s) => <LocksTab server={s} />}</ServerScopedPage>;
}

export function ReplicationPage() {
  const servers = useAllServers();
  const navigate = useNavigate();
  const rows = (servers.data?.items ?? []).filter((s) => s.cluster || s.key_metrics["replication.lag_seconds"] != null);
  return (
    <div>
      <PageHeader title="Replication" description="Replication roles and lag across clustered servers." crumbs={[{ label: "Monitoring" }, { label: "Replication" }]} />
      <Card className="mb-4">
        <DataTable data={rows} loading={servers.isLoading} dense onRowClick={(s) => navigate(`/servers/${s.id}?tab=replication`)}
          empty={<EmptyState title="No replicated servers">Servers appear here when they belong to a cluster or report replication lag.</EmptyState>}
          columns={[
            { header: "Server", accessorKey: "name", cell: ({ row: { original: s } }) => <span className="flex items-center gap-2"><EngineMark engine={s.engine} /><span className="font-medium">{s.name}</span>{s.is_demo && <SimulatedBadge />}</span> },
            { header: "Cluster", accessorFn: (s) => s.cluster?.name ?? "" },
            { header: "Role", accessorKey: "cluster_role" },
            { header: "Environment", accessorFn: (s) => s.environment?.name ?? "" },
            { header: "Lag", accessorFn: (s) => s.key_metrics["replication.lag_seconds"], meta: { align: "right" }, cell: ({ getValue }) => { const v = getValue() as number | null; return <span className={cn(v != null && v > 60 && "font-medium text-crit")}>{duration(v)}</span>; } },
            { header: "Health", accessorKey: "health_status", cell: ({ row: { original: s } }) => <HealthBadge health={s.health_status} reasons={s.health_reasons} /> },
          ] as ColumnDef<Server, unknown>[]} />
      </Card>
      <ServerScopedPage title="Replication detail" description="Replica, slot and upstream details for one server." crumbs={[]} perm="performance.view" cap="replication">
        {(s) => <ReplicationTab server={s} />}
      </ServerScopedPage>
    </div>
  );
}
