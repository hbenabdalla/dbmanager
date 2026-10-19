import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { FolderSearch, HardDrive, Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { del, post, put } from "@/api/client";
import { ConfirmDialog } from "@/components/confirm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/states";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/lib/auth";
import { bytes, dateTime, relTime } from "@/lib/format";
import { errMsg, type Target } from "./api";
import { useTargets } from "./policies";

interface TForm {
  name: string; kind: Target["kind"]; enabled: boolean; path: string;
  endpoint_url: string; bucket: string; prefix: string; region: string; access_key_id: string; addressing_style: string;
  verify_tls: boolean; ca_pem: string; secret: string;
}

function toForm(t: Target | null): TForm {
  const c = (t?.config ?? {}) as Record<string, string | boolean | undefined>;
  return {
    name: t?.name ?? "", kind: t?.kind ?? "local", enabled: t?.enabled ?? true, path: String(c.path ?? "/var/lib/dbmanager/backups/"),
    endpoint_url: String(c.endpoint_url ?? ""), bucket: String(c.bucket ?? ""), prefix: String(c.prefix ?? ""), region: String(c.region ?? ""),
    access_key_id: String(c.access_key_id ?? ""), addressing_style: String(c.addressing_style ?? "auto"), verify_tls: c.verify_tls !== false,
    ca_pem: "", secret: "",
  };
}

export function targetProblem(f: TForm): string | null {
  if (!f.name.trim()) return "Enter a name";
  if (f.kind === "s3") {
    if (!f.bucket.trim()) return "Enter the bucket";
    if (f.endpoint_url && !/^https?:\/\//.test(f.endpoint_url)) return "The endpoint is an http(s) URL";
  } else if (!f.path.startsWith("/")) return "Enter an absolute directory path";
  return null;
}

function body(f: TForm) {
  const config = f.kind === "s3"
    ? {
      bucket: f.bucket.trim(), addressing_style: f.addressing_style, verify_tls: f.verify_tls,
      ...(f.endpoint_url ? { endpoint_url: f.endpoint_url.trim() } : {}), ...(f.prefix ? { prefix: f.prefix.trim() } : {}),
      ...(f.region ? { region: f.region.trim() } : {}), ...(f.access_key_id ? { access_key_id: f.access_key_id.trim() } : {}),
      ...(f.ca_pem.trim() ? { ca_pem: f.ca_pem.trim() } : {}),
    }
    : { path: f.path.trim() };
  return { name: f.name.trim(), kind: f.kind, enabled: f.enabled, config, ...(f.kind === "s3" && f.secret ? { secret_access_key: f.secret } : {}) };
}

function TargetDialog({ target, onClose }: { target: Target | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<TForm>(() => toForm(target));
  const set = (p: Partial<TForm>) => setF((x) => ({ ...x, ...p }));
  const save = useMutation({
    mutationFn: () => (target ? put(`/backup/targets/${target.id}`, body(f)) : post("/backup/targets", body(f))),
    onSuccess: () => { toast.success(target ? "Target saved" : "Target added"); qc.invalidateQueries({ queryKey: ["backup-targets"] }); onClose(); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const problem = targetProblem(f);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-2xl" title={target ? `Edit ${target.name}` : "New storage target"}
      description="File-system targets must be inside DBM_BACKUP_ALLOWED_ROOTS. DB Manager only writes and deletes its own backup folders."
      footer={<>{problem && <span className="mr-auto text-[12.5px] text-muted" role="status">{problem}</span>}<Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!!problem || save.isPending} onClick={() => save.mutate()}>{target ? "Save" : "Add target"}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><Input value={f.name} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field label="Kind">
          <Select value={f.kind} disabled={!!target} onChange={(e) => set({ kind: e.target.value as Target["kind"] })}>
            <option value="local">Local directory</option><option value="mounted">Mounted volume</option><option value="nfs">NFS mount</option><option value="s3">S3-compatible bucket</option>
          </Select>
        </Field>
        {f.kind !== "s3" ? (
          <Field label="Directory" className="sm:col-span-2" hint="Absolute path as seen by the DB Manager backend container"><Input className="font-mono" value={f.path} onChange={(e) => set({ path: e.target.value })} /></Field>
        ) : <>
          <Field label="Endpoint URL" hint="Empty = AWS S3"><Input value={f.endpoint_url} onChange={(e) => set({ endpoint_url: e.target.value })} placeholder="https://minio.example.com" /></Field>
          <Field label="Bucket"><Input value={f.bucket} onChange={(e) => set({ bucket: e.target.value })} /></Field>
          <Field label="Prefix"><Input value={f.prefix} onChange={(e) => set({ prefix: e.target.value })} placeholder="dbmanager" /></Field>
          <Field label="Region"><Input value={f.region} onChange={(e) => set({ region: e.target.value })} /></Field>
          <Field label="Access key ID"><Input value={f.access_key_id} onChange={(e) => set({ access_key_id: e.target.value })} /></Field>
          <Field label="Secret access key" hint={target?.has_secret ? "A secret is stored. Leave empty to keep it." : "Stored encrypted; never shown again"}>
            <Input type="password" autoComplete="new-password" value={f.secret} onChange={(e) => set({ secret: e.target.value })} placeholder={target?.has_secret ? "•••••••• stored" : ""} />
          </Field>
          <Field label="Addressing style"><Select value={f.addressing_style} onChange={(e) => set({ addressing_style: e.target.value })}><option value="auto">auto</option><option value="path">path (MinIO, Ceph)</option><option value="virtual">virtual-hosted</option></Select></Field>
          <div className="flex items-end gap-2 text-[13px]"><Switch checked={f.verify_tls} onCheckedChange={(v) => set({ verify_tls: v })} label="Verify TLS" /> Verify the TLS certificate</div>
          {f.verify_tls && <Field label="CA certificate (PEM, optional)" className="sm:col-span-2"><Textarea rows={3} value={f.ca_pem} onChange={(e) => set({ ca_pem: e.target.value })} placeholder="-----BEGIN CERTIFICATE-----" /></Field>}
        </>}
        <div className="flex items-end gap-2 text-[13px]"><Switch checked={f.enabled} onCheckedChange={(v) => set({ enabled: v })} label="Enabled" /> Enabled</div>
      </div>
    </Dialog>
  );
}

function where(t: Target): string {
  const c = t.config as Record<string, string>;
  return t.kind === "s3" ? `s3://${c.bucket}/${c.prefix ?? ""}${c.endpoint_url ? ` (${c.endpoint_url})` : ""}` : c.path;
}

export function TargetsTab() {
  const qc = useQueryClient();
  const { canPlatform } = useAuth();
  const manage = canPlatform("backup.target.manage");
  const [edit, setEdit] = useState<Target | "new" | null>(null);
  const [removing, setRemoving] = useState<Target | null>(null);
  const q = useTargets();
  const check = useMutation({
    mutationFn: (t: Target) => post<{ ok: boolean; error: string | null; info: Record<string, unknown> }>(`/backup/targets/${t.id}/check`),
    onSuccess: (r, t) => { (r.ok ? toast.success(`${t.name} is writable${r.info.warning ? ` — ${r.info.warning}` : ""}`) : toast.error(`${t.name}: ${r.error}`)); qc.invalidateQueries({ queryKey: ["backup-targets"] }); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const scan = useMutation({
    mutationFn: (t: Target) => post<{ registered: number; skipped: number }>(`/backup/targets/${t.id}/scan`),
    onSuccess: (r) => { toast.success(`${r.registered} backups registered from storage (${r.skipped} already known or unreadable). Verify them before restoring.`); qc.invalidateQueries({ queryKey: ["backups"] }); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const remove = useMutation({
    mutationFn: (t: Target) => del<{ message: string }>(`/backup/targets/${t.id}`),
    onSuccess: (r) => { toast.success(r.message); setRemoving(null); qc.invalidateQueries({ queryKey: ["backup-targets"] }); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const columns: ColumnDef<Target, unknown>[] = [
    { header: "Target", accessorKey: "name", cell: ({ row: { original: t } }) => <div><div className="font-medium">{t.name} <Badge tone="outline">{t.kind}</Badge></div><span className="break-all font-mono text-[12px] text-muted">{where(t)}</span></div> },
    { header: "Usage", id: "u", cell: ({ row: { original: t } }) => <span className="text-[12.5px]">{t.backups} backups · {bytes(t.stored_bytes)}</span> },
    { header: "Last check", id: "c", cell: ({ row: { original: t } }) => t.last_check_at ? <span title={dateTime(t.last_check_at)} className="text-[12.5px]">{t.last_check_ok ? <Badge tone="ok">ok</Badge> : <Badge tone="crit">failed</Badge>} {relTime(t.last_check_at)}{t.last_check_error && <span className="block text-crit">{t.last_check_error}</span>}</span> : <span className="text-muted">never</span> },
    { header: "State", accessorKey: "enabled", cell: ({ getValue }) => getValue() ? <Badge tone="ok">enabled</Badge> : <Badge tone="neutral">disabled</Badge> },
    { header: "", id: "a", enableSorting: false, cell: ({ row: { original: t } }) => (
      <div className="flex justify-end gap-1">
        {manage && <Button size="sm" onClick={() => check.mutate(t)} disabled={check.isPending}><ShieldCheck /> Check</Button>}
        {manage && <Button size="sm" onClick={() => scan.mutate(t)} disabled={scan.isPending}><FolderSearch /> Scan for backups</Button>}
        {manage && <Button size="sm" variant="ghost" aria-label={`Edit ${t.name}`} onClick={() => setEdit(t)}><Pencil /></Button>}
        {manage && <Button size="sm" variant="ghost" aria-label={`Delete ${t.name}`} onClick={() => setRemoving(t)}><Trash2 /></Button>}
      </div>
    ) },
  ];
  return (
    <Card>
      {manage && <div className="flex justify-end border-b border-border p-3"><Button size="sm" variant="primary" onClick={() => setEdit("new")}><Plus /> New target</Button></div>}
      <DataTable columns={columns} data={q.data} loading={q.isLoading} error={q.error} dense
        empty={<EmptyState icon={<HardDrive />} title="No storage targets">Add a directory, a mounted share or an S3-compatible bucket to store backups.</EmptyState>} />
      {edit && <TargetDialog target={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={`Remove ${removing?.name ?? ""}?`} confirmLabel="Remove target" danger
        busy={remove.isPending} onConfirm={() => removing && remove.mutate(removing)}>
        Only targets without policies and without backups in the catalog can be removed. Stored files are never deleted by this action.
      </ConfirmDialog>
    </Card>
  );
}
