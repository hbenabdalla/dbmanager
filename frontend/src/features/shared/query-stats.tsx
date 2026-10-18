import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { FileSearch } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get } from "@/api/client";
import type { QueryStat } from "@/api/types";
import { MetricChart } from "@/components/charts";
import { CodeBlock } from "@/components/page";
import { EngineMark, SimulatedBadge } from "@/components/status";
import { useSourceParam } from "@/lib/fleet-source";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { useAuth } from "@/lib/auth";
import { compactNum, ms, relTime } from "@/lib/format";

const WINDOWS: [string, string][] = [["1h", "last hour"], ["6h", "last 6 hours"], ["24h", "last 24 hours"], ["7d", "last 7 days"]];
const SORTS: [string, string][] = [["total_ms", "Total time"], ["mean_ms", "Mean latency"], ["calls", "Executions"], ["physical_reads", "Physical reads"], ["logical_reads", "Logical reads"], ["rows", "Rows"], ["temp_blocks", "Temp usage"], ["cpu_ms", "CPU time"]];

function QueryDialog({ q, onClose }: { q: QueryStat & { server_id: string }; onClose: () => void }) {
  const navigate = useNavigate();
  const { can } = useAuth();
  const hist = useQuery({ queryKey: ["qhist", q.server_id, q.query_id], queryFn: () => get<{ points: { t: string; calls_per_sec: number; mean_ms: number | null }[] }>(`/servers/${q.server_id}/query-stats/${q.query_id}/history`, { hours: 24 }) });
  const pts = hist.data?.points ?? [];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-3xl" title={`Statement ${q.query_id}`}
      description={`${q.database ?? ""}${q.username ? `, run as ${q.username}` : ""}`}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Close</Button>
        {can("query.explain") && <Button variant="primary" onClick={() => navigate(`/queries/plans?server=${q.server_id}&database=${encodeURIComponent(q.database ?? "")}&sql=${encodeURIComponent(q.query_text)}`)}><FileSearch /> Explain this query</Button>}
      </>}>
      <div className="space-y-4">
        <CodeBlock code={q.query_text} maxHeight="14rem" />
        <div className="grid grid-cols-2 gap-3 text-[13px] sm:grid-cols-4">
          {[["Executions (window)", compactNum(q.calls)], ["Mean", ms(q.mean_ms)], ["Max", ms(q.max_ms)], ["Total", ms(q.total_ms)], ["Rows", compactNum(q.rows)], ["Logical reads", compactNum(q.logical_reads)], ["Physical reads", compactNum(q.physical_reads)], ["Temp blocks", compactNum(q.temp_blocks)]].map(([k, v]) => (
            <div key={k} className="rounded-md border border-border px-3 py-2"><div className="text-[12px] text-muted">{k}</div><div className="font-semibold tabular">{v}</div></div>
          ))}
        </div>
        <div>
          <h4 className="mb-1 text-[13px] font-semibold">Last 24 hours</h4>
          {hist.isLoading ? <SkeletonRows rows={3} /> : pts.length < 2 ? <p className="text-[13px] text-muted">Not enough history yet. Query statistics are sampled every 5 minutes.</p> : (
            <div className="grid gap-3 sm:grid-cols-2">
              <MetricChart height={150} unit="ms" series={[{ key: "mean", label: "Mean latency", points: pts.map((p) => ({ t: p.t, v: p.mean_ms ?? 0, max: p.mean_ms ?? 0 })) }]} />
              <MetricChart height={150} unit="1/s" series={[{ key: "cps", label: "Executions/s", points: pts.map((p) => ({ t: p.t, v: p.calls_per_sec, max: p.calls_per_sec })) }]} />
            </div>
          )}
        </div>
      </div>
    </Dialog>
  );
}

const METRIC_OF: Record<string, { label: string; get: (q: QueryStat) => number | null; fmt: (v: number) => string }> = {
  total_ms: { label: "Total time", get: (q) => q.total_ms, fmt: (v) => ms(v) },
  mean_ms: { label: "Mean latency", get: (q) => q.mean_ms, fmt: (v) => ms(v) },
  calls: { label: "Executions", get: (q) => q.calls, fmt: (v) => compactNum(v) },
  physical_reads: { label: "Physical reads", get: (q) => q.physical_reads, fmt: (v) => compactNum(v) },
  logical_reads: { label: "Logical reads", get: (q) => q.logical_reads, fmt: (v) => compactNum(v) },
  rows: { label: "Rows", get: (q) => q.rows, fmt: (v) => compactNum(v) },
  temp_blocks: { label: "Temp usage", get: (q) => q.temp_blocks, fmt: (v) => compactNum(v) },
  cpu_ms: { label: "CPU time", get: (q) => q.cpu_ms, fmt: (v) => ms(v) },
};

/** Top-N statements by the selected metric, with each one's share of the total. Mean and max are
 *  shown as engines report them; percentiles are not, because no engine exposes per-statement
 *  latency distributions in its cumulative statistics. */
function TopQueriesChart({ rows, sort, onSelect }: { rows: QueryStat[]; sort: string; onSelect: (r: QueryStat) => void }) {
  const m = METRIC_OF[sort] ?? METRIC_OF.total_ms;
  const vals = rows.map((r) => ({ r, v: m.get(r) ?? 0 })).filter((x) => x.v > 0).sort((a, b) => b.v - a.v);
  const total = vals.reduce((s, x) => s + x.v, 0);
  const top = vals.slice(0, 10);
  if (!top.length) return null;
  const max = top[0].v;
  const topShare = total ? (top.reduce((s, x) => s + x.v, 0) / total) * 100 : 0;
  return (
    <div className="border-b border-border px-3 py-3">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-[13px] font-semibold">Top {top.length} by {m.label.toLowerCase()}</h3>
        <span className="text-[12px] text-muted">These statements account for <span className="tabular font-medium text-text-2">{topShare.toFixed(0)}%</span> of {m.label.toLowerCase()} in this list</span>
      </div>
      <ol className="anim-stagger grid gap-x-6 gap-y-1.5 xl:grid-flow-col xl:grid-cols-2 xl:grid-rows-5">
        {top.map(({ r, v }, i) => (
          <li key={`${r.server_id ?? ""}-${r.query_id}`}>
            <button type="button" onClick={() => onSelect(r)} className="group grid w-full grid-cols-[20px_minmax(0,1fr)_auto] items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-surface-2">
              <span className="tabular text-2xs text-muted">{i + 1}</span>
              <span className="min-w-0">
                <code className="block truncate font-mono text-[11.5px] text-text-2 group-hover:text-text">{r.query_text}</code>
                <span className="mt-0.5 block h-1.5 overflow-hidden rounded-full bg-surface-3">
                  <span className="block h-full rounded-full bg-[var(--chart-1)] transition-[width] duration-300" style={{ width: `${(v / max) * 100}%` }} />
                </span>
              </span>
              <span className="tabular whitespace-nowrap text-right text-[12px]">{m.fmt(v)}<span className="block text-2xs text-muted">{total ? ((v / total) * 100).toFixed(1) : 0}%{sort !== "mean_ms" && r.max_ms ? ` · max ${ms(r.max_ms)}` : ""}</span></span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function QueryStatsTable({ serverId, slowOnly, fleet }: { serverId?: string; slowOnly?: boolean; fleet?: boolean }) {
  const [sort, setSort] = useState(slowOnly ? "mean_ms" : "total_ms");
  const [filter, setFilter] = useState("");
  const [range, setRange] = useState("1h");
  const [selected, setSelected] = useState<(QueryStat & { server_id: string }) | null>(null);
  const source = useSourceParam();
  const q = useQuery({
    queryKey: ["query-stats", serverId ?? "fleet", sort, slowOnly, range, fleet || !serverId ? source : null],
    queryFn: async () => {
      if (fleet || !serverId) return { items: await get<QueryStat[]>("/queries/top", { sort, source, range, limit: 200, min_mean_ms: slowOnly ? 500 : undefined }), snapshot_at: null, text_masked: false, capability: null };
      return get<{ items: QueryStat[]; snapshot_at: string | null; text_masked: boolean; capability: { level: string; note?: string } | null }>(`/servers/${serverId}/query-stats`, { sort, range, min_mean_ms: slowOnly ? 500 : undefined, limit: 200 });
    },
    // query telemetry is collected every 5 minutes: polling faster shows nothing new
    refetchInterval: 300_000,
  });
  const rows = useMemo(() => (q.data?.items ?? []).filter((r) => !filter || r.query_text.toLowerCase().includes(filter.toLowerCase()) || r.server_name?.toLowerCase().includes(filter.toLowerCase())), [q.data, filter]);
  const columns: ColumnDef<QueryStat, unknown>[] = [
    { header: "Statement", accessorKey: "query_text", enableSorting: false, cell: ({ row: { original: r } }) => (
      <div className="min-w-[320px] max-w-[620px]"><code className="block truncate font-mono text-[12px]">{r.query_text}</code>
        <div className="mt-0.5 flex gap-2 text-[11.5px] text-muted">{r.database}{r.username ? <span>as {r.username}</span> : null}</div></div>
    ) },
    ...(fleet || !serverId ? [{ header: "Server", accessorKey: "server_name", cell: ({ row: { original: r } }: { row: { original: QueryStat } }) => <span className="flex items-center gap-1.5 whitespace-nowrap"><EngineMark engine={r.engine ?? ""} />{r.server_name}{r.is_demo && <SimulatedBadge />}</span> } as ColumnDef<QueryStat, unknown>] : []),
    { header: "Calls", accessorKey: "calls", meta: { align: "right" }, cell: ({ getValue }) => compactNum(getValue() as number) },
    { header: "Mean", accessorKey: "mean_ms", meta: { align: "right" }, cell: ({ row: { original: r } }) => <span className={r.mean_ms > 1000 ? "font-medium text-warn" : ""}>{ms(r.mean_ms)}</span> },
    { header: "Exec/s", id: "cps", accessorFn: (r) => r.interval?.calls_per_sec ?? null, meta: { align: "right" }, cell: ({ getValue }) => compactNum(getValue() as number | null) },
    { header: "Total", accessorKey: "total_ms", meta: { align: "right" }, cell: ({ getValue }) => ms(getValue() as number) },
    { header: "Rows", accessorKey: "rows", meta: { align: "right" }, cell: ({ getValue }) => compactNum(getValue() as number) },
    { header: "Phys. reads", accessorKey: "physical_reads", meta: { align: "right" }, cell: ({ getValue }) => compactNum(getValue() as number) },
    { header: "Temp", accessorKey: "temp_blocks", meta: { align: "right" }, cell: ({ getValue }) => compactNum(getValue() as number) },
  ];
  if (q.error) return <Card><ErrorState error={q.error} onRetry={() => q.refetch()} /></Card>;
  const cap = (q.data as { capability?: { level: string; note?: string } | null } | undefined)?.capability;
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <Input aria-label="Filter statements" className="w-72" placeholder="Filter by SQL text or server" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <Select aria-label="Sort by" className="w-44" value={sort} onChange={(e) => setSort(e.target.value)}>{SORTS.map(([v, l]) => <option key={v} value={v}>Sort by {l.toLowerCase()}</option>)}</Select>
        <Select aria-label="Activity window" className="w-44" value={range} onChange={(e) => setRange(e.target.value)}>{WINDOWS.map(([v, l]) => <option key={v} value={v}>Activity in the {l}</option>)}</Select>
        <span className="ml-auto text-[12px] text-muted">
          {(q.data as { text_masked?: boolean })?.text_masked && "Literal values are masked for your role. "}
          {(q.data as { snapshot_at?: string | null })?.snapshot_at ? `Last sample ${relTime((q.data as { snapshot_at: string }).snapshot_at)}` : null}
        </span>
      </div>
      {!q.isLoading && <TopQueriesChart rows={rows} sort={sort} onSelect={(r) => setSelected({ ...r, server_id: r.server_id ?? serverId! })} />}
      <DataTable columns={columns} data={rows} loading={q.isLoading} dense onRowClick={(r) => setSelected({ ...r, server_id: r.server_id ?? serverId! })}
        empty={<EmptyState title="No query statistics yet">{cap && cap.level !== "FULL" && cap.level !== "PARTIAL" ? cap.note : slowOnly ? "No statement averages more than 500 ms." : "Statistics appear after the first collection (every 5 minutes)."}</EmptyState>} />
      {selected && <QueryDialog q={selected} onClose={() => setSelected(null)} />}
    </Card>
  );
}
