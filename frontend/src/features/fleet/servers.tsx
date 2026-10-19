import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { get } from "@/api/client";
import { useEngines, useEnvironments } from "@/api/hooks";
import type { Page, Server } from "@/api/types";
import { Meter } from "@/components/charts";
import { PageHeader } from "@/components/page";
import { DatabaseEngineIcon, HealthBadge, SimulatedBadge } from "@/components/status";
import { SourceFilter } from "@/components/fleet-source";
import { useFleetSource } from "@/lib/fleet-source";
import { FleetOnboarding } from "@/features/dashboard/onboarding";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Input, Select } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/states";
import { useAuth } from "@/lib/auth";
import { HEALTH_LABEL } from "@/lib/engines";
import { ms, pct, relTime } from "@/lib/format";
import { OnboardingWizard } from "../onboarding/onboarding-wizard";
import { AlertCountBadge, useAlertsByServer } from "../alerts/server-alerts";

export function ServersPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [page, setPage] = useState(1);
  const [registerOpen, setRegisterOpen] = useState(params.get("register") === "1" || params.get("add") === "1");
  useEffect(() => {
    if (params.get("register") === "1" || params.get("add") === "1") setRegisterOpen(true);
  }, [params]);
  const engines = useEngines();
  const envs = useEnvironments();
  const filters = {
    q: params.get("q") ?? undefined,
    engine: params.get("engine") ?? undefined,
    environment_id: params.get("environment_id") ?? undefined,
    health: params.get("health") ?? undefined,
    sort: params.get("sort") ?? "health",
  };
  useEffect(() => {
    const t = setTimeout(() => {
      const next = new URLSearchParams(params);
      if (q) next.set("q", q); else next.delete("q");
      if (next.toString() !== params.toString()) {
        setParams(next, { replace: true });
        setPage(1);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps

  const setFilter = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next, { replace: true });
    setPage(1);
  };
  const { source, counts } = useFleetSource();
  const query = useQuery({
    queryKey: ["servers", "list", source, filters, page],
    queryFn: () => get<Page<Server>>("/servers", { ...filters, source, page, page_size: 50 }),
    refetchInterval: 30_000,
    placeholderData: (p) => p,
  });

  const alertCounts = useAlertsByServer(can("alerts.view"));
  const byServer = alertCounts.data ?? {};
  const columns = useMemo<ColumnDef<Server, unknown>[]>(() => [
    {
      header: "Server", accessorKey: "name", cell: ({ row: { original: s } }) => (
        <div className="min-w-[220px]">
          <div className="flex items-center gap-2">
            <Link to={`/servers/${s.id}`} className="font-medium hover:underline" onClick={(e) => e.stopPropagation()}>{s.name}</Link>
            {s.is_demo && <SimulatedBadge />}
            {byServer[s.id] && <AlertCountBadge count={byServer[s.id].active} severity={byServer[s.id].top_severity} />}
            {s.implementation === "planned" && <Badge tone="neutral">No adapter</Badge>}
          </div>
          <div className="text-[12px] text-muted">{s.host}:{s.port}{s.cluster ? `, ${s.cluster.name}${s.cluster_role ? ` (${s.cluster_role})` : ""}` : ""}</div>
        </div>
      ),
    },
    { header: "Engine", accessorKey: "engine", cell: ({ row: { original: s } }) => <div className="flex items-center gap-2"><DatabaseEngineIcon engine={s.engine} /><span className="text-[12px] text-muted">{s.version?.split(" ")[0] ?? "—"}</span></div> },
    { header: "Environment", accessorFn: (s) => s.environment?.name ?? "", cell: ({ row: { original: s } }) => s.environment?.name ?? <span className="text-muted">Unassigned</span> },
    { header: "Health", accessorKey: "health_status", cell: ({ row: { original: s } }) => <HealthBadge health={s.health_status} reasons={s.health_reasons} /> },
    {
      header: "Connections", accessorFn: (s) => s.key_metrics["connections.utilization"], meta: { align: "right" },
      cell: ({ row: { original: s } }) => {
        const u = s.key_metrics["connections.utilization"];
        return u === null || u === undefined ? <span className="text-muted">—</span> : (
          <div className="ml-auto w-24"><div className="mb-1 text-right text-[12px] tabular">{pct(u, 0)}</div><Meter value={u} /></div>
        );
      },
    },
    { header: "Latency", accessorFn: (s) => s.key_metrics["query.mean_latency_ms"], meta: { align: "right" }, cell: ({ getValue }) => ms(getValue() as number | null) },
    {
      header: "Advice", accessorFn: (s) => (s.open_findings ?? 0) + (s.open_recommendations ?? 0), meta: { align: "right" },
      cell: ({ row: { original: s } }) => (
        <span className="text-[12px] text-muted tabular">{s.open_recommendations ?? 0} perf, {s.open_findings ?? 0} security</span>
      ),
    },
    { header: "Collected", accessorKey: "last_collected_at", cell: ({ row: { original: s } }) => <span className="text-[12px] text-muted">{relTime(s.last_collected_at)}</span> },
  ], [byServer]);

  return (
    <div>
      <PageHeader title="All servers" description="Every registered server, instance and managed database you have access to."
        crumbs={[{ label: "Fleet" }, { label: "All servers" }]}
        actions={can("server.register") && <Button variant="primary" onClick={() => setRegisterOpen(true)}><Plus /> Add server</Button>} />
      {counts?.total === 0 && !params.toString() ? <FleetOnboarding /> : (
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-muted" />
            <Input aria-label="Search servers" placeholder="Name, host, owner" value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" />
          </div>
          {filters.engine && <DatabaseEngineIcon engine={filters.engine} size="md" />}
          <Select aria-label="Engine" className="w-40" value={filters.engine ?? ""} onChange={(e) => setFilter("engine", e.target.value)}>
            <option value="">All engines</option>{engines.data?.map((e) => <option key={e.engine} value={e.engine}>{e.display_name}</option>)}
          </Select>
          <Select aria-label="Environment" className="w-40" value={filters.environment_id ?? ""} onChange={(e) => setFilter("environment_id", e.target.value)}>
            <option value="">All environments</option>{envs.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
          </Select>
          <Select aria-label="Health" className="w-36" value={filters.health ?? ""} onChange={(e) => setFilter("health", e.target.value)}>
            <option value="">Any health</option>{Object.entries(HEALTH_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
          <SourceFilter />
          <Select aria-label="Sort" className="w-40" value={filters.sort} onChange={(e) => setFilter("sort", e.target.value)}>
            <option value="health">Sort by health</option><option value="name">Sort by name</option><option value="engine">Sort by engine</option>
            <option value="environment">Sort by environment</option><option value="-last_collected_at">Recently collected</option>
          </Select>
          <span className="ml-auto text-[12.5px] text-muted tabular">{query.data?.total ?? 0} {query.data?.total === 1 ? "server" : "servers"}</span>
        </div>
        <DataTable columns={columns} data={query.data?.items} loading={query.isLoading} error={query.error} onRetry={() => query.refetch()}
          onRowClick={(s) => navigate(`/servers/${s.id}`)}
          manual={query.data ? { page, pageSize: 50, total: query.data.total, onPage: setPage } : undefined}
          empty={<EmptyState title="No servers match" action={params.toString() ? <Button onClick={() => { setQ(""); setParams({}); }}>Clear filters</Button> : can("server.register") ? <Button variant="primary" onClick={() => setRegisterOpen(true)}><Plus /> Register server</Button> : undefined}>
            {params.toString() ? "Try removing a filter." : "Register a server to start monitoring it."}
          </EmptyState>} />
      </Card>
      )}
      <OnboardingWizard open={registerOpen} onOpenChange={setRegisterOpen} />
    </div>
  );
}
