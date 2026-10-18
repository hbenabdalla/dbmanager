import { useEffect, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { capUsable, serverCan, useAllServers, useServer } from "@/api/hooks";
import type { ServerDetail } from "@/api/types";
import { PageHeader, type Crumb } from "@/components/page";
import { HealthDot, SimulatedBadge } from "@/components/status";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";

/** Page that operates on one server picked from the fleet (kept in the URL as ?server=). */
export function ServerScopedPage({ title, description, crumbs, perm, cap, children, filter }: {
  title: string; description: string; crumbs: Crumb[]; perm?: string; cap?: string;
  filter?: (s: { implementation: string }) => boolean; children: (s: ServerDetail) => ReactNode;
}) {
  const [params, setParams] = useSearchParams();
  const servers = useAllServers();
  const list = (servers.data?.items ?? []).filter((s) => s.implementation !== "planned" && (!filter || filter(s)));
  const id = params.get("server") ?? undefined;
  useEffect(() => {
    if (!id && list.length) {
      const pick = list.find((s) => s.health_status !== "unavailable") ?? list[0];
      const next = new URLSearchParams(params);
      next.set("server", pick.id);
      setParams(next, { replace: true });
    }
  }, [id, list.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const server = useServer(id);
  const s = server.data;
  return (
    <div>
      <PageHeader title={title} description={description} crumbs={crumbs}
        actions={
          <div className="flex items-center gap-2">
            {s && <HealthDot health={s.health_status} />}
            <Select aria-label="Server" className="w-72" value={id ?? ""} onChange={(e) => { const n = new URLSearchParams(); n.set("server", e.target.value); setParams(n); }}>
              {list.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.engine_label}{x.environment ? `, ${x.environment.name}` : ""})</option>)}
            </Select>
            {s?.is_demo && <SimulatedBadge />}
          </div>
        } />
      {servers.isLoading || (id && server.isLoading) ? <SkeletonRows /> : !list.length ? (
        <Card><EmptyState title="No servers available">Register a server you can monitor, or ask an administrator for access.</EmptyState></Card>
      ) : server.error ? <ErrorState error={server.error} /> : s ? (
        perm && !serverCan(s, perm) ? <Card><EmptyState title="You don't have access to this on the selected server">Your role doesn't include {perm} for {s.name}.</EmptyState></Card>
          : cap && !capUsable(s, cap) ? <Card><EmptyState title="Not available for this server">{s.capabilities[cap]?.note ?? `${s.engine_label} doesn't support this.`}</EmptyState></Card>
            : children(s)
      ) : null}
    </div>
  );
}
