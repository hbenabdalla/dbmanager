import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Archive, CheckCircle2, Download, Eye, HardDrive, Plus, RotateCcw, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { del, download, get, post } from "@/api/client";
import { ConfirmDialog } from "@/components/confirm";
import { CodeBlock, KeyValue, PageHeader, Stat } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Field, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/lib/auth";
import { bytes, dateTime, relTime } from "@/lib/format";
import { type Backup, type Capabilities, errMsg, type Job, type Overview } from "./api";
import { PoliciesTab } from "./policies";
import { JobProgress, KeyInput, StatusBadge, useJob } from "./shared";
import { TargetsTab } from "./targets";

const TABS = [
  ["overview", "Overview"], ["history", "Backup history"], ["policies", "Backup policies"], ["targets", "Storage targets"], ["engines", "Engine support"],
] as const;

export function BackupRestorePage() {
  const [params, setParams] = useSearchParams();
  const { can } = useAuth();
  const tab = TABS.find(([k]) => k === params.get("tab"))?.[0] ?? "overview";
  return (
    <div>
      <PageHeader title="Backup & Restore" crumbs={[{ label: "Platform" }, { label: "Backup & Restore" }]}
        description="Back up DB Manager itself and the managed databases, verify the stored copies and restore them. A backup is only reported successful after its stored copy was read back and checked."
        actions={can("backup.create") && <Button variant="primary" size="sm" asChild><Link to="/platform/backups/new"><Plus /> Create backup</Link></Button>} />
      <Tabs value={tab} onValueChange={(v) => setParams((p) => { const n = new URLSearchParams(p); n.set("tab", v); return n; })}>
        <TabsList className="mb-4">{TABS.map(([k, l]) => <TabsTrigger key={k} value={k}>{l}</TabsTrigger>)}</TabsList>
        <TabsContent value="overview"><OverviewTab /></TabsContent>
        <TabsContent value="history"><HistoryTab /></TabsContent>
        <TabsContent value="policies"><PoliciesTab /></TabsContent>
        <TabsContent value="targets"><TargetsTab /></TabsContent>
        <TabsContent value="engines"><EnginesTab /></TabsContent>
      </Tabs>
    </div>
  );
}

// ---- overview ------------------------------------------------------------------------------------
function OverviewTab() {
  const q = useQuery({ queryKey: ["backup-overview"], queryFn: () => get<Overview>("/backup/overview"), refetchInterval: 15_000 });
  if (q.isLoading) return <SkeletonRows rows={6} />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const o = q.data;
  const ok = o.last_30_days.SUCCESS ?? 0;
  const bad = (o.last_30_days.FAILED ?? 0) + (o.last_30_days.CORRUPTED ?? 0);
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Successful (30 days)" value={ok} tone={ok ? "ok" : undefined} />
        <Stat label="Failed or corrupted (30 days)" value={bad} tone={bad ? "crit" : undefined} />
        <Stat label="Last platform backup" value={o.last_platform_backup_at ? relTime(o.last_platform_backup_at) : "never"}
          tone={o.last_platform_backup_at ? undefined : "warn"} sub={o.platform_protection.protected ? "includes server registrations" : "server registrations not protected"} />
        <Stat label="Stored" value={bytes(o.stored_bytes)} />
      </div>
      <Card>
        <CardHeader title="Asset protection" description={`${o.servers} registered (non-simulated) servers you can see`} />
        <CardBody className="flex flex-wrap gap-3 text-[13px]">
          <Badge tone="ok">{o.coverage.protected} protected</Badge>
          <Badge tone="warn">{o.coverage.at_risk} at risk (no recent backup)</Badge>
          <Badge tone="crit">{o.coverage.not_protected} not protected</Badge>
        </CardBody>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Running jobs" />
          <CardBody className="space-y-4">
            {o.running_jobs.length ? o.running_jobs.map((j) => <JobProgress key={j.id} job={j} />) : <p className="text-[13px] text-muted">Nothing is running.</p>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Next scheduled runs" />
          <CardBody>
            {o.next_runs.length ? (
              <ul className="space-y-1 text-[13px]">
                {o.next_runs.map((r) => <li key={r.policy_id} className="flex gap-2"><span className="flex-1">{r.name}</span><span title={dateTime(r.next_run_at)}>{relTime(r.next_run_at)}</span>{r.last_status && <StatusBadge status={r.last_status} />}</li>)}
              </ul>
            ) : <p className="text-[13px] text-muted">No scheduled policies.</p>}
          </CardBody>
        </Card>
      </div>
      <Card>
        <CardHeader title="Recent failures (7 days)" />
        <CardBody>
          {o.recent_failures.length ? (
            <ul className="space-y-1.5 text-[13px]">
              {o.recent_failures.map((b) => <li key={b.id} className="flex flex-wrap gap-2"><StatusBadge status={b.status} /><span>{b.server_name ? `${b.server_name}${b.database ? ` / ${b.database}` : ""}` : "DB Manager platform"}</span><span className="text-muted">{relTime(b.created_at)}</span>{b.error && <span className="w-full text-[12px] text-crit">{b.error}</span>}</li>)}
            </ul>
          ) : <p className="text-[13px] text-muted">No failed backups.</p>}
        </CardBody>
      </Card>
    </div>
  );
}

// ---- history -------------------------------------------------------------------------------------
export function asset(b: Backup): string {
  if (b.kind === "platform") return "DB Manager platform";
  return `${b.server_name ?? "removed server"}${b.database ? ` / ${b.database}` : ""}`;
}

export function rowActions(b: Backup, can: (p: string) => boolean, canPlatform: (p: string) => boolean) {
  const platform = b.kind === "platform";
  const restorable = b.status === "SUCCESS" || (platform && b.status === "VERIFYING");
  return {
    verify: can("backup.verify") && b.status !== "DELETED" && b.status !== "CREATING",
    restore: restorable && (platform ? canPlatform("restore.execute") && canPlatform("restore.platform") : can("restore.execute") && can("restore.database")),
    remove: canPlatform("backup.delete") && b.status !== "DELETED",
    download: b.status === "SUCCESS" && b.location_kind === "target" && (platform ? canPlatform("restore.platform") : can("restore.database")),
  };
}

function HistoryTab() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const { can, canPlatform } = useAuth();
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [details, setDetails] = useState<Backup | null>(null);
  const [verifying, setVerifying] = useState<Backup | null>(null);
  const [removing, setRemoving] = useState<Backup | null>(null);
  const q = useQuery({
    queryKey: ["backups", kind, status, page],
    queryFn: () => get<{ items: Backup[]; total: number; page: number; page_size: number }>("/backups", { kind, status, page, page_size: 50 }),
    refetchInterval: 15_000,
  });
  const remove = useMutation({
    mutationFn: ({ b, typed }: { b: Backup; typed: string }) => del<{ message: string }>(`/backups/${b.id}`, { confirm: typed }),
    onSuccess: (r) => { toast.success(r.message); setRemoving(null); qc.invalidateQueries({ queryKey: ["backups"] }); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const columns: ColumnDef<Backup, unknown>[] = [
    { header: "Created", accessorKey: "created_at", cell: ({ getValue }) => <span title={dateTime(getValue() as string)}>{relTime(getValue() as string)}</span> },
    { header: "Type", accessorKey: "kind", cell: ({ row: { original: b } }) => <span className="text-[12.5px]">{b.kind === "platform" ? "Platform" : `${b.engine} · ${b.method}`}</span> },
    { header: "Asset", id: "asset", cell: ({ row: { original: b } }) => asset(b) },
    { header: "Policy", accessorKey: "policy_name", cell: ({ getValue }) => (getValue() as string) ?? <span className="text-muted">manual</span> },
    { header: "Target", accessorKey: "target_name", cell: ({ row: { original: b } }) => b.location_kind === "server" ? <span className="text-muted">on the database server</span> : (b.target_name ?? "—") },
    { header: "Status", accessorKey: "status", cell: ({ row: { original: b } }) => <span className="flex flex-wrap gap-1"><StatusBadge status={b.status} />{b.pinned && <Badge tone="outline">pinned</Badge>}</span> },
    { header: "Size", accessorKey: "size_bytes", cell: ({ getValue }) => bytes(getValue() as number | null) },
    { header: "Encrypted", accessorKey: "encrypted", cell: ({ row: { original: b } }) => b.encrypted ? <Badge tone="ok">{b.encryption.mode}</Badge> : <Badge tone="neutral">no</Badge> },
    { header: "Verified", id: "verified", cell: ({ row: { original: b } }) => b.verification?.level ? <span className="text-[12.5px]">{b.verification.level}{b.verification.ok === false ? " (failed)" : ""}</span> : "—" },
    { header: "", id: "a", enableSorting: false, cell: ({ row: { original: b } }) => {
      const a = rowActions(b, can, canPlatform);
      return (
        <div className="flex justify-end gap-1">
          <Button size="sm" variant="ghost" aria-label={`Details of ${b.id.slice(0, 8)}`} onClick={() => setDetails(b)}><Eye /></Button>
          {a.verify && <Button size="sm" variant="ghost" aria-label={`Verify ${b.id.slice(0, 8)}`} onClick={() => setVerifying(b)}><CheckCircle2 /></Button>}
          {a.restore && <Button size="sm" variant="ghost" aria-label={`Restore ${b.id.slice(0, 8)}`} onClick={() => nav(`/platform/backups/${b.id}/restore`)}><RotateCcw /></Button>}
          {a.download && <Button size="sm" variant="ghost" aria-label={`Download ${b.id.slice(0, 8)}`} onClick={() => download(`/backups/${b.id}/download`, `dbm-${b.id.slice(0, 8)}-${(b.location ?? "backup").split("/").pop()}`).catch((e) => toast.error(errMsg(e)))}><Download /></Button>}
          {a.remove && <Button size="sm" variant="ghost" aria-label={`Delete ${b.id.slice(0, 8)}`} onClick={() => setRemoving(b)}><Trash2 /></Button>}
        </div>
      );
    } },
  ];
  const total = q.data?.total ?? 0;
  return (
    <Card>
      <div className="flex flex-wrap items-end gap-3 border-b border-border p-3">
        <Field label="Type"><Select value={kind} onChange={(e) => { setKind(e.target.value); setPage(1); }}><option value="">All</option><option value="platform">Platform</option><option value="database">Database</option></Select></Field>
        <Field label="Status"><Select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All except deleted</option>
          {["SUCCESS", "VERIFYING", "CREATING", "FAILED", "CORRUPTED", "DELETED"].map((s) => <option key={s} value={s}>{s}</option>)}
        </Select></Field>
        <span className="ml-auto text-[12.5px] text-muted">{total} backups</span>
        {total > 50 && <span className="flex gap-1"><Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><Button size="sm" disabled={page * 50 >= total} onClick={() => setPage(page + 1)}>Next</Button></span>}
      </div>
      <DataTable columns={columns} data={q.data?.items} loading={q.isLoading} error={q.error} dense
        empty={<EmptyState icon={<Archive />} title="No backups yet">Create a backup of DB Manager or a managed database, or set up a policy.</EmptyState>} />
      {details && <DetailsDialog backup={details} onClose={() => setDetails(null)} />}
      {verifying && <VerifyDialog backup={verifying} onClose={() => setVerifying(null)} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} danger title={`Delete backup ${removing?.id.slice(0, 8) ?? ""}?`}
        expected={removing?.id.slice(0, 8)} expectedLabel={`Type ${removing?.id.slice(0, 8)} (the first 8 characters of its id) to confirm`}
        confirmLabel="Delete backup" busy={remove.isPending} onConfirm={(typed) => removing && remove.mutate({ b: removing, typed })}>
        The stored files are removed from the storage target; the catalog entry stays (status DELETED) for the audit trail.
        {removing?.location_kind === "server" && " This backup lives on the database server: DB Manager does not delete that file."}
      </ConfirmDialog>
    </Card>
  );
}

function DetailsDialog({ backup, onClose }: { backup: Backup; onClose: () => void }) {
  const q = useQuery({ queryKey: ["backup", backup.id], queryFn: () => get<Backup>(`/backups/${backup.id}`) });
  const b = q.data ?? backup;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-3xl" title={`Backup ${b.id.slice(0, 8)}`} description={asset(b)}>
      <div className="space-y-4">
        <KeyValue items={[
          ["Status", <StatusBadge key="s" status={b.status} />],
          ["Created", `${dateTime(b.created_at)}${b.created_by ? ` by ${b.created_by}` : ""}`],
          ["Method", `${b.method}${b.tool ? ` (${b.tool})` : ""}`],
          ["Location", b.location ? <span key="l" className="break-all font-mono text-[12px]">{b.location_kind === "server" ? "database server: " : ""}{b.location}</span> : "—"],
          ["Size", bytes(b.size_bytes)],
          ["SHA-256", b.sha256 ? <span key="h" className="break-all font-mono text-[12px]">{b.sha256}</span> : "—"],
          ["Encryption", b.encrypted ? `${b.encryption.algorithm ?? b.encryption.mode}${b.encryption.recipient ? ` → ${b.encryption.recipient}` : ""}` : "none"],
          ["Versions", `DB Manager ${b.app_version}, schema ${b.schema_version ?? "?"}, format ${b.format_version}`],
          ["Verification", b.verification ? `${b.verification.level ?? ""} ${b.verification.ok === false ? "failed" : b.verification.ok ? "passed" : ""}${b.verified_at ? `, ${relTime(b.verified_at)}` : ""}` : "not verified"],
        ]} />
        {b.error && <p className="rounded-md border border-crit/30 bg-crit-soft px-3 py-2 text-[13px] text-crit">{b.error}</p>}
        {b.restore_command && <div><div className="mb-1 text-[12.5px] font-medium">Restore with the engine's tools (external)</div><CodeBlock code={b.restore_command} /></div>}
        {b.manifest && <div><div className="mb-1 text-[12.5px] font-medium">Manifest</div><CodeBlock code={JSON.stringify(b.manifest, null, 2)} maxHeight="18rem" /></div>}
      </div>
    </Dialog>
  );
}

export function VerifyDialog({ backup, onClose }: { backup: Backup; onClose: () => void }) {
  const qc = useQueryClient();
  const [identity, setIdentity] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const start = useMutation({
    mutationFn: () => post<Job>(`/backups/${backup.id}/verify`, { identity: identity || null, passphrase: passphrase || null }),
    onSuccess: (j) => setJobId(j.id),
    onError: (e) => toast.error(errMsg(e)),
  });
  const done = job.data && !["QUEUED", "RUNNING", "VERIFYING"].includes(job.data.status);
  useEffect(() => {
    if (done) void qc.invalidateQueries({ queryKey: ["backups"] });
  }, [done, qc]);
  const res = job.data?.result as { ok?: boolean; level?: string; problems?: string[]; note?: string } | null | undefined;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Verify backup ${backup.id.slice(0, 8)}`}
      description="Reads the stored copy back and checks its SHA-256; with the key it also checks every file in the archive (or the engine's own check for database backups)."
      footer={<><Button variant="ghost" onClick={onClose}>Close</Button>{!jobId && <Button variant="primary" disabled={start.isPending} onClick={() => start.mutate()}>Verify</Button>}</>}>
      <div className="space-y-3">
        {!jobId && backup.encrypted && <KeyInput mode={backup.encryption.mode} identity={identity} passphrase={passphrase} onIdentity={setIdentity} onPassphrase={setPassphrase} />}
        {job.data && <JobProgress job={job.data} />}
        {done && res && (
          <p className={res.ok ? "text-[13px] text-ok" : "text-[13px] text-crit"} role="status">
            {res.ok ? `Verified (${res.level})${res.note ? `: ${res.note}` : ""}` : `Verification failed: ${(res.problems ?? []).join("; ")}`}
          </p>
        )}
      </div>
    </Dialog>
  );
}

// ---- engine support --------------------------------------------------------------------------------
const yes = (v: boolean) => (v ? "yes" : "—");
function EnginesTab() {
  const q = useQuery({ queryKey: ["backup-capabilities"], queryFn: () => get<Capabilities>("/backup/capabilities") });
  if (q.isLoading) return <SkeletonRows rows={6} />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const c = q.data;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Database engines" description="What DB Manager runs itself. Point-in-time recovery is not offered for any engine." />
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead className="text-left text-[12px] text-muted"><tr>{["Engine", "Status", "Logical", "Native", "Physical", "PITR", "Restore", "External tooling", "Notes"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-border">
              {Object.entries(c.engines).map(([e, x]) => (
                <tr key={e} className="align-top">
                  <td className="px-3 py-2 font-medium">{e}</td>
                  <td className="px-3 py-2"><Badge tone={x.status === "AVAILABLE" ? "ok" : x.status === "PARTIAL" ? "warn" : "neutral"}>{x.status.replaceAll("_", " ")}</Badge></td>
                  <td className="px-3 py-2">{yes(x.logical)}</td><td className="px-3 py-2">{yes(x.native)}</td><td className="px-3 py-2">{yes(x.physical)}</td>
                  <td className="px-3 py-2">{yes(x.pitr)}</td><td className="px-3 py-2">{yes(x.restore)}</td><td className="px-3 py-2">{yes(x.external)}</td>
                  <td className="px-3 py-2 text-[12.5px] text-text-2"><ul className="list-disc space-y-0.5 pl-4">{x.notes.map((n) => <li key={n}>{n}</li>)}</ul></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card>
        <CardHeader title="Client tools on this DB Manager host" />
        <CardBody>
          <ul className="space-y-1 text-[13px]">
            {Object.entries(c.tools).map(([n, t]) => <li key={n} className="flex gap-2"><HardDrive className="size-4 text-muted" /><span className="w-28 font-mono">{n}</span>{t.path ? <span>{t.version ?? t.path}</span> : <Badge tone="warn">not installed</Badge>}</li>)}
          </ul>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Platform backup contents" />
        <CardBody className="grid gap-4 text-[13px] md:grid-cols-2">
          <div><div className="mb-1 flex items-center gap-1 font-medium"><ShieldCheck className="size-4" />Components</div><ul className="space-y-0.5">{Object.entries(c.platform.components).map(([k, v]) => <li key={k}><span className="font-mono">{k}</span>: {v}</li>)}</ul></div>
          <div><div className="mb-1 font-medium">Not included (rebuilt on their own)</div><ul className="space-y-0.5">{Object.entries(c.platform.excluded).map(([k, v]) => <li key={k}><span className="font-mono">{k}</span>: {v}</li>)}</ul></div>
        </CardBody>
      </Card>
    </div>
  );
}
