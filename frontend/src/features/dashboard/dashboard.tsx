import { useQuery } from "@tanstack/react-query";
import { AutoRefreshPicker } from "@/components/auto-refresh";
import { useRefreshMs } from "@/lib/refresh";
import { ArrowRight, FilterX, RefreshCw, TrendingDown, TrendingUp } from "lucide-react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Sparkline, trendDelta } from "@/components/sparkline";
import { cn } from "@/lib/utils";
import { get } from "@/api/client";
import type { MetricPoint } from "@/api/types";
import { MetricChart } from "@/components/charts";
import { FleetStrip, type FleetTile } from "@/components/fleet-strip";
import { PageHeader } from "@/components/page";
import { DatabaseEngineIcon, HealthBadge, HEALTH_VAR, SeverityBadge, SimulatedBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton, StorageUnavailable } from "@/components/ui/states";
import { HEALTH_LABEL } from "@/lib/engines";
import { bytes, compactNum, relTime } from "@/lib/format";
import { useFleetSource } from "@/lib/fleet-source";
import { SourceFilter } from "@/components/fleet-source";
import { FleetOnboarding } from "./onboarding";
import { SetupChecklist } from "../setup/checklist";
import { useAuth } from "@/lib/auth";
import { useAlertSummary } from "../alerts/alerts";

interface Summary {
  kpis: Record<string, number>;
  health: Record<string, number>;
  engines: { engine: string; label: string; count: number }[];
  environments: { name: string; tier: string | null; servers: number; health: Record<string, number> }[];
  fleet: FleetTile[];
  attention: { id: string; name: string; engine: string; health: string; environment: string | null; reasons: string[]; last_collected_at: string | null; is_demo: boolean }[];
  recommendations_by_severity: Record<string, number>;
  findings_by_severity: Record<string, number>;
  top_recommendations: { id: string; rule_id: string; title: string; severity: string; server_id: string; server_name: string; engine: string; confidence: number }[];
  trends: { connections?: MetricPoint[]; latency_ms?: MetricPoint[]; tps?: MetricPoint[]; unavailable?: boolean };
  capacity: { server_id: string; server_name: string; engine: string; bytes: number }[];
  demo: boolean;
  source?: "real" | "simulated" | "all";
  sources?: { real: number; simulated: number };
}

const SEVERITIES = ["critical", "high", "medium", "low", "info"];

function BarList({ items, max, color, selected, onSelect }: {
  items: { key: string; label: React.ReactNode; value: number; display?: string; to?: string }[]; max?: number; color?: string;
  selected?: string; onSelect?: (key: string) => void;
}) {
  const m = max ?? Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="space-y-2">
      {items.map((i) => (
        <li key={i.key} className={cn("grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded text-[13px]", selected && selected !== i.key && "opacity-50")}>
          <span className="truncate">
            {onSelect ? <button type="button" className="hover:underline" aria-pressed={selected === i.key} onClick={() => onSelect(i.key)}>{i.label}</button>
              : i.to ? <Link className="hover:underline" to={i.to}>{i.label}</Link> : i.label}
          </span>
          <span className="tabular text-text-2">{i.display ?? i.value}</span>
          <div className="col-span-2 h-1.5 overflow-hidden rounded-full bg-surface-3">
            <div className="h-full rounded-full transition-[width] duration-300" style={{ width: `${(i.value / m) * 100}%`, background: color ?? "var(--accent)" }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function StackedHealth({ health, total }: { health: Record<string, number>; total: number }) {
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-surface-3">
      {["critical", "unavailable", "warning", "unknown", "healthy"].map((h) =>
        health[h] ? <div key={h} title={`${HEALTH_LABEL[h]}: ${health[h]}`} style={{ width: `${(health[h] / total) * 100}%`, background: HEALTH_VAR[h] }} /> : null,
      )}
    </div>
  );
}

type Tone = "ok" | "warn" | "crit" | "down" | undefined;

/** Command-center KPI: value, optional 24 h sparkline with change, and (when it maps to a fleet
 *  filter) a toggle that filters the fleet map and attention list below. */
function Kpi({ label, value, sub, tone, trend, active, onSelect, title }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: Tone; trend?: number[];
  active?: boolean; onSelect?: () => void; title?: string;
}) {
  const color = tone ? `var(--${tone})` : undefined;
  const delta = trend ? trendDelta(trend) : null;
  const body = (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-[12px] text-muted">{label}</span>
        {delta !== null && Math.abs(delta) >= 1 && (
          <span className={cn("inline-flex items-center gap-0.5 text-2xs tabular", delta > 0 ? "text-text-2" : "text-muted")} title="change over the last 24 hours">
            {delta > 0 ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}{Math.abs(delta).toFixed(0)}%
          </span>
        )}
      </div>
      <div className="mt-0.5 flex items-end justify-between gap-2">
        <span className="tabular text-[22px] font-semibold leading-tight" style={{ color }}>{value}</span>
        {trend && <span className="min-w-0 shrink overflow-hidden"><Sparkline values={trend} width={72} height={24} color={color ?? "var(--chart-1)"} label={`${label} trend, 24 hours`} /></span>}
      </div>
      {sub && <div className="mt-0.5 truncate text-[12px] text-muted">{sub}</div>}
    </>
  );
  if (!onSelect) return <div className="min-w-0 px-4 py-3">{body}</div>;
  return (
    <button type="button" onClick={onSelect} aria-pressed={active} title={title}
      className={cn("min-w-0 px-4 py-3 text-left transition-colors duration-150 hover:bg-surface-2 focus-visible:outline-offset-[-2px]",
        active && "bg-accent-soft/60 shadow-[inset_0_-2px_0_var(--accent)]")}>
      {body}
    </button>
  );
}

interface Filters { health?: string; engine?: string; environment?: string }

function useDashboardFilters(): [Filters, (k: keyof Filters, v: string | undefined) => void, () => void] {
  const [params, setParams] = useSearchParams();
  const f: Filters = { health: params.get("health") ?? undefined, engine: params.get("engine") ?? undefined, environment: params.get("env") ?? undefined };
  const set = (k: keyof Filters, v: string | undefined) => {
    const key = k === "environment" ? "env" : k;
    const next = new URLSearchParams(params);
    if (!v || params.get(key) === v) next.delete(key); else next.set(key, v);
    setParams(next, { replace: true });
  };
  const clear = () => setParams(new URLSearchParams(), { replace: true });
  return [f, set, clear];
}

function SourceNote({ d }: { d: Summary }) {
  const sim = d.sources?.simulated ?? 0;
  if (!sim) return null;
  if (d.source === "real") return <span>{sim} simulated {sim === 1 ? "server is" : "servers are"} not included</span>;
  if (d.source === "simulated") return <SimulatedBadge label="Simulated servers only" />;
  return <SimulatedBadge label={`Includes ${sim} simulated ${sim === 1 ? "server" : "servers"}`} />;
}

export function DashboardPage() {
  const { source, counts } = useFleetSource();
  const refreshMs = useRefreshMs();
  // 24-hour fleet trends: polling faster than once a minute adds load but no information
  const q = useQuery({
    queryKey: ["dashboard", source],
    queryFn: () => get<Summary>("/dashboard/summary", { source }),
    refetchInterval: refreshMs ? Math.max(refreshMs, 60_000) : false,
  });
  const d = q.data;
  const { can, canPlatform } = useAuth();
  const navigate = useNavigate();
  const alertSummary = useAlertSummary(can("alerts.view"));
  const alerts = can("alerts.view") && alertSummary.data?.visible ? alertSummary.data : null;
  const [filters, setFilter, clearFilters] = useDashboardFilters();
  const anyFilter = Boolean(filters.health || filters.engine || filters.environment);
  const match = (t: { health: string; engine: string; environment: string | null }) =>
    (!filters.health || t.health === filters.health) && (!filters.engine || t.engine === filters.engine) &&
    (!filters.environment || t.environment === filters.environment);
  const fleet = d ? d.fleet.filter((t) => match(t)) : [];
  const attention = d ? d.attention.filter((t) => match(t)) : [];
  const serversLink = `/fleet/servers?${new URLSearchParams(Object.entries({ health: filters.health, engine: filters.engine }).filter(([, v]) => v) as [string, string][]).toString()}`;
  const series = (k: "connections" | "latency_ms" | "tps") => (d?.trends[k] ?? []).map((p) => p.v);

  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const total = d?.sources ? d.sources.real + d.sources.simulated : counts?.total;
  if (d && total === 0) {
    return (
      <div>
        <PageHeader title="Fleet overview" description="Health, load and open advice across every server you can see." />
        {canPlatform("settings.manage") && <SetupChecklist className="mb-4" />}
        <FleetOnboarding />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Fleet overview"
        description="Health, load and open advice across every server you can see."
        meta={d && <>
          <span>Refreshed {relTime(new Date(q.dataUpdatedAt).toISOString())}</span>
          <SourceNote d={d} />
        </>}
        actions={<>
          <SourceFilter />
          <AutoRefreshPicker />
          <Button size="sm" onClick={() => q.refetch()} disabled={q.isFetching}><RefreshCw className={q.isFetching ? "animate-spin" : ""} /> Refresh</Button>
        </>}
      />

      {canPlatform("settings.manage") && <SetupChecklist className="mb-4" />}
      <Card className="mb-4 overflow-hidden">
        <div className={cn("anim-stagger -mb-px -mr-px grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 [&>*]:border-b [&>*]:border-r [&>*]:border-border 2xl:[&>*]:border-b-0", alerts ? "2xl:grid-cols-10" : "2xl:grid-cols-9")}>
          {d ? (
            <>
              <Kpi label="Servers" value={d.kpis.servers} active={!anyFilter} onSelect={clearFilters} title="Show every server" />
              <Kpi label="Healthy" value={d.kpis.healthy} tone="ok" active={filters.health === "healthy"} onSelect={() => setFilter("health", "healthy")} title="Show only healthy servers" />
              <Kpi label="Warning" value={d.kpis.warning} tone={d.kpis.warning ? "warn" : undefined} active={filters.health === "warning"} onSelect={() => setFilter("health", "warning")} title="Show only servers with warnings" />
              <Kpi label="Critical" value={d.kpis.critical} tone={d.kpis.critical ? "crit" : undefined} active={filters.health === "critical"} onSelect={() => setFilter("health", "critical")} title="Show only critical servers" />
              <Kpi label="Unavailable" value={d.kpis.unavailable} tone={d.kpis.unavailable ? "down" : undefined} active={filters.health === "unavailable"} onSelect={() => setFilter("health", "unavailable")} title="Show only unavailable servers" />
              <Kpi label="Databases" value={compactNum(d.kpis.databases)} sub={bytes(d.kpis.total_storage_bytes)} />
              <Kpi label="Sessions" value={compactNum(d.kpis.active_connections)} trend={d.trends.unavailable ? undefined : series("connections")} />
              <Kpi label="Critical findings" value={d.kpis.critical_findings} sub="critical + high" tone={d.kpis.critical_findings ? "crit" : undefined} />
              <Kpi label="Open advice" value={d.kpis.open_recommendations} sub="recommendations" />
              {alerts && (
                <Kpi label="Active alerts" value={alerts.active} sub={alerts.unacknowledged ? `${alerts.unacknowledged} unacknowledged` : alerts.active ? "all acknowledged" : "nothing firing"}
                  tone={(alerts.by_severity.critical ?? 0) + (alerts.by_severity.high ?? 0) ? "crit" : alerts.active ? "warn" : undefined}
                  onSelect={() => navigate("/alerts")} title="Open the alert list" />
              )}
            </>
          ) : (
            Array.from({ length: 9 }).map((_, i) => <div key={i} className="px-4 py-3"><Skeleton className="mb-2 h-3 w-16" /><Skeleton className="h-6 w-10" /></div>)
          )}
        </div>
      </Card>

      {anyFilter && (
        <div className="anim-fade mb-3 flex flex-wrap items-center gap-2 text-[12.5px]" role="status">
          <span className="text-muted">Filtered by</span>
          {filters.health && <button className="rounded-full border border-border bg-surface px-2 py-0.5 hover:border-border-strong" onClick={() => setFilter("health", undefined)}>Health: {HEALTH_LABEL[filters.health] ?? filters.health} ×</button>}
          {filters.engine && <button className="rounded-full border border-border bg-surface px-2 py-0.5 hover:border-border-strong" onClick={() => setFilter("engine", undefined)}><span className="inline-flex items-center gap-1">Engine: <DatabaseEngineIcon engine={filters.engine} size="xs" withLabel /> ×</span></button>}
          {filters.environment && <button className="rounded-full border border-border bg-surface px-2 py-0.5 hover:border-border-strong" onClick={() => setFilter("environment", undefined)}>Environment: {filters.environment} ×</button>}
          <span className="tabular text-muted">{fleet.length} of {d?.fleet.length ?? 0} servers</span>
          <Button size="sm" variant="ghost" onClick={clearFilters}><FilterX /> Clear</Button>
          <Link to={serversLink} className="text-accent hover:underline">Open in server list</Link>
        </div>
      )}

      <Card className="mb-4">
        <CardHeader title="Fleet health" description={anyFilter ? "Filtered view. Select a tile to open the server." : "One tile per server. Select a tile to open it; select a KPI, engine or environment to filter."}
          actions={<Link to="/fleet/servers" className="inline-flex items-center gap-1 text-[12.5px] font-medium text-accent hover:underline">All servers <ArrowRight className="size-3.5" /></Link>} />
        <CardBody>
          {d ? d.fleet.length ? (fleet.length ? <FleetStrip tiles={fleet} /> : <EmptyState title="No server matches the filter" action={<Button size="sm" onClick={clearFilters}>Clear filter</Button>} />) : (
            <EmptyState title="No servers yet" action={<Button asChild variant="primary"><Link to="/fleet/servers?register=1">Register a server</Link></Button>}>
              Register your first database server to start collecting health and performance data.
            </EmptyState>
          ) : <Skeleton className="h-24 w-full" />}
          {d && d.fleet.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-4 border-t border-border pt-3 text-[12px] text-muted">
              {["healthy", "warning", "critical", "unavailable", "unknown"].map((h) => (
                <button key={h} type="button" onClick={() => setFilter("health", h)} aria-pressed={filters.health === h}
                  className={cn("inline-flex items-center gap-1.5 rounded px-1 hover:text-text", filters.health === h && "font-medium text-text")}>
                  <span className="size-2.5 rounded-[2px]" style={{ background: HEALTH_VAR[h] }} /> {HEALTH_LABEL[h]} <span className="tabular text-text-2">{d.health[h] ?? 0}</span>
                </button>
              ))}
            </div>
          )}
        </CardBody>
      </Card>

      <div className="mb-4 grid gap-4 xl:grid-cols-3">
        {[
          { key: "connections", title: "Connections", desc: "Sum across monitored servers, 24 hours", unit: "count" },
          { key: "latency_ms", title: "Mean statement latency", desc: "Average across servers, 24 hours", unit: "ms" },
          { key: "tps", title: "Transactions per second", desc: "Sum across servers, 24 hours", unit: "1/s" },
        ].map((c) => (
          <Card key={c.key}>
            <CardHeader title={c.title} description={c.desc} />
            <CardBody className="pt-2">
              {!d ? <Skeleton className="h-[170px] w-full" />
                : d.trends.unavailable ? <StorageUnavailable compact className="h-[170px]" />
                : <MetricChart unit={c.unit} height={170} series={[{ key: c.key, label: c.title, points: (d.trends[c.key as "connections" | "latency_ms" | "tps"] ?? []) }]} />}
            </CardBody>
          </Card>
        ))}
      </div>

      <div className="mb-4 grid items-start gap-4 xl:grid-cols-[1.25fr_1fr]">
        <Card>
          <CardHeader title="Servers requiring attention" description="Unavailable, critical and warning servers" />
          {d && attention.length === 0 ? <EmptyState title={anyFilter ? "Nothing needs attention in this filter" : "Every server is healthy"} /> : (
            <ul className="divide-y divide-border">
              {attention.map((s) => (
                <li key={s.id}>
                  <Link to={`/servers/${s.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2">
                    <DatabaseEngineIcon engine={s.engine} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2"><span className="font-medium">{s.name}</span>{s.is_demo && <SimulatedBadge />}</div>
                      <div className="truncate text-[12px] text-muted">{s.reasons.join("; ") || "No collection yet"}</div>
                    </div>
                    <span className="hidden text-[12px] text-muted sm:block">{s.environment}</span>
                    <HealthBadge health={s.health} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>
          <CardHeader title="Top recommendations" description="Highest severity first, with the evidence on the next screen"
            actions={<Link to="/optimization/performance" className="text-[12.5px] font-medium text-accent hover:underline">View all</Link>} />
          {d && d.top_recommendations.length === 0 ? <EmptyState title="No open recommendations" /> : (
            <ul className="divide-y divide-border">
              {(d?.top_recommendations ?? []).map((r) => (
                <li key={r.id}>
                  <Link to={`/optimization/performance?id=${r.id}`} className="flex items-start gap-3 px-4 py-2.5 hover:bg-surface-2">
                    <SeverityBadge severity={r.severity} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium">{r.title}</div>
                      <div className="text-[12px] text-muted">{r.server_name} <span className="font-mono text-2xs">{r.rule_id}</span></div>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <CardHeader title="Engines" description="Select one to filter the fleet" />
          <CardBody>{d && <BarList selected={filters.engine} onSelect={(k) => setFilter("engine", k)} items={d.engines.map((e) => ({ key: e.engine, label: <DatabaseEngineIcon engine={e.engine} withLabel />, value: e.count }))} />}</CardBody>
        </Card>
        <Card>
          <CardHeader title="Environments" description="Select one to filter the fleet" />
          <CardBody>
            <ul className="space-y-1">
              {d?.environments.map((e) => (
                <li key={e.name}>
                  <button type="button" onClick={() => setFilter("environment", e.name)} aria-pressed={filters.environment === e.name}
                    className={cn("w-full rounded-md px-1.5 py-1 text-left transition-colors duration-150 hover:bg-surface-2", filters.environment && filters.environment !== e.name && "opacity-50", filters.environment === e.name && "bg-accent-soft/60")}>
                    <div className="mb-1 flex justify-between text-[13px]"><span>{e.name}</span><span className="tabular text-text-2">{e.servers}</span></div>
                    <StackedHealth health={e.health} total={e.servers} />
                  </button>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Security findings" description="Open, by severity" actions={<Link to="/security/findings" className="text-[12.5px] font-medium text-accent hover:underline">Review</Link>} />
          <CardBody>
            {d && <BarList color="var(--crit)" items={SEVERITIES.map((s) => ({ key: s, label: <SeverityBadge severity={s} />, value: d.findings_by_severity[s] ?? 0 }))} />}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Capacity" description="Largest servers by database size" />
          <CardBody>
            {d && <BarList color="var(--chart-4)" items={d.capacity.slice(0, 6).map((c) => ({ key: c.server_id, label: c.server_name, value: c.bytes, display: bytes(c.bytes), to: `/servers/${c.server_id}?tab=storage` }))} />}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
