/**
 * Performance intelligence (0.13): query regressions, period-vs-period and plan-vs-plan
 * comparison, capacity forecasts and the before/after validation of resolved recommendations.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { ArrowRight, GitCompare, TrendingUp } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, del, get } from "@/api/client";
import { serverCan } from "@/api/hooks";
import type { ExplainResult, Finding, ServerDetail } from "@/api/types";
import { ForecastChart, type ForecastBandPoint } from "@/components/charts";
import { PageHeader, Stat } from "@/components/page";
import { PlanViewer } from "@/components/plan-graph";
import { DatabaseEngineIcon, SeverityBadge, SimulatedBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Field, Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { useSourceParam } from "@/lib/fleet-source";
import { bytes, compactNum, dateTime, ms, num } from "@/lib/format";
import { ServerScopedPage } from "../shared/server-scoped";

// ---- types --------------------------------------------------------------------------------------
export interface Regression {
  database: string | null; fingerprint: string; kind: "latency" | "plan_change" | "latency+plan"; regressed: boolean; plan_changed: boolean;
  baseline_mean_ms: number; recent_mean_ms: number; change_pct: number | null; delta_ms: number; baseline_calls: number; recent_calls: number;
  baseline_plan_id: string | null; recent_plan_id: string | null; baseline_plans: number | null; recent_plans: number | null;
  excess_ms: number; query_text: string;
}
interface RegressionResponse { plan_identity: boolean; notes: string[]; text_masked: boolean; settings: Record<string, number>; items: Regression[] }

interface PeriodRow { calls: number; total_ms: number; mean_ms: number | null; rows: number | null; cpu_ms: number | null; logical_reads: number | null; physical_reads: number | null }
export interface CompareItem {
  database: string | null; fingerprint: string; status: "new" | "gone" | "slower" | "faster" | "similar" | "unclassified"; a: PeriodRow; b: PeriodRow;
  mean_change_pct: number | null; calls_change_pct: number | null; total_ms_delta: number; query_text: string;
}
interface CompareResponse {
  a: { start: string; end: string }; b: { start: string; end: string }; summary: Record<"a" | "b", { calls: number; total_ms: number; mean_ms: number | null }>;
  status_counts: Record<string, number>; items: CompareItem[]; total_statements: number; text_masked: boolean; notes: string[];
}

export interface Forecast {
  server_id: string; server_name?: string; engine?: string; scope: string; object: string | null; points: number; history_days: number;
  current_bytes: number | null; capacity_bytes: number | null; capacity_basis: string; growth_bytes_per_day: number | null;
  growth_low: number | null; growth_high: number | null; days_to_full: { likely: number | null; earliest: number | null; latest: number | null } | null;
  reason: string | null; band?: ForecastBandPoint[]; history?: { t: string; v: number }[];
}

interface SavedPlan {
  id: string; server_id: string; server_name: string; engine: string; database: string | null; fingerprint: string | null; label: string;
  statement: string; mode: string; total_cost: number | null; created_by: string | null; created_at: string; plan?: ExplainResult;
}
interface PlanDiff {
  same_engine: boolean; identical_shape: boolean; cost_change_pct: number | null; execution_change_pct: number | null;
  a: { nodes: number; total_cost: number | null }; b: { nodes: number; total_cost: number | null };
  operations_added: string[]; operations_removed: string[]; access_path_changes: { relation: string; a: string[]; b: string[] }[];
  issues_added: { code: string; title: string }[]; issues_resolved: { code: string; title: string }[]; notes: string[];
}

export interface Validation {
  status: "measuring" | "pending" | "improved" | "unchanged" | "regressed" | "insufficient_data" | "not_measurable" | "reopened";
  method?: { kind: string; metric: string; better: "lower" | "higher"; fingerprint?: string };
  before?: { value: number | null; samples: number }; after?: { value: number | null; samples: number } | null;
  change_pct?: number | null; window_hours: number; due_at?: string; reason?: string;
}

const pctText = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${num(v, 0)}%`);
const daysText = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v < 1 ? "< 1 day" : `${Math.round(v).toLocaleString()} days`);

// ---- query regressions ----------------------------------------------------------------------------
function RegressionsView({ server }: { server: ServerDetail }) {
  const [recent, setRecent] = useState(1);
  const [baseline, setBaseline] = useState(7);
  const q = useQuery({
    queryKey: ["query-regressions", server.id, recent, baseline],
    queryFn: () => get<RegressionResponse>(`/servers/${server.id}/query-regressions`, { recent_hours: recent, baseline_days: baseline }),
  });
  const columns: ColumnDef<Regression, unknown>[] = [
    { header: "Detected", accessorKey: "kind", cell: ({ row: { original: r } }) => (
      <div className="flex flex-col items-start gap-1">
        {r.regressed && <Badge tone="crit">slower</Badge>}
        {r.plan_changed && <Badge tone="warn">plan changed</Badge>}
      </div>
    ) },
    { header: "Statement", accessorKey: "query_text", cell: ({ row: { original: r } }) => (
      <div className="min-w-[220px] max-w-[400px]"><div className="truncate font-mono text-[12px]" title={r.query_text}>{r.query_text || r.fingerprint}</div>
        <div className="text-[11.5px] text-muted">{r.database ?? "—"} · <span className="font-mono">{r.fingerprint}</span></div></div>
    ) },
    { header: "Baseline mean", accessorKey: "baseline_mean_ms", meta: { align: "right" }, cell: ({ getValue }) => ms(getValue() as number) },
    { header: "Recent mean", accessorKey: "recent_mean_ms", meta: { align: "right" }, cell: ({ getValue }) => <span className="font-medium">{ms(getValue() as number)}</span> },
    { header: "Change", id: "pct", accessorKey: "change_pct", meta: { align: "right" }, cell: ({ getValue }) => pctText(getValue() as number | null) },
    { header: "Calls recent / baseline", accessorKey: "recent_calls", meta: { align: "right" }, cell: ({ row: { original: r } }) => `${compactNum(r.recent_calls)} / ${compactNum(r.baseline_calls)}` },
    { header: "Plan", id: "plan", cell: ({ row: { original: r } }) => r.baseline_plan_id || r.recent_plan_id
      ? <span className="font-mono text-[11.5px]">{r.baseline_plan_id ?? "?"} → {r.recent_plan_id ?? "?"}</span>
      : r.recent_plans ? <span className="text-[12px]">{r.baseline_plans} → {r.recent_plans} plans</span> : <span className="text-muted">—</span> },
    { header: "", id: "go", cell: ({ row: { original: r } }) => (
      <div className="flex flex-col gap-0.5 whitespace-nowrap text-[12px]">
        <Link className="text-accent hover:underline" to={`/queries/compare?server=${server.id}&fp=${r.fingerprint}`}>Compare</Link>
        {serverCan(server, "query.explain") && !q.data?.text_masked && r.query_text && (
          <Link className="text-accent hover:underline" to={`/queries/plans?server=${server.id}&sql=${encodeURIComponent(r.query_text)}${r.database ? `&database=${encodeURIComponent(r.database)}` : ""}&fp=${r.fingerprint}`}>Explain</Link>
        )}
      </div>
    ) },
  ];
  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex flex-wrap items-end gap-3">
          <Field label="Recent window"><Select className="w-40" value={recent} onChange={(e) => setRecent(Number(e.target.value))}>{[1, 3, 6, 12, 24].map((h) => <option key={h} value={h}>last {h} h</option>)}</Select></Field>
          <Field label="Baseline"><Select className="w-40" value={baseline} onChange={(e) => setBaseline(Number(e.target.value))}>{[1, 3, 7, 14, 30].map((d) => <option key={d} value={d}>previous {d} days</option>)}</Select></Field>
          {q.data && <p className="max-w-xl pb-1 text-[12.5px] text-muted">
            Reported when the mean latency rose by at least {q.data.settings.regression_threshold_pct}% and {q.data.settings.regression_min_delta_ms} ms,
            with at least {q.data.settings.regression_min_calls} executions in both windows. Thresholds are platform settings.
          </p>}
        </CardBody>
      </Card>
      {q.data?.notes.map((n) => <p key={n} className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[12.5px] text-muted">{n}</p>)}
      <Card>
        <DataTable columns={columns} data={q.data?.items} loading={q.isLoading} error={q.error} onRetry={() => q.refetch()}
          empty={<EmptyState icon={<TrendingUp />} title="No regressions">No statement is measurably slower than its own baseline. Statements need enough executions in both windows to be compared.</EmptyState>} />
      </Card>
    </div>
  );
}

export function QueryRegressionsPage() {
  return (
    <ServerScopedPage title="Query regressions" description="Statements whose mean latency is well above their own baseline, and execution plan changes where the engine exposes plan identity."
      crumbs={[{ label: "Queries" }, { label: "Regressions" }]} perm="query.view">
      {(s) => <RegressionsView key={s.id} server={s} />}
    </ServerScopedPage>
  );
}

// ---- period-vs-period comparison ----------------------------------------------------------------
const PRESETS: [string, string][] = [
  ["hour_vs_previous", "Last hour vs the hour before"],
  ["day_vs_previous", "Last 24 h vs the 24 h before"],
  ["day_vs_week_ago", "Last 24 h vs the same day a week ago"],
  ["week_vs_previous", "Last 7 days vs the week before"],
  ["custom", "Custom periods"],
];
const STATUS_TONE: Record<string, "crit" | "ok" | "neutral" | "accent" | "warn" | "outline"> = { slower: "crit", faster: "ok", similar: "neutral", new: "accent", gone: "warn", unclassified: "outline" };

function toLocalInput(d: Date): string {
  const z = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
  return z.toISOString().slice(0, 16);
}

function CompareView({ server, focus }: { server: ServerDetail; focus?: string }) {
  const [preset, setPreset] = useState("day_vs_previous");
  const now = useMemo(() => new Date(), []);
  const [custom, setCustom] = useState({
    a_start: toLocalInput(new Date(now.getTime() - 48 * 3600e3)), a_end: toLocalInput(new Date(now.getTime() - 24 * 3600e3)),
    b_start: toLocalInput(new Date(now.getTime() - 24 * 3600e3)), b_end: toLocalInput(now),
  });
  const [status, setStatus] = useState("");
  const params = preset === "custom"
    ? Object.fromEntries(Object.entries(custom).map(([k, v]) => [k, new Date(v).toISOString()]))
    : { preset };
  const q = useQuery({
    queryKey: ["query-compare", server.id, params],
    queryFn: () => get<CompareResponse>(`/servers/${server.id}/query-compare`, { ...params, limit: 200 }),
    retry: false,
  });
  const rows = (q.data?.items ?? []).filter((i) => !status || i.status === status);
  const columns: ColumnDef<CompareItem, unknown>[] = [
    { header: "Status", accessorKey: "status", cell: ({ getValue }) => <Badge tone={STATUS_TONE[getValue() as string]}>{getValue() as string}</Badge> },
    { header: "Statement", accessorKey: "query_text", cell: ({ row: { original: r } }) => (
      <div className={`min-w-[240px] max-w-[460px] ${focus === r.fingerprint ? "rounded bg-accent-soft px-1" : ""}`}>
        <div className="truncate font-mono text-[12px]">{r.query_text || r.fingerprint}</div>
        <div className="text-[11.5px] text-muted">{r.database ?? "—"}</div></div>
    ) },
    { header: "Mean A → B", id: "mean", meta: { align: "right" }, cell: ({ row: { original: r } }) => <span className="whitespace-nowrap">{ms(r.a.mean_ms)} → <b>{ms(r.b.mean_ms)}</b></span> },
    { header: "Mean change", accessorKey: "mean_change_pct", meta: { align: "right" }, cell: ({ getValue }) => pctText(getValue() as number | null) },
    { header: "Calls A → B", id: "calls", meta: { align: "right" }, cell: ({ row: { original: r } }) => <span className="whitespace-nowrap">{compactNum(r.a.calls)} → {compactNum(r.b.calls)}</span> },
    { header: "Total time Δ", accessorKey: "total_ms_delta", meta: { align: "right" }, cell: ({ getValue }) => { const v = getValue() as number; return <span className={v > 0 ? "text-crit" : "text-ok"}>{v > 0 ? "+" : ""}{ms(v)}</span>; } },
    { header: "Physical reads A → B", id: "reads", meta: { align: "right" }, cell: ({ row: { original: r } }) => `${compactNum(r.a.physical_reads)} → ${compactNum(r.b.physical_reads)}` },
  ];
  const s = q.data?.summary;
  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex flex-wrap items-end gap-3">
          <Field label="Compare"><Select className="w-80" value={preset} onChange={(e) => setPreset(e.target.value)}>{PRESETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
          {preset === "custom" && (["a", "b"] as const).map((p) => (
            <div key={p} className="flex items-end gap-2">
              <Field label={`Period ${p.toUpperCase()} from`}><Input type="datetime-local" value={custom[`${p}_start`]} onChange={(e) => setCustom((c) => ({ ...c, [`${p}_start`]: e.target.value }))} /></Field>
              <Field label="to"><Input type="datetime-local" value={custom[`${p}_end`]} onChange={(e) => setCustom((c) => ({ ...c, [`${p}_end`]: e.target.value }))} /></Field>
            </div>
          ))}
          <Field label="Show"><Select className="w-36" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statements</option>{["slower", "faster", "new", "gone", "similar"].map((x) => <option key={x} value={x}>{x} ({q.data?.status_counts[x] ?? 0})</option>)}
          </Select></Field>
        </CardBody>
      </Card>
      {(q.data?.notes ?? []).map((n) => <p key={n} role="status" className="rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-[12.5px] text-warn">{n}</p>)}
      {q.error ? <Card><ErrorState error={q.error} /></Card> : s && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Stat label="Executions A → B" value={`${compactNum(s.a.calls)} → ${compactNum(s.b.calls)}`} />
          <Stat label="Total execution time A → B" value={`${ms(s.a.total_ms)} → ${ms(s.b.total_ms)}`} tone={s.b.total_ms > s.a.total_ms * 1.2 ? "warn" : undefined} />
          <Stat label="Mean latency A → B" value={`${ms(s.a.mean_ms)} → ${ms(s.b.mean_ms)}`} sub={`A ${dateTime(q.data!.a.start)} – ${dateTime(q.data!.a.end)} · B ${dateTime(q.data!.b.start)} – ${dateTime(q.data!.b.end)}`} />
        </div>
      )}
      <Card>
        <DataTable columns={columns} data={rows} loading={q.isLoading} initialSort={[]}
          empty={<EmptyState icon={<GitCompare />} title="No statement activity in these periods">Query statistics are collected every few minutes; pick periods covered by the monitoring history.</EmptyState>} />
      </Card>
    </div>
  );
}

export function QueryComparePage() {
  const focus = new URLSearchParams(window.location.search).get("fp") ?? undefined;
  return (
    <ServerScopedPage title="Compare periods" description="Statement activity in a reference period (A) against a period under review (B): what got slower, faster, appeared or disappeared."
      crumbs={[{ label: "Queries" }, { label: "Compare periods" }]} perm="query.view">
      {(s) => <CompareView key={s.id} server={s} focus={focus} />}
    </ServerScopedPage>
  );
}

// ---- plan-vs-plan -------------------------------------------------------------------------------
function PlanCompareView({ server }: { server: ServerDetail }) {
  const qc = useQueryClient();
  const [picked, setPicked] = useState<string[]>([]);
  const plans = useQuery({ queryKey: ["saved-plans", server.id], queryFn: () => get<SavedPlan[]>(`/servers/${server.id}/plans`) });
  const [a, b] = picked;
  const cmp = useQuery({
    queryKey: ["plan-compare", a, b], enabled: !!a && !!b,
    queryFn: () => get<{ a: SavedPlan; b: SavedPlan; same_statement: boolean; diff: PlanDiff }>("/plans/compare", { a, b }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/plans/${id}`),
    onSuccess: (_r, id) => { toast.success("Saved plan deleted"); setPicked((p) => p.filter((x) => x !== id)); qc.invalidateQueries({ queryKey: ["saved-plans", server.id] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id].slice(-2)));
  const columns: ColumnDef<SavedPlan, unknown>[] = [
    { header: "", id: "pick", cell: ({ row: { original: p } }) => <input type="checkbox" aria-label={`Select ${p.label}`} checked={picked.includes(p.id)} onChange={() => toggle(p.id)} onClick={(e) => e.stopPropagation()} /> },
    { header: "Label", accessorKey: "label", cell: ({ row: { original: p } }) => <div><div className="font-medium">{p.label}</div><div className="max-w-[420px] truncate font-mono text-[11.5px] text-muted">{p.statement}</div></div> },
    { header: "Mode", accessorKey: "mode", cell: ({ getValue }) => <Badge tone={getValue() === "analyze" ? "warn" : "neutral"}>{getValue() as string}</Badge> },
    { header: "Cost", accessorKey: "total_cost", meta: { align: "right" }, cell: ({ getValue }) => compactNum(getValue() as number | null) },
    { header: "Saved", accessorKey: "created_at", cell: ({ row: { original: p } }) => <span className="whitespace-nowrap text-[12px]">{dateTime(p.created_at)}{p.created_by ? ` · ${p.created_by}` : ""}</span> },
    { header: "", id: "del", cell: ({ row: { original: p } }) => <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); remove.mutate(p.id); }}>Delete</Button> },
  ];
  const d = cmp.data?.diff;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Saved plans" description="Save a plan from Execution plans (“Save plan as”), then pick two to compare. The first selected is A." />
        <DataTable columns={columns} data={plans.data} loading={plans.isLoading} error={plans.error} onRowClick={(p) => toggle(p.id)}
          empty={<EmptyState title="No saved plans yet">On Execution plans, enter a label in “Save plan as” before running EXPLAIN. Save one before and one after a change (an index, new statistics) to compare them.</EmptyState>} />
      </Card>
      {cmp.isLoading && a && b && <SkeletonRows rows={4} />}
      {cmp.error && <Card><ErrorState error={cmp.error} /></Card>}
      {cmp.data && d && (
        <>
          <div className="grid gap-3 sm:grid-cols-4">
            <Stat label="Cost A → B" value={`${compactNum(d.a.total_cost)} → ${compactNum(d.b.total_cost)}`} sub={d.same_engine ? pctText(d.cost_change_pct) : "different engines"} tone={(d.cost_change_pct ?? 0) > 20 ? "warn" : undefined} />
            <Stat label="Operators A → B" value={`${d.a.nodes} → ${d.b.nodes}`} sub={d.identical_shape ? "same shape" : "shape changed"} />
            <Stat label="Execution time change" value={pctText(d.execution_change_pct)} sub="analyze plans only" />
            <Stat label="Statement" value={cmp.data.same_statement ? "identical" : "different"} />
          </div>
          <Card>
            <CardHeader title="Differences" />
            <CardBody className="grid gap-4 text-[13px] md:grid-cols-2">
              <section><h4 className="mb-1 font-semibold">Access path changes</h4>
                {d.access_path_changes.length === 0 ? <p className="text-muted">None.</p> : d.access_path_changes.map((c) => (
                  <div key={c.relation} className="flex flex-wrap items-center gap-1.5"><span className="font-mono">{c.relation}</span>: {c.a.join(", ") || "—"} <ArrowRight className="size-3.5" /> <b>{c.b.join(", ") || "—"}</b></div>
                ))}</section>
              <section><h4 className="mb-1 font-semibold">Plan issues</h4>
                {d.issues_added.map((i) => <div key={`+${i.code}`}><Badge tone="crit">new</Badge> {i.title}</div>)}
                {d.issues_resolved.map((i) => <div key={`-${i.code}`}><Badge tone="ok">resolved</Badge> {i.title}</div>)}
                {!d.issues_added.length && !d.issues_resolved.length && <p className="text-muted">No change.</p>}</section>
              <section><h4 className="mb-1 font-semibold">Operations only in B</h4>{d.operations_added.length ? <ul className="list-disc pl-5">{d.operations_added.map((o, i) => <li key={i} className="font-mono text-[12px]">{o}</li>)}</ul> : <p className="text-muted">None.</p>}</section>
              <section><h4 className="mb-1 font-semibold">Operations only in A</h4>{d.operations_removed.length ? <ul className="list-disc pl-5">{d.operations_removed.map((o, i) => <li key={i} className="font-mono text-[12px]">{o}</li>)}</ul> : <p className="text-muted">None.</p>}</section>
              {d.notes.map((n) => <p key={n} className="text-muted md:col-span-2">{n}</p>)}
            </CardBody>
          </Card>
          <div className="grid gap-4 xl:grid-cols-2">
            {([["A", cmp.data.a], ["B", cmp.data.b]] as const).map(([k, p]) => (
              <Card key={k}><CardHeader title={`${k}: ${p.label}`} description={`${p.mode} · cost ${compactNum(p.total_cost)} · ${dateTime(p.created_at)}`} />
                <CardBody>{p.plan?.root ? <PlanViewer root={p.plan.root} issues={p.plan.issues} analyzed={p.mode === "analyze"} raw={p.plan.raw} engine={p.engine} height={360} /> : <p className="text-muted">No plan tree.</p>}</CardBody></Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export function PlanComparePage() {
  return (
    <ServerScopedPage title="Compare plans" description="Side-by-side comparison of two saved execution plans: cost, operators, access paths and detected issues."
      crumbs={[{ label: "Queries" }, { label: "Compare plans" }]} perm="query.explain" cap="explain">
      {(s) => <PlanCompareView key={s.id} server={s} />}
    </ServerScopedPage>
  );
}

// ---- capacity forecasts -------------------------------------------------------------------------
function ForecastDetail({ f, onClose }: { f: Forecast; onClose: () => void }) {
  const q = useQuery({
    queryKey: ["forecast", f.server_id, f.scope],
    queryFn: () => get<{ items: Forecast[] }>(`/servers/${f.server_id}/capacity-forecast`, { scope: f.scope, days: 60 }),
  });
  const row = q.data?.items.find((x) => x.object === f.object) ?? null;
  return (
    <Card>
      <CardHeader title={`${f.server_name ?? ""}${f.object ? ` · ${f.object}` : ""}`} description="Daily size (solid), Theil–Sen forecast (dashed) and its 90% band (shaded)"
        actions={<Button size="sm" variant="ghost" onClick={onClose}>Close</Button>} />
      <CardBody>
        {q.isLoading ? <SkeletonRows rows={3} /> : q.error ? <ErrorState error={q.error} /> : row
          ? <ForecastChart history={row.history ?? []} band={row.band ?? []} capacity={row.capacity_bytes} />
          : <p className="text-muted">No history for this object.</p>}
      </CardBody>
    </Card>
  );
}

export function CapacityForecastPage() {
  const source = useSourceParam();
  const [scope, setScope] = useState<"server" | "database" | "tablespace">("server");
  const [selected, setSelected] = useState<Forecast | null>(null);
  const q = useQuery({ queryKey: ["capacity-forecast", scope, source], queryFn: () => get<{ items: Forecast[]; total: number }>("/capacity/forecast", { scope, source, limit: 500 }) });
  const columns: ColumnDef<Forecast, unknown>[] = [
    { header: "Server", accessorKey: "server_name", cell: ({ row: { original: f } }) => <span className="flex items-center gap-2 whitespace-nowrap">{f.engine && <DatabaseEngineIcon engine={f.engine} />}{f.server_name}</span> },
    ...(scope !== "server" ? [{ header: scope === "database" ? "Database" : "Tablespace", accessorKey: "object" } as ColumnDef<Forecast, unknown>] : []),
    { header: "Size", accessorKey: "current_bytes", meta: { align: "right" }, cell: ({ getValue }) => bytes(getValue() as number | null) },
    { header: "Growth / day (90% band)", accessorKey: "growth_bytes_per_day", meta: { align: "right" }, cell: ({ row: { original: f } }) => f.growth_bytes_per_day === null ? "—"
      : <span className="whitespace-nowrap">{bytes(f.growth_bytes_per_day)} <span className="text-muted">({bytes(f.growth_low)} – {bytes(f.growth_high)})</span></span> },
    { header: "Capacity", accessorKey: "capacity_bytes", meta: { align: "right" }, cell: ({ row: { original: f } }) => <span title={f.capacity_basis}>{bytes(f.capacity_bytes)}</span> },
    { header: "Full in (earliest · likely · latest)", id: "days", cell: ({ row: { original: f } }) => f.days_to_full
      ? <span className="whitespace-nowrap"><b className={(f.days_to_full.earliest ?? 999) <= 30 ? "text-crit" : ""}>{daysText(f.days_to_full.earliest)}</b> · {daysText(f.days_to_full.likely)} · {daysText(f.days_to_full.latest)}</span>
      : <span className="text-[12px] text-muted">{f.reason}</span> },
    { header: "History", accessorKey: "history_days", meta: { align: "right" }, cell: ({ getValue }) => `${num(getValue() as number, 0)} d` },
  ];
  return (
    <div>
      <PageHeader title="Capacity forecast" description="Robust growth trends (Theil–Sen, resistant to one-off loads and purges) with 90% bands. Nothing is forecast from less than 7 days of history or without a known capacity."
        crumbs={[{ label: "Monitoring" }, { label: "Capacity forecast" }]}
        actions={<Select aria-label="Scope" className="w-44" value={scope} onChange={(e) => { setScope(e.target.value as typeof scope); setSelected(null); }}>
          <option value="server">Server storage</option><option value="database">Databases</option><option value="tablespace">Tablespaces</option>
        </Select>} />
      <div className="space-y-4">
        {selected && <ForecastDetail key={`${selected.server_id}-${selected.object}`} f={selected} onClose={() => setSelected(null)} />}
        <Card>
          <DataTable columns={columns} data={q.data?.items} loading={q.isLoading} error={q.error} onRowClick={setSelected}
            empty={<EmptyState title="No size history yet">Sizes are written by the hourly inventory discovery (databases, tablespaces) and the storage collector (server). Forecasts appear after 7 days.</EmptyState>} />
        </Card>
        {scope === "database" && <p className="text-[12.5px] text-muted">Databases share their server's volume: a database's capacity is its size plus the server's free capacity, i.e. the date it would fill the volume if it were the only one growing.</p>}
      </div>
    </div>
  );
}

// ---- recommendation validation ------------------------------------------------------------------
const OUTCOME_TONE: Record<string, "ok" | "crit" | "neutral" | "warn" | "accent"> = {
  improved: "ok", regressed: "crit", unchanged: "neutral", measuring: "accent", pending: "accent", insufficient_data: "warn", not_measurable: "neutral", reopened: "warn",
};

export function ValidationBadge({ v }: { v: Validation | null | undefined }) {
  if (!v) return null;
  return <Badge tone={OUTCOME_TONE[v.status] ?? "neutral"}>{v.status.replace("_", " ")}</Badge>;
}

/** Before/after block shown in a resolved recommendation's dialog. */
export function ValidationDetail({ v }: { v: Validation }) {
  const metric = v.method ? (v.method.kind === "query" ? "mean statement latency" : v.method.metric) : null;
  const fmt = (x: number | null | undefined) => (x === null || x === undefined ? "—" : v.method?.kind === "query" ? ms(x) : num(x, 2));
  return (
    <div className="rounded-md border border-border p-3 text-[13px]" data-testid="validation">
      <div className="mb-1 flex items-center gap-2"><span className="font-semibold">Validation</span><ValidationBadge v={v} /></div>
      {v.status === "not_measurable" ? <p className="text-muted">{v.reason}</p> : (
        <>
          {metric && <p className="text-text-2">Measures {metric} ({v.method!.better} is better) over {v.window_hours} h before and after the resolution.</p>}
          <div className="mt-1.5 flex flex-wrap gap-x-6 gap-y-1 tabular">
            <span>Before: <b>{fmt(v.before?.value)}</b> <span className="text-muted">({compactNum(v.before?.samples)} samples)</span></span>
            <span>After: <b>{v.after ? fmt(v.after.value) : "measuring"}</b>{v.after && <span className="text-muted"> ({compactNum(v.after.samples)} samples)</span>}</span>
            {v.change_pct !== undefined && v.change_pct !== null && <span>Change: <b>{pctText(v.change_pct)}</b></span>}
          </div>
          {v.status === "measuring" && v.due_at && <p className="mt-1 text-[12px] text-muted">The after-window ends {dateTime(v.due_at)}.</p>}
          {v.reason && <p className="mt-1 text-[12px] text-muted">{v.reason}</p>}
        </>
      )}
    </div>
  );
}

export function RecommendationValidationPage() {
  const source = useSourceParam();
  const q = useQuery({ queryKey: ["validation-summary", source], queryFn: () => get<{ counts: Record<string, number>; recent: (Finding & { validation: Validation })[] }>("/recommendations/validation-summary", { source }) });
  const c = q.data?.counts ?? {};
  const columns: ColumnDef<Finding & { validation: Validation }, unknown>[] = [
    { header: "Outcome", id: "outcome", cell: ({ row: { original: f } }) => <ValidationBadge v={f.validation} /> },
    { header: "Recommendation", accessorKey: "title", cell: ({ row: { original: f } }) => <div className="min-w-[260px]"><div className="flex items-center gap-2"><SeverityBadge severity={f.severity} /><span className="font-medium">{f.title}</span></div><div className="font-mono text-[11.5px] text-muted">{f.rule_id}</div></div> },
    { header: "Server", accessorKey: "server_name", cell: ({ row: { original: f } }) => <span className="flex items-center gap-2 whitespace-nowrap"><DatabaseEngineIcon engine={f.engine} />{f.server_name}{f.is_demo && <SimulatedBadge />}</span> },
    { header: "Before → after", id: "ba", meta: { align: "right" }, cell: ({ row: { original: f } }) => <span className="tabular whitespace-nowrap">{num(f.validation.before?.value ?? null, 2)} → {num(f.validation.after?.value ?? null, 2)}</span> },
    { header: "Change", id: "chg", meta: { align: "right" }, cell: ({ row: { original: f } }) => pctText(f.validation.change_pct) },
    { header: "Resolved", accessorKey: "resolved_at", cell: ({ getValue }) => <span className="whitespace-nowrap text-[12px]">{dateTime(getValue() as string)}</span> },
  ];
  return (
    <div>
      <PageHeader title="Recommendation outcomes" description="Before/after measurement of resolved recommendations: did the change improve the metric that triggered it?"
        crumbs={[{ label: "Optimization" }, { label: "Outcomes" }]} />
      <div className="mb-4 grid gap-3 sm:grid-cols-4">
        <Stat label="Improved" value={c.improved ?? 0} tone="ok" />
        <Stat label="Unchanged" value={c.unchanged ?? 0} />
        <Stat label="Regressed" value={c.regressed ?? 0} tone={(c.regressed ?? 0) > 0 ? "crit" : undefined} />
        <Stat label="Still measuring" value={(c.measuring ?? 0) + (c.pending ?? 0)} sub={`${c.not_measurable ?? 0} not measurable · ${c.insufficient_data ?? 0} without data`} />
      </div>
      <Card>
        <CardHeader title="Latest measured outcomes" />
        <DataTable columns={columns} data={q.data?.recent} loading={q.isLoading} error={q.error}
          empty={<EmptyState title="No measured outcomes yet">When a recommendation is resolved (manually, or because its evidence disappeared), the metric is measured for a window before and after. Results appear once the after-window has passed.</EmptyState>} />
      </Card>
    </div>
  );
}
