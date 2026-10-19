import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus, Search } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, get, post } from "@/api/client";
import { useAllServers, useClusters, useEnvironments } from "@/api/hooks";
import type { Page, Server } from "@/api/types";
import { PageHeader } from "@/components/page";
import { DatabaseEngineIcon, HealthBadge, HealthDot, SimulatedBadge } from "@/components/status";
import { useSourceParam } from "@/lib/fleet-source";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { PlacementActions } from "./placement";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { useAuth } from "@/lib/auth";
import { HEALTH_LABEL } from "@/lib/engines";
import { bytes, relTime } from "@/lib/format";

function groupBy<T>(items: T[], key: (t: T) => string | undefined) {
  const m = new Map<string, T[]>();
  for (const i of items) {
    const k = key(i);
    if (!k) continue;
    m.set(k, [...(m.get(k) ?? []), i]);
  }
  return m;
}

function CreateDialog({ kind, open, onOpenChange }: { kind: "clusters" | "environments"; open: boolean; onOpenChange: (o: boolean) => void }) {
  const [name, setName] = useState("");
  const [extra, setExtra] = useState(kind === "clusters" ? "primary-replica" : "development");
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => post(`/${kind}`, kind === "clusters" ? { name, kind: extra } : { name, tier: extra }),
    onSuccess: () => { toast.success(`${kind === "clusters" ? "Cluster" : "Environment"} created`); qc.invalidateQueries({ queryKey: [kind] }); onOpenChange(false); setName(""); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={kind === "clusters" ? "New cluster" : "New environment"}
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!name || m.isPending} onClick={() => m.mutate()}>Create</Button></>}>
      <div className="space-y-3">
        <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} autoFocus /></Field>
        {kind === "clusters" ? (
          <Field label="Topology">
            <Select value={extra} onChange={(e) => setExtra(e.target.value)}>
              {["primary-replica", "rac", "availability-group", "galera", "hadr", "pacemaker"].map((k) => <option key={k}>{k}</option>)}
            </Select>
          </Field>
        ) : (
          <Field label="Tier" hint="Production tier servers require typed confirmation for every change">
            <Select value={extra} onChange={(e) => setExtra(e.target.value)}>
              {["production", "dr", "uat", "development", "test"].map((k) => <option key={k}>{k}</option>)}
            </Select>
          </Field>
        )}
      </div>
    </Dialog>
  );
}

export function ClustersPage() {
  const clusters = useClusters();
  const servers = useAllServers();
  const { canPlatform } = useAuth();
  const [open, setOpen] = useState(false);
  const members = groupBy(servers.data?.items ?? [], (s) => s.cluster?.id);
  return (
    <div>
      <PageHeader title="Clusters" description="Primary/replica sets, RAC, availability groups and HADR pairs."
        crumbs={[{ label: "Fleet" }, { label: "Clusters" }]}
        actions={canPlatform("inventory.manage") && <Button variant="primary" onClick={() => setOpen(true)}><Plus /> New cluster</Button>} />
      {clusters.isLoading ? <SkeletonRows /> : !clusters.data?.length ? <Card><EmptyState title="No clusters defined" /></Card> : (
        <div className="grid gap-4 lg:grid-cols-2">
          {clusters.data.map((c) => {
            const list = members.get(c.id) ?? [];
            return (
              <Card key={c.id}>
                <CardHeader title={c.name} description={`${c.kind}, ${list.length} member${list.length === 1 ? "" : "s"}`}
                  actions={canPlatform("inventory.manage") && <PlacementActions kind="clusters" item={c} />} />
                <ul className="divide-y divide-border">
                  {list.map((s) => (
                    <li key={s.id}>
                      <Link to={`/servers/${s.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2">
                        <DatabaseEngineIcon engine={s.engine} />
                        <span className="font-medium">{s.name}</span>
                        {s.cluster_role && <Badge tone="outline">{s.cluster_role}</Badge>}
                        <span className="ml-auto text-[12px] text-muted">{s.environment?.name}</span>
                        <HealthBadge health={s.health_status} reasons={s.health_reasons} />
                      </Link>
                    </li>
                  ))}
                  {!list.length && <li className="px-4 py-3 text-[13px] text-muted">No member servers you can see</li>}
                </ul>
              </Card>
            );
          })}
        </div>
      )}
      <CreateDialog kind="clusters" open={open} onOpenChange={setOpen} />
    </div>
  );
}

export function EnvironmentsPage() {
  const envs = useEnvironments();
  const servers = useAllServers();
  const { canPlatform } = useAuth();
  const [open, setOpen] = useState(false);
  const byEnv = groupBy(servers.data?.items ?? [], (s) => s.environment?.id);
  return (
    <div>
      <PageHeader title="Environments" description="Lifecycle tiers. Production servers get stricter confirmation rules."
        crumbs={[{ label: "Fleet" }, { label: "Environments" }]}
        actions={canPlatform("inventory.manage") && <Button variant="primary" onClick={() => setOpen(true)}><Plus /> New environment</Button>} />
      {envs.isLoading ? <SkeletonRows /> : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {envs.data?.map((e) => {
            const list = byEnv.get(e.id) ?? [];
            const counts = list.reduce<Record<string, number>>((a, s) => ({ ...a, [s.health_status]: (a[s.health_status] ?? 0) + 1 }), {});
            return (
              <Card key={e.id}>
                <CardHeader title={e.name} description={<span className="capitalize">{e.tier} tier</span>}
                  actions={<span className="flex items-center gap-2">
                    <Link className="text-[12.5px] font-medium text-accent hover:underline" to={`/fleet/servers?environment_id=${e.id}`}>{list.length} servers</Link>
                    {canPlatform("inventory.manage") && <PlacementActions kind="environments" item={e} />}
                  </span>} />
                <CardBody className="space-y-2">
                  {Object.keys(HEALTH_LABEL).map((h) => counts[h] ? (
                    <div key={h} className="flex items-center gap-2 text-[13px]"><HealthDot health={h} /> {HEALTH_LABEL[h]}<span className="ml-auto tabular text-text-2">{counts[h]}</span></div>
                  ) : null)}
                  {!list.length && <p className="text-[13px] text-muted">No servers you can see in this environment</p>}
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}
      <CreateDialog kind="environments" open={open} onOpenChange={setOpen} />
    </div>
  );
}

interface DbRow { name: string; size_bytes: number | null; owner: string | null; server_id: string; server_name: string; engine: string; environment: string | null; is_demo: boolean; discovered_at: string }

export function FleetDatabasesPage() {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const navigate = useNavigate();
  const source = useSourceParam();
  const data = useQuery({ queryKey: ["fleet-databases", source, q, page], queryFn: () => get<Page<DbRow>>("/databases", { q, source, page, page_size: 50 }), placeholderData: (p) => p });
  const columns: ColumnDef<DbRow, unknown>[] = [
    { header: "Database", accessorKey: "name", cell: ({ row: { original: d } }) => <span className="font-medium">{d.name}</span> },
    { header: "Server", accessorKey: "server_name", cell: ({ row: { original: d } }) => <span className="flex items-center gap-2"><DatabaseEngineIcon engine={d.engine} />{d.server_name}{d.is_demo && <SimulatedBadge />}</span> },
    { header: "Environment", accessorKey: "environment" },
    { header: "Owner", accessorKey: "owner" },
    { header: "Size", accessorKey: "size_bytes", meta: { align: "right" }, cell: ({ getValue }) => bytes(getValue() as number) },
    { header: "Discovered", accessorKey: "discovered_at", cell: ({ getValue }) => <span className="text-[12px] text-muted">{relTime(getValue() as string)}</span> },
  ];
  return (
    <div>
      <PageHeader title="Databases" description="Every user database found by discovery across the fleet. Discovery runs hourly and on registration."
        crumbs={[{ label: "Fleet" }, { label: "Databases" }]} />
      <Card>
        <div className="flex items-center gap-2 border-b border-border p-3">
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-muted" />
            <Input aria-label="Search databases" className="pl-8" placeholder="Database or server name" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
          </div>
          <span className="ml-auto text-[12.5px] text-muted tabular">{data.data?.total ?? 0} databases</span>
        </div>
        <DataTable columns={columns} data={data.data?.items} loading={data.isLoading} error={data.error}
          onRowClick={(d) => navigate(`/servers/${d.server_id}?tab=databases`)}
          manual={data.data ? { page, pageSize: 50, total: data.data.total, onPage: setPage } : undefined} />
      </Card>
    </div>
  );
}

export type { Server };
