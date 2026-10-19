import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { CalendarClock, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { del, get, post, put } from "@/api/client";
import type { Environment, Page, Server, Site } from "@/api/types";
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
import { engineLabel } from "@/lib/engines";
import { dateTime, relTime } from "@/lib/format";
import {
  cleanEncryption, COMPONENTS, DEFAULT_COMPONENTS, type DatabaseScope, type Encryption, encryptionProblem, errMsg, type Job, type Policy,
  type Retention, type Schedule, type Target,
} from "./api";
import { EncryptionFields, StatusBadge } from "./shared";

export interface PolicyForm {
  name: string; description: string; kind: "platform" | "database"; enabled: boolean; target_id: string;
  components: string[]; metrics: "none" | "export"; metrics_days: number;
  scope: DatabaseScope; databases_text: string; tags_text: string;
  schedule: Schedule; encryption: Encryption; retention: Retention; channel_ids: string[]; max_age_hours: number;
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

export function toPolicyForm(p: Policy | null, prefillServer?: string | null): PolicyForm {
  const sc = p?.scope ?? {};
  return {
    name: p?.name ?? "", description: p?.description ?? "", kind: p?.kind ?? (prefillServer ? "database" : "platform"), enabled: p?.enabled ?? true,
    target_id: p?.target_id ?? "", components: sc.components ?? [...DEFAULT_COMPONENTS], metrics: sc.metrics ?? "none", metrics_days: sc.metrics_days ?? 0,
    scope: {
      server_ids: sc.server_ids ?? (prefillServer ? [prefillServer] : []), environment_ids: sc.environment_ids ?? [], site_ids: sc.site_ids ?? [],
      tags: sc.tags ?? [], databases: sc.databases ?? [], server_directory: sc.server_directory ?? "", directory: sc.directory ?? "",
    },
    databases_text: (sc.databases ?? []).join(", "), tags_text: (sc.tags ?? []).join(", "),
    schedule: p?.schedule ?? { frequency: "daily", time: "02:00", time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", weekday: 7, day_of_month: 1 },
    encryption: p?.encryption ?? { mode: "recipient", recipient: "" },
    retention: p?.retention ?? { keep_last: 7, keep_days: 0, keep_daily: 0, keep_weekly: 0, keep_monthly: 0 },
    channel_ids: p?.channel_ids ?? [], max_age_hours: p?.max_age_hours ?? 0,
  };
}

export function policyProblem(f: PolicyForm): string | null {
  if (!f.name.trim()) return "Enter a name";
  if (!f.target_id) return "Choose a storage target";
  if (f.kind === "platform" && !f.components.length) return "Select at least one component";
  if (f.kind === "database" && !(f.scope.server_ids.length || f.scope.environment_ids.length || f.scope.site_ids.length || split(f.tags_text).length)) {
    return "Select servers, environments, sites or tags";
  }
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(f.schedule.time)) return "Time is HH:MM (24 h)";
  return encryptionProblem(f.encryption, f.schedule.frequency !== "manual");
}

export function policyBody(f: PolicyForm) {
  return {
    name: f.name.trim(), description: f.description.trim() || null, kind: f.kind, enabled: f.enabled, target_id: f.target_id,
    ...(f.kind === "platform"
      ? { platform_scope: { components: f.components, metrics: f.metrics, metrics_days: f.metrics_days } }
      : {
        database_scope: {
          server_ids: f.scope.server_ids, environment_ids: f.scope.environment_ids, site_ids: f.scope.site_ids, tags: split(f.tags_text),
          databases: split(f.databases_text), ...(f.scope.server_directory ? { server_directory: f.scope.server_directory } : {}),
          ...(f.scope.directory ? { directory: f.scope.directory } : {}),
        },
      }),
    schedule: f.schedule, encryption: cleanEncryption(f.encryption), retention: f.retention, channel_ids: f.channel_ids, max_age_hours: f.max_age_hours,
  };
}

function MultiCheck({ label, options, value, onChange }: { label: string; options: { id: string; name: string }[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset className="rounded-md border border-border p-2">
      <legend className="px-1 text-[12.5px] font-medium">{label}</legend>
      <div className="max-h-36 space-y-0.5 overflow-y-auto text-[13px]">
        {options.length ? options.map((o) => (
          <label key={o.id} className="flex items-center gap-2"><input type="checkbox" checked={value.includes(o.id)} onChange={(e) => onChange(e.target.checked ? [...value, o.id] : value.filter((x) => x !== o.id))} />{o.name}</label>
        )) : <span className="text-muted">none</span>}
      </div>
    </fieldset>
  );
}

export function useTargets() {
  return useQuery({ queryKey: ["backup-targets"], queryFn: () => get<Target[]>("/backup/targets") });
}
export function useBackupServers() {
  return useQuery({ queryKey: ["backup-servers"], queryFn: () => get<Page<Server>>("/servers", { page_size: 500, sort: "name" }), select: (p) => p.items.filter((s) => !s.is_demo) });
}

export function DatabaseScopeFields({ f, set }: { f: PolicyForm; set: (p: Partial<PolicyForm>) => void }) {
  const servers = useBackupServers();
  const envs = useQuery({ queryKey: ["environments"], queryFn: () => get<Environment[]>("/environments") });
  const sites = useQuery({ queryKey: ["sites"], queryFn: () => get<Site[]>("/sites") });
  const setScope = (p: Partial<DatabaseScope>) => set({ scope: { ...f.scope, ...p } });
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <MultiCheck label="Servers" options={(servers.data ?? []).map((s) => ({ id: s.id, name: `${s.name} (${engineLabel(s.engine)})` }))} value={f.scope.server_ids} onChange={(v) => setScope({ server_ids: v })} />
      <div className="space-y-3">
        <MultiCheck label="Environments" options={envs.data ?? []} value={f.scope.environment_ids} onChange={(v) => setScope({ environment_ids: v })} />
        <MultiCheck label="Sites" options={sites.data ?? []} value={f.scope.site_ids} onChange={(v) => setScope({ site_ids: v })} />
      </div>
      <Field label="Tags (comma separated, key or key=value)"><Input value={f.tags_text} onChange={(e) => set({ tags_text: e.target.value })} placeholder="backup=nightly" /></Field>
      <Field label="Databases (comma separated)" hint="Empty = each server's default database. Oracle: schema names."><Input value={f.databases_text} onChange={(e) => set({ databases_text: e.target.value })} /></Field>
      <Field label="Server directory (SQL Server, Db2)" hint="Absolute path on the database server. Empty = the instance default (SQL Server 2019+)."><Input value={f.scope.server_directory ?? ""} onChange={(e) => setScope({ server_directory: e.target.value })} className="font-mono" /></Field>
      <Field label="Data Pump directory (Oracle)" hint="Directory object; empty = DATA_PUMP_DIR"><Input value={f.scope.directory ?? ""} onChange={(e) => setScope({ directory: e.target.value })} className="font-mono" /></Field>
    </div>
  );
}

export function ComponentChecks({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset className="space-y-1 text-[13px]">
      {COMPONENTS.map(([k, label]) => (
        <label key={k} className="flex items-start gap-2">
          <input type="checkbox" className="mt-1" checked={value.includes(k)} onChange={(e) => onChange(e.target.checked ? [...value, k] : value.filter((x) => x !== k))} />
          <span><span className="font-medium">{k}</span> — {label}</span>
        </label>
      ))}
    </fieldset>
  );
}

export function PolicyDialog({ policy, prefillServer, onClose }: { policy: Policy | null; prefillServer?: string | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<PolicyForm>(() => toPolicyForm(policy, prefillServer));
  const set = (p: Partial<PolicyForm>) => setF((x) => ({ ...x, ...p }));
  const targets = useTargets();
  const channels = useQuery({ queryKey: ["notification-channels"], queryFn: () => get<{ id: string; name: string }[]>("/notification-channels"), retry: false });
  const save = useMutation({
    mutationFn: () => (policy ? put(`/backup/policies/${policy.id}`, policyBody(f)) : post("/backup/policies", policyBody(f))),
    onSuccess: () => { toast.success(policy ? "Policy saved" : "Policy created"); qc.invalidateQueries({ queryKey: ["backup-policies"] }); onClose(); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const problem = policyProblem(f);
  const sch = f.schedule;
  const setSch = (p: Partial<Schedule>) => set({ schedule: { ...sch, ...p } });
  const setRet = (k: keyof Retention, v: string) => set({ retention: { ...f.retention, [k]: Math.max(0, Number(v.replace(/\D/g, "")) || 0) } });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-3xl" title={policy ? `Edit ${policy.name}` : "New backup policy"}
      description="A policy runs backups on a schedule, encrypts them and removes the ones its retention rules no longer keep (only backups it created)."
      footer={<>{problem && <span className="mr-auto text-[12.5px] text-muted" role="status">{problem}</span>}<Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!!problem || save.isPending} onClick={() => save.mutate()}>{policy ? "Save" : "Create policy"}</Button></>}>
      <div className="space-y-5">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name"><Input value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="Nightly platform backup" /></Field>
          <Field label="What to back up">
            <Select value={f.kind} disabled={!!policy} onChange={(e) => set({ kind: e.target.value as PolicyForm["kind"] })}>
              <option value="platform">DBManager platform (configuration)</option><option value="database">Managed databases</option>
            </Select>
          </Field>
          <Field label="Storage target">
            <Select value={f.target_id} onChange={(e) => set({ target_id: e.target.value })}>
              <option value="">Choose…</option>{(targets.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name} ({t.kind})</option>)}
            </Select>
          </Field>
          <div className="flex items-end gap-2 text-[13px]"><Switch checked={f.enabled} onCheckedChange={(v) => set({ enabled: v })} label="Enabled" /> Enabled</div>
          <Field label="Description" className="sm:col-span-2"><Textarea rows={2} value={f.description} onChange={(e) => set({ description: e.target.value })} /></Field>
        </div>
        <section>
          <h3 className="mb-2 text-[13.5px] font-semibold">Scope</h3>
          {f.kind === "platform" ? (
            <div className="space-y-3">
              <ComponentChecks value={f.components} onChange={(v) => set({ components: v })} />
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Monitoring telemetry"><Select value={f.metrics} onChange={(e) => set({ metrics: e.target.value as PolicyForm["metrics"] })}><option value="none">Not included</option><option value="export">VictoriaMetrics export</option></Select></Field>
                {f.metrics === "export" && <Field label="Days of telemetry (0 = everything retained)"><Input inputMode="numeric" value={String(f.metrics_days)} onChange={(e) => set({ metrics_days: Number(e.target.value.replace(/\D/g, "")) || 0 })} /></Field>}
              </div>
            </div>
          ) : <DatabaseScopeFields f={f} set={set} />}
        </section>
        <section className="grid gap-3 sm:grid-cols-3">
          <h3 className="text-[13.5px] font-semibold sm:col-span-3">Schedule</h3>
          <Field label="Frequency"><Select value={sch.frequency} onChange={(e) => setSch({ frequency: e.target.value as Schedule["frequency"] })}><option value="manual">Manual only</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></Select></Field>
          {sch.frequency !== "manual" && <>
            <Field label="Time (HH:MM)"><Input value={sch.time} onChange={(e) => setSch({ time: e.target.value })} /></Field>
            <Field label="Time zone"><Input value={sch.time_zone} onChange={(e) => setSch({ time_zone: e.target.value })} /></Field>
            {sch.frequency === "weekly" && <Field label="Day"><Select value={String(sch.weekday)} onChange={(e) => setSch({ weekday: Number(e.target.value) })}>{WEEKDAYS.map((d, i) => <option key={d} value={i + 1}>{d}</option>)}</Select></Field>}
            {sch.frequency === "monthly" && <Field label="Day of month (1–28)"><Input inputMode="numeric" value={String(sch.day_of_month)} onChange={(e) => setSch({ day_of_month: Math.min(28, Math.max(1, Number(e.target.value.replace(/\D/g, "")) || 1)) })} /></Field>}
          </>}
        </section>
        <section><h3 className="mb-2 text-[13.5px] font-semibold">Encryption</h3><EncryptionFields value={f.encryption} onChange={(e) => set({ encryption: e })} /></section>
        <section className="grid gap-3 sm:grid-cols-5">
          <h3 className="text-[13.5px] font-semibold sm:col-span-5">Retention</h3>
          {(["keep_last", "keep_days", "keep_daily", "keep_weekly", "keep_monthly"] as const).map((k) => (
            <Field key={k} label={{ keep_last: "Keep last N", keep_days: "Keep N days", keep_daily: "Daily generations", keep_weekly: "Weekly generations", keep_monthly: "Monthly generations" }[k]}>
              <Input inputMode="numeric" value={String(f.retention[k])} onChange={(e) => setRet(k, e.target.value)} />
            </Field>
          ))}
          <p className="text-[12px] text-muted sm:col-span-5">All zero = keep everything. A backup is kept when any rule keeps it; the newest successful backup and pinned backups are always kept.</p>
        </section>
        <section className="grid gap-3 sm:grid-cols-2">
          <h3 className="text-[13.5px] font-semibold sm:col-span-2">Notifications</h3>
          {channels.data ? (
            <MultiCheck label="Notification channels (failures, no recent success, storage unavailable)" options={channels.data} value={f.channel_ids} onChange={(v) => set({ channel_ids: v })} />
          ) : <p className="text-[12.5px] text-muted">Notification channels are not visible to you.</p>}
          <Field label="Alert when no successful backup for (hours)" hint="0 = derived from the schedule (daily 26 h, weekly 8 days, monthly 32 days)">
            <Input inputMode="numeric" value={String(f.max_age_hours)} onChange={(e) => set({ max_age_hours: Number(e.target.value.replace(/\D/g, "")) || 0 })} />
          </Field>
        </section>
      </div>
    </Dialog>
  );
}

function RetentionDialog({ policy, onClose }: { policy: Policy; onClose: () => void }) {
  const q = useQuery({ queryKey: ["backup-retention", policy.id], queryFn: () => get<{ keep: string[]; remove: string[]; note: string }>(`/backup/policies/${policy.id}/retention`) });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Retention of ${policy.name}`} description={q.data?.note}>
      {q.data && (
        <div className="space-y-2 text-[13px]">
          <div>Kept: {q.data.keep.length ? q.data.keep.map((i) => <Badge key={i} tone="ok" className="mr-1 font-mono">{i.slice(0, 8)}</Badge>) : "none"}</div>
          <div>Removed at the next run: {q.data.remove.length ? q.data.remove.map((i) => <Badge key={i} tone="warn" className="mr-1 font-mono">{i.slice(0, 8)}</Badge>) : "none"}</div>
        </div>
      )}
    </Dialog>
  );
}

const describeSchedule = (s: Schedule) =>
  s.frequency === "manual" ? "manual" : `${s.frequency} at ${s.time} ${s.time_zone}${s.frequency === "weekly" ? ` (${WEEKDAYS[s.weekday - 1]})` : s.frequency === "monthly" ? ` (day ${s.day_of_month})` : ""}`;

export function PoliciesTab() {
  const qc = useQueryClient();
  const { can, canPlatform } = useAuth();
  const [params, setParams] = useSearchParams();
  const prefill = params.get("server_id");
  const [edit, setEdit] = useState<Policy | "new" | null>(params.get("policy") === "new" ? "new" : null);
  const [removing, setRemoving] = useState<Policy | null>(null);
  const [retention, setRetention] = useState<Policy | null>(null);
  const q = useQuery({ queryKey: ["backup-policies"], queryFn: () => get<Policy[]>("/backup/policies"), refetchInterval: 30_000 });
  const run = useMutation({
    mutationFn: (p: Policy) => post<Job>(`/backup/policies/${p.id}/run`),
    onSuccess: (_j, p) => { toast.success(`${p.name} queued`); qc.invalidateQueries({ queryKey: ["backup-overview"] }); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const remove = useMutation({
    mutationFn: (p: Policy) => del<{ message: string }>(`/backup/policies/${p.id}`),
    onSuccess: (r) => { toast.success(r.message); setRemoving(null); qc.invalidateQueries({ queryKey: ["backup-policies"] }); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const manage = canPlatform("backup.policy.manage");
  const close = () => { setEdit(null); if (params.get("policy")) setParams((p) => { const n = new URLSearchParams(p); n.delete("policy"); n.delete("server_id"); return n; }); };
  const columns: ColumnDef<Policy, unknown>[] = [
    { header: "Policy", accessorKey: "name", cell: ({ row: { original: p } }) => <div><div className="font-medium">{p.name}</div><span className="text-[12px] text-muted">{p.kind === "platform" ? "Platform" : "Managed databases"} · encryption {p.encryption.mode}</span></div> },
    { header: "Schedule", accessorKey: "schedule", cell: ({ row: { original: p } }) => <span className="text-[12.5px]">{describeSchedule(p.schedule)}{p.next_run_at && <span className="block text-muted" title={dateTime(p.next_run_at)}>next {relTime(p.next_run_at)}</span>}</span> },
    { header: "Retention", accessorKey: "retention", cell: ({ row: { original: { retention: r } } }) => <span className="text-[12.5px]">{[r.keep_last && `last ${r.keep_last}`, r.keep_days && `${r.keep_days} days`, r.keep_daily && `${r.keep_daily} daily`, r.keep_weekly && `${r.keep_weekly} weekly`, r.keep_monthly && `${r.keep_monthly} monthly`].filter(Boolean).join(", ") || "keep all"}</span> },
    { header: "Last", accessorKey: "last_status", cell: ({ row: { original: p } }) => <span className="flex flex-col gap-0.5 text-[12.5px]">{p.last_status ? <StatusBadge status={p.last_status} /> : "—"}{p.last_success_at && <span className="text-muted">success {relTime(p.last_success_at)}</span>}</span> },
    { header: "State", accessorKey: "enabled", cell: ({ getValue }) => getValue() ? <Badge tone="ok">enabled</Badge> : <Badge tone="neutral">disabled</Badge> },
    { header: "", id: "a", enableSorting: false, cell: ({ row: { original: p } }) => (
      <div className="flex justify-end gap-1">
        {can("backup.create") && <Button size="sm" onClick={() => run.mutate(p)} disabled={run.isPending}><Play /> Run now</Button>}
        <Button size="sm" variant="ghost" aria-label={`Retention of ${p.name}`} onClick={() => setRetention(p)}><CalendarClock /></Button>
        {manage && <Button size="sm" variant="ghost" aria-label={`Edit ${p.name}`} onClick={() => setEdit(p)}><Pencil /></Button>}
        {manage && <Button size="sm" variant="ghost" aria-label={`Delete ${p.name}`} onClick={() => setRemoving(p)}><Trash2 /></Button>}
      </div>
    ) },
  ];
  return (
    <Card>
      {manage && <div className="flex justify-end border-b border-border p-3"><Button size="sm" variant="primary" onClick={() => setEdit("new")}><Plus /> New policy</Button></div>}
      <DataTable columns={columns} data={q.data} loading={q.isLoading} error={q.error} dense
        empty={<EmptyState icon={<CalendarClock />} title="No backup policies">A policy backs up on a schedule and applies retention.</EmptyState>} />
      {edit && <PolicyDialog policy={edit === "new" ? null : edit} prefillServer={edit === "new" ? prefill : null} onClose={close} />}
      {retention && <RetentionDialog policy={retention} onClose={() => setRetention(null)} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={`Delete ${removing?.name ?? ""}?`} confirmLabel="Delete policy" danger
        busy={remove.isPending} onConfirm={() => removing && remove.mutate(removing)}>
        Its backups are kept, but are no longer subject to retention.
      </ConfirmDialog>
    </Card>
  );
}
