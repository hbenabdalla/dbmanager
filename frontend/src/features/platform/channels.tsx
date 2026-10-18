import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { History, Mail, Pencil, Plus, Send, Trash2, Webhook } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, del, get, post, put } from "@/api/client";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/ui/states";
import { dateTime, relTime } from "@/lib/format";

export interface NotificationChannel {
  id: string; name: string; kind: "webhook" | "email"; enabled: boolean; min_severity: string; send_for_simulated: boolean;
  config: { url?: string; headers?: Record<string, string>; recipients?: string[]; smtp_host?: string; smtp_port?: number; smtp_user?: string; from?: string; starttls?: boolean };
  has_signing_secret: boolean; has_smtp_password: boolean; created_by: string | null; created_at: string;
}
interface Delivery { id: number; alert_id: string | null; event: string; status: string; attempts: number; response_code: number | null; error: string | null; created_at: string }
interface TestResult { status: string; response_code: number | null; error: string | null; attempts: number }

const SEVERITIES = ["info", "low", "medium", "high", "critical"];
const err = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

interface Form {
  name: string; kind: "webhook" | "email"; enabled: boolean; min_severity: string; send_for_simulated: boolean;
  url: string; headers: string; recipients: string; smtp_host: string; smtp_port: string; smtp_user: string; from: string; starttls: boolean;
  /** write-only: "" = keep the stored value unless `clear` is set */
  secret: string; clear: boolean;
}

function toForm(c: NotificationChannel | null): Form {
  const cfg = c?.config ?? {};
  return {
    name: c?.name ?? "", kind: c?.kind ?? "webhook", enabled: c?.enabled ?? true, min_severity: c?.min_severity ?? "info",
    send_for_simulated: c?.send_for_simulated ?? false, url: cfg.url ?? "",
    headers: Object.entries(cfg.headers ?? {}).map(([k, v]) => `${k}: ${v}`).join("\n"),
    recipients: (cfg.recipients ?? []).join(", "), smtp_host: cfg.smtp_host ?? "", smtp_port: cfg.smtp_port ? String(cfg.smtp_port) : "",
    smtp_user: cfg.smtp_user ?? "", from: cfg.from ?? "", starttls: cfg.starttls ?? true, secret: "", clear: false,
  };
}

function toBody(f: Form) {
  const secretKey = f.kind === "webhook" ? "signing_secret" : "smtp_password";
  const config = f.kind === "webhook"
    ? { url: f.url.trim(), headers: Object.fromEntries(f.headers.split("\n").map((l) => l.split(/:(.*)/s).map((x) => x.trim())).filter(([k, v]) => k && v).map(([k, v]) => [k, v])) }
    : {
      recipients: f.recipients.split(",").map((r) => r.trim()).filter(Boolean),
      ...(f.smtp_host ? { smtp_host: f.smtp_host.trim() } : {}), ...(f.smtp_port ? { smtp_port: Number(f.smtp_port) } : {}),
      ...(f.smtp_user ? { smtp_user: f.smtp_user.trim() } : {}), ...(f.from ? { from: f.from.trim() } : {}), starttls: f.starttls,
    };
  return {
    name: f.name.trim(), kind: f.kind, enabled: f.enabled, min_severity: f.min_severity, send_for_simulated: f.send_for_simulated, config,
    ...(f.clear ? { [secretKey]: "" } : f.secret ? { [secretKey]: f.secret } : {}),
  };
}

function ChannelDialog({ channel, onClose }: { channel: NotificationChannel | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<Form>(() => toForm(channel));
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((p) => ({ ...p, [k]: v }));
  const stored = channel ? (f.kind === "webhook" ? channel.has_signing_secret : channel.has_smtp_password) : false;
  const save = useMutation({
    mutationFn: () => (channel ? put(`/notification-channels/${channel.id}`, toBody(f)) : post("/notification-channels", toBody(f))),
    onSuccess: () => { toast.success(channel ? "Channel saved" : "Channel created"); qc.invalidateQueries({ queryKey: ["notification-channels"] }); onClose(); },
    onError: (e) => toast.error(err(e)),
  });
  const valid = f.name.trim() && (f.kind === "webhook" ? /^https?:\/\/.+/.test(f.url.trim()) : f.recipients.includes("@"));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-2xl" title={channel ? `Edit ${channel.name}` : "New notification channel"}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>{channel ? "Save" : "Create channel"}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><Input value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="DBA on-call" /></Field>
        <Field label="Type">
          <Select value={f.kind} disabled={!!channel} onChange={(e) => set("kind", e.target.value as Form["kind"])}>
            <option value="webhook">Webhook (JSON POST)</option><option value="email">Email (SMTP)</option>
          </Select>
        </Field>
        {f.kind === "webhook" ? <>
          <Field label="URL" className="sm:col-span-2" hint="Private, loopback and link-local addresses are refused unless DBM_WEBHOOK_ALLOW_PRIVATE=true"><Input value={f.url} onChange={(e) => set("url", e.target.value)} placeholder="https://hooks.example.com/dbmanager" /></Field>
          <Field label="Extra headers (one per line, Name: value)" className="sm:col-span-2" hint="Not encrypted: do not put credentials here">
            <textarea className="min-h-[60px] w-full rounded-md border border-border bg-surface px-2.5 py-1.5 font-mono text-[12.5px]" value={f.headers} onChange={(e) => set("headers", e.target.value)} placeholder="X-Team: dba" />
          </Field>
          <Field label="Signing secret (HMAC-SHA256)" className="sm:col-span-2" hint={stored ? "A secret is stored. Leave empty to keep it." : "Optional. Sent as X-DBM-Signature: sha256=<hex>"}>
            <Input type="password" autoComplete="new-password" value={f.secret} disabled={f.clear} onChange={(e) => set("secret", e.target.value)} placeholder={stored ? "•••••••• stored" : ""} />
          </Field>
        </> : <>
          <Field label="Recipients (comma separated)" className="sm:col-span-2"><Input value={f.recipients} onChange={(e) => set("recipients", e.target.value)} placeholder="dba@example.com, oncall@example.com" /></Field>
          <Field label="SMTP host" hint="Empty = DBM_SMTP_HOST"><Input value={f.smtp_host} onChange={(e) => set("smtp_host", e.target.value)} /></Field>
          <Field label="SMTP port" hint="Empty = DBM_SMTP_PORT"><Input inputMode="numeric" value={f.smtp_port} onChange={(e) => set("smtp_port", e.target.value.replace(/\D/g, ""))} /></Field>
          <Field label="SMTP user" hint="Empty = DBM_SMTP_USER"><Input value={f.smtp_user} onChange={(e) => set("smtp_user", e.target.value)} /></Field>
          <Field label="From address" hint="Empty = DBM_SMTP_FROM"><Input value={f.from} onChange={(e) => set("from", e.target.value)} /></Field>
          <Field label="SMTP password" className="sm:col-span-2" hint={stored ? "A password is stored (encrypted). Leave empty to keep it." : "Empty = DBM_SMTP_PASSWORD. Stored encrypted, never shown again."}>
            <Input type="password" autoComplete="new-password" value={f.secret} disabled={f.clear} onChange={(e) => set("secret", e.target.value)} placeholder={stored ? "•••••••• stored" : ""} />
          </Field>
          <div className="flex items-center gap-2 text-[13px]"><Switch checked={f.starttls} onCheckedChange={(v) => set("starttls", v)} label="STARTTLS" /> Use STARTTLS</div>
        </>}
        {stored && <label className="flex items-center gap-2 text-[13px] sm:col-span-2"><input type="checkbox" checked={f.clear} onChange={(e) => setF((p) => ({ ...p, clear: e.target.checked, secret: "" }))} /> Remove the stored {f.kind === "webhook" ? "signing secret" : "password"}</label>}
        <Field label="Minimum severity"><Select value={f.min_severity} onChange={(e) => set("min_severity", e.target.value)}>{SEVERITIES.map((s) => <option key={s}>{s}</option>)}</Select></Field>
        <div className="flex flex-col justify-end gap-2 text-[13px]">
          <div className="flex items-center gap-2"><Switch checked={f.enabled} onCheckedChange={(v) => set("enabled", v)} label="Enabled" /> Enabled</div>
          <div className="flex items-center gap-2"><Switch checked={f.send_for_simulated} onCheckedChange={(v) => set("send_for_simulated", v)} label="Send for simulated servers" /> Also send for simulated servers</div>
        </div>
      </div>
    </Dialog>
  );
}

function DeliveryLog({ channel, onClose }: { channel: NotificationChannel; onClose: () => void }) {
  const q = useQuery({ queryKey: ["notification-deliveries", channel.id], queryFn: () => get<Delivery[]>(`/notification-channels/${channel.id}/deliveries`, { limit: 100 }) });
  const columns: ColumnDef<Delivery, unknown>[] = [
    { header: "When", accessorKey: "created_at", cell: ({ getValue }) => <span title={dateTime(getValue() as string)}>{relTime(getValue() as string)}</span> },
    { header: "Event", accessorKey: "event" },
    { header: "Result", accessorKey: "status", cell: ({ getValue }) => <Badge tone={getValue() === "sent" ? "ok" : getValue() === "failed" ? "crit" : "neutral"}>{getValue() as string}</Badge> },
    { header: "Attempts", accessorKey: "attempts" },
    { header: "Code", accessorKey: "response_code", cell: ({ getValue }) => (getValue() as number | null) ?? "—" },
    { header: "Detail", accessorKey: "error", cell: ({ getValue }) => <span className="text-[12px] text-muted">{(getValue() as string | null) ?? ""}</span> },
  ];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-3xl" title={`Delivery log — ${channel.name}`} description="The last 100 notification attempts of this channel">
      <DataTable columns={columns} data={q.data} loading={q.isLoading} error={q.error} dense empty={<EmptyState icon={<History />} title="Nothing sent yet" />} />
    </Dialog>
  );
}

export function NotificationChannelsPage() {
  const qc = useQueryClient();
  const [edit, setEdit] = useState<NotificationChannel | "new" | null>(null);
  const [log, setLog] = useState<NotificationChannel | null>(null);
  const list = useQuery({ queryKey: ["notification-channels"], queryFn: () => get<NotificationChannel[]>("/notification-channels") });
  const test = useMutation({
    mutationFn: (c: NotificationChannel) => post<TestResult>(`/notification-channels/${c.id}/test`),
    onSuccess: (r, c) => {
      if (r.status === "sent") toast.success(`Test notification sent through ${c.name}${r.response_code ? ` (HTTP ${r.response_code})` : ""}`);
      else toast.error(`Test failed after ${r.attempts} attempt(s): ${r.error ?? r.status}`);
      qc.invalidateQueries({ queryKey: ["notification-deliveries", c.id] });
    },
    onError: (e) => toast.error(err(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/notification-channels/${id}`),
    onSuccess: () => { toast.success("Channel deleted"); qc.invalidateQueries({ queryKey: ["notification-channels"] }); },
    onError: (e) => toast.error(err(e)),
  });
  const columns: ColumnDef<NotificationChannel, unknown>[] = [
    { header: "Channel", accessorKey: "name", cell: ({ row: { original: c } }) => (
      <div className="flex items-center gap-2 font-medium">{c.kind === "webhook" ? <Webhook className="size-4 text-muted" /> : <Mail className="size-4 text-muted" />}{c.name}</div>
    ) },
    { header: "Target", id: "target", cell: ({ row: { original: c } }) => <span className="break-all font-mono text-[12px]">{c.kind === "webhook" ? c.config.url : (c.config.recipients ?? []).join(", ")}</span> },
    { header: "Secret", id: "secret", cell: ({ row: { original: c } }) => (c.kind === "webhook" ? c.has_signing_secret : c.has_smtp_password) ? <Badge tone="ok">stored (encrypted)</Badge> : <span className="text-muted">none</span> },
    { header: "Min. severity", accessorKey: "min_severity" },
    { header: "Simulated", accessorKey: "send_for_simulated", cell: ({ getValue }) => getValue() ? <Badge tone="warn">sends</Badge> : <span className="text-muted">skipped</span> },
    { header: "State", accessorKey: "enabled", cell: ({ getValue }) => getValue() ? <Badge tone="ok">enabled</Badge> : <Badge tone="neutral">disabled</Badge> },
    { header: "", id: "a", enableSorting: false, cell: ({ row: { original: c } }) => (
      <div className="flex justify-end gap-1">
        <Button size="sm" onClick={() => test.mutate(c)} disabled={test.isPending}><Send /> Test</Button>
        <Button size="sm" variant="ghost" aria-label={`Delivery log of ${c.name}`} onClick={() => setLog(c)}><History /></Button>
        <Button size="sm" variant="ghost" aria-label={`Edit ${c.name}`} onClick={() => setEdit(c)}><Pencil /></Button>
        <Button size="sm" variant="ghost" aria-label={`Delete ${c.name}`} onClick={() => remove.mutate(c.id)}><Trash2 /></Button>
      </div>
    ) },
  ];
  return (
    <div>
      <PageHeader title="Notification channels" description="Where alert notifications are delivered. Secrets are encrypted at rest and never shown again."
        crumbs={[{ label: "Platform" }, { label: "Notification channels" }]}
        actions={<Button variant="primary" size="sm" onClick={() => setEdit("new")}><Plus /> New channel</Button>} />
      <Card>
        <DataTable columns={columns} data={list.data} loading={list.isLoading} error={list.error} dense
          empty={<EmptyState icon={<Send />} title="No notification channels" action={<Button variant="primary" onClick={() => setEdit("new")}><Plus /> Add a webhook or email channel</Button>}>
            Alerts are shown in the app even without channels.
          </EmptyState>} />
      </Card>
      {edit && <ChannelDialog channel={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
      {log && <DeliveryLog channel={log} onClose={() => setLog(null)} />}
    </div>
  );
}
