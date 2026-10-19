import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, get, put } from "@/api/client";
import type { CredentialInfo, Job, Page } from "@/api/types";
import { KeyValue, PageHeader, Stat } from "@/components/page";
import { DatabaseEngineIcon, SimulatedBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Field, Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { SkeletonRows } from "@/components/ui/states";
import { dateTime, ms, relTime } from "@/lib/format";
import { PROVIDER_LABEL, useSecretProviders, vaultStatus, type SecretProvider } from "@/features/shared/secret-source";

interface CredRow { server_id: string; server_name: string; engine: string; is_demo: boolean; environment: string | null; tls_mode: string; connection_status: string; monitoring: CredentialInfo | null; admin: CredentialInfo | null }

/** Credential sources of this installation (GET /platform/secret-providers). */
export function SecretProvidersCard() {
  const q = useSecretProviders();
  if (!q.data) return null;
  const d = q.data;
  const v = vaultStatus(d.vault);
  const rows: [SecretProvider, React.ReactNode][] = [
    ["local", <Badge tone="ok">available</Badge>],
    ["env", <Badge tone="ok">available</Badge>],
    ["file", <span><Badge tone="ok">available</Badge> <span className="text-[12px] text-muted">allowed directories: {d.file.allowed_dirs?.join(", ") || "none"}</span></span>],
    ["vault", <span><Badge tone={d.vault.available ? (v.tone === "ok" ? "ok" : "crit") : "neutral"}>{d.vault.available ? "configured" : "not configured"}</Badge> <span className="text-[12px] text-muted">{v.text}</span></span>],
  ];
  return (
    <Card className="mb-4">
      <CardHeader title="Secret sources" description="Where server passwords can come from. Only a reference is stored for environment variables, mounted files and Vault." />
      <CardBody>
        <ul className="space-y-1.5 text-[13px]" aria-label="Secret sources">
          {rows.map(([k, state]) => <li key={k} className="flex flex-wrap items-center gap-2"><span className="w-56 font-medium">{PROVIDER_LABEL[k]}</span>{state}</li>)}
        </ul>
      </CardBody>
    </Card>
  );
}

export function CredentialsPage() {
  const q = useQuery({ queryKey: ["credentials"], queryFn: () => get<CredRow[]>("/credentials") });
  const navigate = useNavigate();
  const cell = (c: CredentialInfo | null) => c ? <span><span className="font-medium">{c.username}</span> <span className="text-[12px] text-muted">{c.provider === "local" ? "encrypted" : c.provider === "vault" ? `vault ${c.secret_ref ?? ""}` : c.provider}, rotated {relTime(c.rotated_at)}</span></span> : <span className="text-muted">not set</span>;
  const columns: ColumnDef<CredRow, unknown>[] = [
    { header: "Server", accessorKey: "server_name", cell: ({ row: { original: r } }) => <span className="flex items-center gap-2"><DatabaseEngineIcon engine={r.engine} /><span className="font-medium">{r.server_name}</span>{r.is_demo && <SimulatedBadge />}</span> },
    { header: "Environment", accessorKey: "environment" },
    { header: "Monitoring credential", accessorFn: (r) => r.monitoring?.username ?? "", cell: ({ row: { original: r } }) => cell(r.monitoring) },
    { header: "Admin credential", accessorFn: (r) => r.admin?.username ?? "", cell: ({ row: { original: r } }) => cell(r.admin) },
    { header: "TLS", accessorKey: "tls_mode", cell: ({ getValue }) => { const v = getValue() as string; return <Badge tone={v.startsWith("verify") ? "ok" : v === "require" ? "info" : "warn"}>{v}</Badge>; } },
    { header: "Last connection", accessorKey: "connection_status", cell: ({ getValue }) => { const v = getValue() as string; return <Badge tone={v === "ok" ? "ok" : v === "failed" ? "crit" : "neutral"}>{v}</Badge>; } },
  ];
  return (
    <div>
      <PageHeader title="Credentials" description="Which servers have monitoring and administration credentials. Secrets are AES-256-GCM encrypted (or read from environment variables, mounted files or HashiCorp Vault) and never returned by the API."
        crumbs={[{ label: "Platform" }, { label: "Credentials" }]} />
      <SecretProvidersCard />
      <Card><DataTable columns={columns} data={q.data} loading={q.isLoading} error={q.error} dense onRowClick={(r) => navigate(`/servers/${r.server_id}?tab=settings`)} /></Card>
    </div>
  );
}

interface Status { process_role: string; worker_running: boolean; is_leader: boolean; started_at: string | null; last_scheduler_tick: string | null; concurrency: number; pending: number; last_hour: Record<string, number>; by_kind: { kind: string; status: string; count: number; avg_ms: number }[]; open_circuits: { server_id: string; server_name: string; until: string; failures: number; error: string | null }[] }

export function CollectorsPage() {
  const s = useQuery({ queryKey: ["collectors"], queryFn: () => get<Status>("/collectors/status"), refetchInterval: 10_000 });
  const [page, setPage] = useState(1);
  const jobs = useQuery({ queryKey: ["jobs", page], queryFn: () => get<Page<Job>>("/collectors/jobs", { page, page_size: 50 }), refetchInterval: 10_000, placeholderData: (p) => p });
  const d = s.data;
  const kinds = [...new Set((d?.by_kind ?? []).map((k) => k.kind))];
  return (
    <div>
      <PageHeader title="Collectors" description="Background scheduling and collection. One process holds the scheduler lock; any number of workers claim jobs." crumbs={[{ label: "Platform" }, { label: "Collectors" }]} />
      {!d ? <SkeletonRows /> : (
        <>
          <Card className="mb-4 grid grid-cols-2 divide-x divide-border sm:grid-cols-5">
            <Stat label="This process" value={d.process_role} sub={d.worker_running ? (d.is_leader ? "worker, scheduler leader" : "worker") : "API only"} />
            <Stat label="Pending jobs" value={d.pending} />
            <Stat label="Done (1h)" value={d.last_hour.done ?? 0} tone="ok" />
            <Stat label="Failed (1h)" value={d.last_hour.failed ?? 0} tone={d.last_hour.failed ? "crit" : undefined} />
            <Stat label="Last scheduler tick" value={<span className="text-[15px]">{relTime(d.last_scheduler_tick)}</span>} sub={`concurrency ${d.concurrency}`} />
          </Card>
          <div className="mb-4 grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader title="Jobs by kind (last hour)" />
              <table className="w-full text-[13px]"><thead><tr className="border-b border-border bg-surface-2 text-left text-[12px] text-muted"><th className="px-3 py-2 font-medium">Kind</th><th className="px-3 text-right font-medium">Done</th><th className="px-3 text-right font-medium">Failed</th><th className="px-3 text-right font-medium">Skipped</th><th className="px-3 text-right font-medium">Avg duration</th></tr></thead>
                <tbody>{kinds.map((k) => { const rows = d.by_kind.filter((x) => x.kind === k); const g = (st: string) => rows.find((x) => x.status === st)?.count ?? 0; const avg = rows.find((x) => x.status === "done")?.avg_ms;
                  return <tr key={k} className="border-b border-border/60"><td className="px-3 py-2 font-mono text-[12px]">{k}</td><td className="px-3 text-right tabular">{g("done")}</td><td className="px-3 text-right tabular text-crit">{g("failed") || ""}</td><td className="px-3 text-right tabular">{g("skipped") || ""}</td><td className="px-3 text-right tabular">{ms(avg)}</td></tr>; })}</tbody></table>
            </Card>
            <Card>
              <CardHeader title="Paused servers" description="Collection backs off after repeated connection failures" />
              <CardBody>
                {d.open_circuits.length === 0 ? <p className="text-[13px] text-muted">No servers are paused.</p> : d.open_circuits.map((c) => (
                  <div key={c.server_id} className="mb-2 text-[13px]"><span className="font-medium">{c.server_name}</span> <span className="text-muted">{c.failures} failures, retry {relTime(c.until)}</span><div className="text-[12px] text-crit">{c.error}</div></div>
                ))}
              </CardBody>
            </Card>
          </div>
        </>
      )}
      <Card>
        <CardHeader title="Recent jobs" />
        <DataTable data={jobs.data?.items} loading={jobs.isLoading} dense manual={jobs.data ? { page, pageSize: 50, total: jobs.data.total, onPage: setPage } : undefined}
          columns={[
            { header: "Created", accessorKey: "created_at", cell: ({ getValue }) => dateTime(getValue() as string) },
            { header: "Server", accessorKey: "server_name", cell: ({ getValue }) => (getValue() as string) ?? <span className="text-muted">global</span> },
            { header: "Kind", accessorKey: "kind", cell: ({ getValue }) => <span className="font-mono text-[12px]">{getValue() as string}</span> },
            { header: "Status", accessorKey: "status", cell: ({ getValue }) => { const v = getValue() as string; return <Badge tone={v === "done" ? "ok" : v === "failed" ? "crit" : v === "running" ? "info" : "neutral"}>{v}</Badge>; } },
            { header: "Duration", accessorKey: "duration_ms", meta: { align: "right" }, cell: ({ getValue }) => ms(getValue() as number) },
            { header: "Worker", accessorKey: "worker", cell: ({ getValue }) => <span className="font-mono text-2xs text-muted">{getValue() as string}</span> },
            { header: "Error", accessorKey: "error", cell: ({ getValue }) => <span className="text-[12px] text-crit">{getValue() as string}</span> },
          ] as ColumnDef<Job, unknown>[]} />
      </Card>
    </div>
  );
}

interface SettingsView { editable: Record<string, unknown>; descriptions?: Record<string, string>; readonly: Record<string, unknown> }

export function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => get<SettingsView>("/settings") });
  const [vals, setVals] = useState<Record<string, unknown>>({});
  useEffect(() => { if (q.data) setVals(q.data.editable); }, [q.data]);
  const save = useMutation({
    mutationFn: () => put("/settings", vals),
    onSuccess: () => { toast.success("Settings saved"); qc.invalidateQueries({ queryKey: ["settings"] }); qc.invalidateQueries({ queryKey: ["settings-public"] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  if (!q.data) return <SkeletonRows />;
  return (
    <div>
      <PageHeader title="Settings" description="Runtime settings stored in the application database. Deployment settings come from environment variables and are shown read only."
        crumbs={[{ label: "Platform" }, { label: "Settings" }]} actions={<Button variant="primary" onClick={() => save.mutate()} disabled={save.isPending}>Save settings</Button>} />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Platform behaviour" />
          <CardBody className="space-y-4">
            {Object.entries(vals).map(([k, v]) => (
              <div key={k}>
                {typeof v === "boolean" ? (
                  <label className="flex items-start gap-3 text-[13px]"><Switch checked={v} onCheckedChange={(x) => setVals({ ...vals, [k]: x })} label={k} disabled={k === "ai_insights.enabled"} />
                    <span><span className="font-mono text-[12px]">{k}</span><span className="block text-[12px] text-muted">{q.data.descriptions?.[k]}</span></span></label>
                ) : (
                  <Field label={k} hint={q.data.descriptions?.[k]}>
                    <Input value={String(v ?? "")} onChange={(e) => setVals({ ...vals, [k]: typeof v === "number" ? Number(e.target.value) : e.target.value })} />
                  </Field>
                )}
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Deployment (environment variables)" />
          <CardBody><KeyValue items={Object.entries(q.data.readonly).map(([k, v]) => [k, String(v)])} /></CardBody>
        </Card>
      </div>
    </div>
  );
}
