import { RefreshCw } from "lucide-react";
import { useParams, useSearchParams } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { ApiError, post } from "@/api/client";
import { capUsable, serverCan, useServer } from "@/api/hooks";
import type { ServerDetail } from "@/api/types";
import { PageHeader } from "@/components/page";
import { EngineMark, HealthBadge, SimulatedBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip } from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth";
import { LEVEL_LABEL } from "@/lib/capabilities";
import { relTime } from "@/lib/format";
import { AuditTable } from "../shared/audit-table";
import { FindingsList } from "../shared/findings";
import { QueryStatsTable } from "../shared/query-stats";
import { UsersTab, PrivilegesTab } from "./tabs/access";
import { LocksTab, SessionsTab } from "./tabs/activity";
import { DatabasesTab } from "./tabs/databases";
import { ConfigurationTab, LogsTab, ReplicationTab, StorageTab } from "./tabs/infra";
import { ObjectBrowser } from "./tabs/objects";
import { OverviewTab } from "./tabs/overview";
import { PerformanceTab } from "./tabs/performance";
import { SettingsTab } from "./tabs/settings";
import { AdministrationTab } from "./tabs/administration";
import { ServerVitals } from "./vitals";

interface TabDef { key: string; label: string; perm?: string; cap?: string; platformPerm?: string; render: (s: ServerDetail, go: (t: string, db?: string) => void, db?: string) => React.ReactNode }

const TABS: TabDef[] = [
  { key: "overview", label: "Overview", render: (s) => <OverviewTab server={s} /> },
  { key: "performance", label: "Performance", perm: "performance.view", cap: "performance_metrics", render: (s) => <PerformanceTab server={s} /> },
  { key: "databases", label: "Databases", perm: "database.view", cap: "databases", render: (s, go) => <DatabasesTab server={s} onOpen={(db) => go("schemas", db)} /> },
  { key: "schemas", label: "Schemas", perm: "schema.view", cap: "schemas", render: (s, _g, db) => <ObjectBrowser server={s} initialDatabase={db} /> },
  { key: "sessions", label: "Sessions", perm: "session.view", cap: "sessions", render: (s) => <SessionsTab server={s} /> },
  { key: "queries", label: "Queries", perm: "query.view", cap: "query_statistics", render: (s) => <QueryStatsTable serverId={s.id} /> },
  { key: "locks", label: "Locks", perm: "session.view", cap: "locks", render: (s) => <LocksTab server={s} /> },
  { key: "replication", label: "Replication", perm: "performance.view", cap: "replication", render: (s) => <ReplicationTab server={s} /> },
  { key: "storage", label: "Storage", perm: "performance.view", cap: "storage", render: (s) => <StorageTab server={s} /> },
  { key: "users", label: "Users", perm: "user.view", cap: "users", render: (s) => <UsersTab server={s} /> },
  { key: "privileges", label: "Privileges", perm: "privilege.view", cap: "privileges", render: (s) => <PrivilegesTab server={s} /> },
  { key: "recommendations", label: "Recommendations", perm: "performance.view", render: (s) => <FindingsList kind="recommendations" serverId={s.id} /> },
  { key: "security", label: "Security", perm: "security.view", render: (s) => <FindingsList kind="security" serverId={s.id} /> },
  { key: "configuration", label: "Configuration", perm: "performance.view", cap: "configuration", render: (s) => <ConfigurationTab server={s} /> },
  { key: "logs", label: "Logs", perm: "logs.view", cap: "logs", render: (s) => <LogsTab server={s} /> },
  { key: "administration", label: "Administration", cap: "administration", render: (s) => <AdministrationTab server={s} /> },
  { key: "audit", label: "Audit", platformPerm: "audit.view", render: (s) => <AuditTable serverId={s.id} serverName={s.name} /> },
  { key: "settings", label: "Settings", render: (s) => <SettingsTab server={s} /> },
];

export function ServerPage() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const { canPlatform, can } = useAuth();
  const q = useServer(id);
  const collect = useMutation({ mutationFn: () => post(`/servers/${id}/collect`), onSuccess: () => toast.success("Collection queued"), onError: (e: ApiError) => toast.error(e.message) });

  if (q.isLoading) return <SkeletonRows rows={10} />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const s = q.data;
  const planned = s.implementation === "planned";
  const tab = params.get("tab") ?? "overview";
  const go = (t: string, db?: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", t);
    if (db) next.set("db", db); else next.delete("db");
    setParams(next, { replace: false });
  };

  const state = (t: TabDef): { enabled: boolean; reason?: string } => {
    if (t.key === "settings") return { enabled: ["server.modify", "credentials.manage", "server.delete", "collectors.manage"].some((p) => serverCan(s, p)) };
    if (t.platformPerm && !canPlatform(t.platformPerm)) return { enabled: false, reason: `Requires ${t.platformPerm}` };
    if (t.perm && !serverCan(s, t.perm) && !can(t.perm)) return { enabled: false, reason: `Your role doesn't include ${t.perm} for this server` };
    if (planned && t.key !== "overview" && t.key !== "audit") return { enabled: false, reason: `No ${s.engine_label} adapter is available` };
    if (t.cap && !capUsable(s, t.cap)) {
      const c = s.capabilities[t.cap];
      return { enabled: false, reason: c ? `${LEVEL_LABEL[c.level] ?? c.level}${c.note ? `: ${c.note}` : ""}` : "Not supported for this server" };
    }
    return { enabled: true };
  };
  const active = TABS.find((t) => t.key === tab && state(t).enabled) ?? TABS[0];

  return (
    <div>
      <PageHeader
        crumbs={[{ label: "Fleet", to: "/fleet/servers" }, { label: s.environment?.name ?? "Unassigned", to: s.environment ? `/fleet/servers?environment_id=${s.environment.id}` : undefined }, { label: s.name }]}
        title={<span className="flex flex-wrap items-center gap-2.5">{s.name}<HealthBadge health={s.health_status} reasons={s.health_reasons} />{s.is_demo && <SimulatedBadge />}{planned && <Badge tone="neutral">No adapter</Badge>}{!s.is_demo && !planned && (s.management_mode === "administration" ? <Badge tone="warn">Administration enabled</Badge> : <Badge tone="outline">Monitoring only</Badge>)}</span>}
        description={s.description ?? undefined}
        meta={<>
          <EngineMark engine={s.engine} withLabel />
          <span>{s.version ?? "version unknown"}</span>
          <span>{s.environment?.name ?? "No environment"}</span>
          {s.cluster && <span>{s.cluster.name}{s.cluster_role ? ` (${s.cluster_role})` : ""}</span>}
          <span>Last collection {relTime(s.last_collected_at)}</span>
          {s.circuit_open_until && <Tooltip content={s.last_error ?? ""}><span className="text-warn">Collection paused after repeated failures until {new Date(s.circuit_open_until).toLocaleTimeString()}</span></Tooltip>}
        </>}
        actions={serverCan(s, "collectors.manage") && !planned && <Button size="sm" onClick={() => collect.mutate()} disabled={collect.isPending}><RefreshCw /> Collect now</Button>}
      />
      {s.is_demo && (
        <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-dashed border-sim/45 bg-sim-soft px-3 py-2 text-[13px] text-text-2">
          <SimulatedBadge />
          <span>
            Simulated server{s.simulation ? <> from the <span className="font-medium">{s.simulation.profile}</span> profile (seed {s.simulation.seed}). {s.simulation.story}</> : null}.
            {" "}Every value is generated; DB Manager never connects to it and SQL is never executed.
          </span>
        </div>
      )}
      {s.health_status === "unavailable" && s.last_error && (
        <div className="mb-4 rounded-md border border-down/30 bg-down-soft px-3 py-2 text-[13px] text-text-2"><span className="font-medium">Unreachable:</span> {s.last_error}</div>
      )}
      {!planned && capUsable(s, "performance_metrics") && (serverCan(s, "performance.view") || can("performance.view")) && <ServerVitals server={s} />}
      <Tabs value={active.key} onValueChange={(v) => go(v)}>
        <TabsList className="mb-4">
          {TABS.map((t) => {
            const st = state(t);
            const trigger = <TabsTrigger key={t.key} value={t.key} disabled={!st.enabled}>{t.label}</TabsTrigger>;
            return st.enabled || !st.reason ? trigger : <Tooltip key={t.key} content={st.reason}><span>{trigger}</span></Tooltip>;
          })}
        </TabsList>
        <TabsContent value={active.key}>{active.render(s, go, params.get("db") ?? undefined)}</TabsContent>
      </Tabs>
    </div>
  );
}
