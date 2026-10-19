import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Pencil, Plus, Radio, Send, Trash2, Webhook } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, del, get, post, put } from "@/api/client";
import { ConfirmDialog } from "@/components/confirm";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/ui/states";
import { dateTime, relTime } from "@/lib/format";

export type SiemTransport = "udp" | "tcp" | "tls";
export interface SiemDestination {
  id: string; name: string; kind: "syslog" | "webhook"; enabled: boolean;
  config: {
    host?: string; port?: number; transport?: SiemTransport; facility?: string | number; app_name?: string; sd_id?: string; ca_pem?: string;
    hostname?: string; timeout?: number; url?: string; headers?: Record<string, string>;
  };
  categories: string[]; has_signing_secret: boolean; cursor_id: number; exported_count: number; pending: number | null;
  last_success_at: string | null; last_error: string | null; last_error_at: string | null; created_by: string | null; created_at: string;
}

/** Categories that audit events are recorded with (app.services.audit callers). */
export const AUDIT_CATEGORIES = ["auth", "security", "admin", "platform", "alerting", "query", "backup", "collector", "operations", "report"];
/** app.services.siem.FACILITIES */
export const FACILITIES = ["audit", "auth", "authpriv", "user", "kern", "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7"];
const DEFAULT_PORT: Record<SiemTransport, number> = { udp: 514, tcp: 514, tls: 6514 };
const SECRETISH = /password|secret|token/i;
const err = (e: unknown) => (e instanceof ApiError ? e.message : String(e));

interface Form {
  name: string; kind: "syslog" | "webhook"; enabled: boolean; categories: string[]; extra_categories: string;
  host: string; port: string; transport: SiemTransport; facility: string; app_name: string; sd_id: string; ca_pem: string;
  url: string; headers: string;
  /** write-only: "" = keep the stored value unless `clear` is set */
  secret: string; clear: boolean; start_from: "now" | "beginning";
}

function toForm(d: SiemDestination | null): Form {
  const c = d?.config ?? {};
  const cats = d?.categories ?? [];
  return {
    name: d?.name ?? "", kind: d?.kind ?? "syslog", enabled: d?.enabled ?? true,
    categories: cats.filter((x) => AUDIT_CATEGORIES.includes(x)), extra_categories: cats.filter((x) => !AUDIT_CATEGORIES.includes(x)).join(", "),
    host: c.host ?? "", port: c.port ? String(c.port) : "", transport: c.transport ?? "udp", facility: c.facility !== undefined ? String(c.facility) : "audit",
    app_name: c.app_name ?? "", sd_id: c.sd_id ?? "", ca_pem: c.ca_pem ?? "",
    url: c.url ?? "", headers: Object.entries(c.headers ?? {}).map(([k, v]) => `${k}: ${v}`).join("\n"),
    secret: "", clear: false, start_from: "now",
  };
}

export function parseHeaders(text: string): Record<string, string> {
  return Object.fromEntries(text.split("\n").map((l) => l.split(/:(.*)/s).map((x) => (x ?? "").trim())).filter(([k, v]) => k && v).map(([k, v]) => [k, v]));
}

export function validateForm(f: Form): string | null {
  if (!f.name.trim()) return "Enter a name";
  if (f.kind === "syslog") {
    if (!f.host.trim()) return "Syslog destinations need a host";
    if (f.port && !(Number(f.port) >= 1 && Number(f.port) <= 65535)) return "Port must be 1–65535";
    if (f.ca_pem.trim() && !f.ca_pem.includes("BEGIN CERTIFICATE")) return "Paste the CA certificate in PEM format";
  } else {
    if (!/^https?:\/\/[^\s]+$/.test(f.url.trim())) return "Enter an http(s) URL";
    const h = parseHeaders(f.headers);
    if (Object.keys(h).some((k) => k.toLowerCase() === "authorization")) return "An Authorization header would be stored unencrypted: use the signing secret";
    if (Object.keys(h).some((k) => SECRETISH.test(k))) return "Header names suggesting a secret are refused: use the signing secret";
  }
  return null;
}

export function toBody(f: Form, isNew: boolean) {
  const categories = [...new Set([...f.categories, ...f.extra_categories.split(",").map((x) => x.trim()).filter(Boolean)])];
  const config = f.kind === "syslog"
    ? {
      host: f.host.trim(), transport: f.transport, ...(f.port ? { port: Number(f.port) } : {}),
      facility: /^\d+$/.test(f.facility) ? Number(f.facility) : f.facility,
      ...(f.app_name.trim() ? { app_name: f.app_name.trim() } : {}), ...(f.sd_id.trim() ? { sd_id: f.sd_id.trim() } : {}),
      ...(f.transport === "tls" && f.ca_pem.trim() ? { ca_pem: f.ca_pem.trim() } : {}),
    }
    : { url: f.url.trim(), headers: parseHeaders(f.headers) };
  return {
    name: f.name.trim(), kind: f.kind, enabled: f.enabled, config, categories,
    ...(f.kind === "webhook" ? (f.clear ? { signing_secret: "" } : f.secret ? { signing_secret: f.secret } : {}) : {}),
    ...(isNew ? { start_from: f.start_from } : {}),
  };
}

function DestinationDialog({ dest, onClose }: { dest: SiemDestination | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<Form>(() => toForm(dest));
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((p) => ({ ...p, [k]: v }));
  const save = useMutation({
    mutationFn: () => (dest ? put(`/platform/siem-destinations/${dest.id}`, toBody(f, false)) : post("/platform/siem-destinations", toBody(f, true))),
    onSuccess: () => { toast.success(dest ? "Destination saved" : "Destination added"); qc.invalidateQueries({ queryKey: ["siem-destinations"] }); onClose(); },
    onError: (e) => toast.error(err(e)),
  });
  const problem = validateForm(f);
  const toggle = (c: string, on: boolean) => set("categories", on ? [...f.categories, c] : f.categories.filter((x) => x !== c));
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-2xl" title={dest ? `Edit ${dest.name}` : "New audit forwarding destination"}
      description="Audit events are forwarded at least once, oldest first. Passwords and secrets are never part of an event."
      footer={<>
        {problem && <span className="mr-auto text-[12.5px] text-muted" role="status">{problem}</span>}
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!!problem || save.isPending} onClick={() => save.mutate()}>{dest ? "Save" : "Add destination"}</Button>
      </>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><Input value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="Corporate SIEM" /></Field>
        <Field label="Type" hint={dest ? "The type cannot be changed; add a new destination instead" : undefined}>
          <Select value={f.kind} disabled={!!dest} onChange={(e) => set("kind", e.target.value as Form["kind"])}>
            <option value="syslog">Syslog (RFC 5424)</option><option value="webhook">Webhook (JSON batches)</option>
          </Select>
        </Field>
        {f.kind === "syslog" ? <>
          <Field label="Host"><Input value={f.host} onChange={(e) => set("host", e.target.value)} placeholder="siem.example.com" /></Field>
          <Field label="Transport">
            <Select value={f.transport} onChange={(e) => set("transport", e.target.value as SiemTransport)}>
              <option value="udp">UDP (RFC 5426)</option><option value="tcp">TCP, octet counting (RFC 6587)</option><option value="tls">TLS (RFC 5425)</option>
            </Select>
          </Field>
          <Field label="Port" hint={`Empty = ${DEFAULT_PORT[f.transport]}`}><Input inputMode="numeric" value={f.port} onChange={(e) => set("port", e.target.value.replace(/\D/g, ""))} /></Field>
          <Field label="Facility">
            <Select value={f.facility} onChange={(e) => set("facility", e.target.value)}>
              {FACILITIES.map((x) => <option key={x} value={x}>{x === "audit" ? "audit (13, default)" : x}</option>)}
              {!FACILITIES.includes(f.facility) && <option value={f.facility}>{f.facility}</option>}
            </Select>
          </Field>
          <Field label="APP-NAME" hint="Empty = dbmanager"><Input value={f.app_name} onChange={(e) => set("app_name", e.target.value)} /></Field>
          <Field label="Structured-data ID" hint="Empty = dbm@32473; use name@<your enterprise number> if you have one"><Input value={f.sd_id} onChange={(e) => set("sd_id", e.target.value)} /></Field>
          {f.transport === "tls" && (
            <Field label="CA certificate (PEM)" className="sm:col-span-2" hint="Optional. Without it the system trust store verifies the receiver's certificate; verification is never turned off.">
              <Textarea rows={4} value={f.ca_pem} onChange={(e) => set("ca_pem", e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" />
            </Field>
          )}
          {f.transport === "udp" && <p className="text-[12px] text-muted sm:col-span-2">UDP gives no delivery confirmation and messages over 8 KiB are truncated: prefer TCP or TLS.</p>}
        </> : <>
          <Field label="URL" className="sm:col-span-2" hint="Receives POST {source, events:[…]}. Private and loopback addresses are refused unless DBM_WEBHOOK_ALLOW_PRIVATE=true">
            <Input value={f.url} onChange={(e) => set("url", e.target.value)} placeholder="https://siem.example.com/ingest/dbmanager" />
          </Field>
          <Field label="Extra headers (one per line, Name: value)" className="sm:col-span-2" hint="Not encrypted: do not put credentials here">
            <Textarea rows={2} className="font-mono text-[12.5px]" value={f.headers} onChange={(e) => set("headers", e.target.value)} placeholder="X-Source: dbmanager" />
          </Field>
          <Field label="Signing secret (HMAC-SHA256)" className="sm:col-span-2"
            hint={dest?.has_signing_secret ? "A secret is stored. Leave empty to keep it." : "Optional. Each batch is signed: X-DBM-Signature: sha256=<hex>"}>
            <Input type="password" autoComplete="new-password" value={f.secret} disabled={f.clear} onChange={(e) => set("secret", e.target.value)} placeholder={dest?.has_signing_secret ? "•••••••• stored" : ""} />
          </Field>
          {dest?.has_signing_secret && <label className="flex items-center gap-2 text-[13px] sm:col-span-2"><input type="checkbox" checked={f.clear} onChange={(e) => setF((p) => ({ ...p, clear: e.target.checked, secret: "" }))} /> Remove the stored signing secret</label>}
        </>}
        <fieldset className="rounded-md border border-border p-3 sm:col-span-2">
          <legend className="px-1 text-[12.5px] font-medium">Event categories</legend>
          <p className="mb-2 text-[12px] text-muted">None selected = every category.</p>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[13px]">
            {AUDIT_CATEGORIES.map((c) => <label key={c} className="flex items-center gap-1.5"><input type="checkbox" checked={f.categories.includes(c)} onChange={(e) => toggle(c, e.target.checked)} />{c}</label>)}
          </div>
          <Field label="Other categories (comma separated)" className="mt-2"><Input value={f.extra_categories} onChange={(e) => set("extra_categories", e.target.value)} /></Field>
        </fieldset>
        {!dest && (
          <Field label="Start forwarding from">
            <Select value={f.start_from} onChange={(e) => set("start_from", e.target.value as Form["start_from"])}>
              <option value="now">New events only</option><option value="beginning">The whole audit log</option>
            </Select>
          </Field>
        )}
        <div className="flex items-end gap-2 text-[13px]"><Switch checked={f.enabled} onCheckedChange={(v) => set("enabled", v)} label="Enabled" /> Enabled</div>
      </div>
    </Dialog>
  );
}

function target(d: SiemDestination): string {
  if (d.kind === "webhook") return d.config.url ?? "";
  const t = d.config.transport ?? "udp";
  return `${t}://${d.config.host}:${d.config.port ?? DEFAULT_PORT[t]}`;
}

export function SiemDestinationsPage() {
  const qc = useQueryClient();
  const [edit, setEdit] = useState<SiemDestination | "new" | null>(null);
  const [removing, setRemoving] = useState<SiemDestination | null>(null);
  const list = useQuery({ queryKey: ["siem-destinations"], queryFn: () => get<SiemDestination[]>("/platform/siem-destinations"), refetchInterval: 30_000 });
  const test = useMutation({
    mutationFn: (d: SiemDestination) => post<{ ok: boolean; error: string | null }>(`/platform/siem-destinations/${d.id}/test`),
    onSuccess: (r, d) => (r.ok ? toast.success(`Test event sent to ${d.name}`) : toast.error(`Test event to ${d.name} failed: ${r.error ?? "unknown error"}`)),
    onError: (e) => toast.error(err(e)),
  });
  const remove = useMutation({
    mutationFn: (d: SiemDestination) => del(`/platform/siem-destinations/${d.id}`),
    onSuccess: () => { toast.success("Destination removed"); setRemoving(null); qc.invalidateQueries({ queryKey: ["siem-destinations"] }); },
    onError: (e) => toast.error(err(e)),
  });
  const columns: ColumnDef<SiemDestination, unknown>[] = [
    { header: "Destination", accessorKey: "name", cell: ({ row: { original: d } }) => (
      <div>
        <div className="flex items-center gap-2 font-medium">{d.kind === "webhook" ? <Webhook className="size-4 text-muted" /> : <Radio className="size-4 text-muted" />}{d.name}</div>
        <span className="break-all font-mono text-[12px] text-muted">{target(d)}</span>
      </div>
    ) },
    { header: "Categories", accessorKey: "categories", cell: ({ row: { original: d } }) => d.categories.length ? <span className="text-[12px]">{d.categories.join(", ")}</span> : <span className="text-muted">all</span> },
    { header: "Backlog", accessorKey: "pending", cell: ({ row: { original: d } }) => d.pending === null ? "—" : <Badge tone={d.pending === 0 ? "ok" : d.last_error ? "crit" : "warn"}>{d.pending} pending</Badge> },
    { header: "Forwarded", accessorKey: "exported_count" },
    { header: "Last result", id: "last", cell: ({ row: { original: d } }) => (
      <div className="text-[12px]">
        {d.last_success_at ? <div title={dateTime(d.last_success_at)}>Success {relTime(d.last_success_at)}</div> : <div className="text-muted">Nothing sent yet</div>}
        {d.last_error && <div className="text-crit" title={d.last_error_at ? dateTime(d.last_error_at) : undefined}>Error {relTime(d.last_error_at)}: {d.last_error}</div>}
      </div>
    ) },
    { header: "State", accessorKey: "enabled", cell: ({ getValue }) => getValue() ? <Badge tone="ok">enabled</Badge> : <Badge tone="neutral">disabled</Badge> },
    { header: "", id: "a", enableSorting: false, cell: ({ row: { original: d } }) => (
      <div className="flex justify-end gap-1">
        <Button size="sm" onClick={() => test.mutate(d)} disabled={test.isPending}><Send /> Send test event</Button>
        <Button size="sm" variant="ghost" aria-label={`Edit ${d.name}`} onClick={() => setEdit(d)}><Pencil /></Button>
        <Button size="sm" variant="ghost" aria-label={`Delete ${d.name}`} onClick={() => setRemoving(d)}><Trash2 /></Button>
      </div>
    ) },
  ];
  return (
    <div>
      <PageHeader title="Audit forwarding" description="Forward audit events to a SIEM over syslog (RFC 5424) or a webhook. Each destination keeps its own position; a failed destination is retried from where it stopped."
        crumbs={[{ label: "Platform" }, { label: "Audit forwarding" }]}
        actions={<Button variant="primary" size="sm" onClick={() => setEdit("new")}><Plus /> New destination</Button>} />
      <Card>
        <DataTable columns={columns} data={list.data} loading={list.isLoading} error={list.error} dense
          empty={<EmptyState icon={<Radio />} title="No forwarding destinations" action={<Button variant="primary" onClick={() => setEdit("new")}><Plus /> Add a syslog or webhook destination</Button>}>
            The audit log is kept in DB Manager either way; forwarding sends a copy to your SIEM.
          </EmptyState>} />
      </Card>
      {edit && <DestinationDialog dest={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={`Remove ${removing?.name ?? ""}?`} confirmLabel="Remove destination" danger
        busy={remove.isPending} onConfirm={() => removing && remove.mutate(removing)}>
        Events stop being forwarded to this destination. The audit log itself is not changed.
      </ConfirmDialog>
    </div>
  );
}
