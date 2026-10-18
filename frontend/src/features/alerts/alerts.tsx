import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { BellRing, CalendarClock, Check, CheckCheck, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, del, get, post, put } from "@/api/client";
import type { Page, Server } from "@/api/types";
import { PageHeader } from "@/components/page";
import { EngineMark, SeverityBadge, SimulatedBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/ui/states";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/lib/auth";
import { dateTime, relTime } from "@/lib/format";

// ---- types --------------------------------------------------------------------------------------
export interface AlertItem {
  id: string; rule_id: string; rule_name: string; rule_kind: string; operator: string | null; server_id: string;
  server_name: string; engine: string; environment: string | null; status: "pending" | "firing" | "acknowledged" | "resolved";
  severity: string; title: string; message: string; metric: string | null; value: number | null; threshold: number | null;
  is_demo: boolean; suppressed: boolean; pending_since: string; fired_at: string | null; acknowledged_at: string | null;
  acknowledged_by: string | null; ack_comment: string | null; resolved_at: string | null; resolved_by: string | null;
}
export interface AlertRule {
  id: string; name: string; description: string | null; enabled: boolean; kind: "metric" | "health" | "collector";
  metric: string | null; operator: string | null; threshold: number | null; health_states: string[]; for_seconds: number;
  severity: string; scope_type: "all" | "environment" | "server"; scope_id: string | null; channel_ids: string[];
  notify_on_resolve: boolean;
}
interface Window { id: string; name: string; reason: string | null; scope_type: string; scope_id: string | null; starts_at: string; ends_at: string; state: string; created_by: string | null }
interface CatalogMetric { key: string; label: string; unit: string; description: string }
interface Channel { id: string; name: string; kind: string; enabled: boolean }
interface Env { id: string; name: string }

export interface AlertSummary { active: number; unacknowledged?: number; by_severity: Record<string, number>; visible: boolean }

export function useAlertSummary(enabled = true) {
  return useQuery({ queryKey: ["alert-summary"], queryFn: () => get<AlertSummary>("/alerts/summary"), refetchInterval: 60_000, enabled });
}

const STATUS_TONE: Record<string, "crit" | "warn" | "ok" | "neutral" | "info"> = { firing: "crit", acknowledged: "warn", pending: "info", resolved: "ok" };
const SEVERITIES = ["critical", "high", "medium", "low", "info"];
const OPERATORS = [">", ">=", "<", "<=", "==", "!="];

function scopeLabel(type: string, id: string | null, envs: Env[], servers: Server[]): string {
  if (type === "all") return "All servers";
  if (type === "environment") return `Environment: ${envs.find((e) => e.id === id)?.name ?? id}`;
  return `Server: ${servers.find((s) => s.id === id)?.name ?? id}`;
}

function useScopeData() {
  const envs = useQuery({ queryKey: ["environments"], queryFn: () => get<Env[]>("/environments"), staleTime: 300_000 });
  const servers = useQuery({ queryKey: ["servers-all-names"], queryFn: () => get<Page<Server>>("/servers", { page_size: 500, source: "all" }), staleTime: 60_000 });
  return { envs: envs.data ?? [], servers: servers.data?.items ?? [] };
}

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

// ---- alerts ----------------------------------------------------------------------------------------
const VIEWS: Record<string, string[]> = { active: ["firing", "acknowledged"], pending: ["pending"], resolved: ["resolved"] };

export function AlertsPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [view, setView] = useState("active");
  const [severity, setSeverity] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [ack, setAck] = useState<{ alert: AlertItem; action: "acknowledge" | "resolve" } | null>(null);
  const [comment, setComment] = useState("");
  const data = useQuery({
    queryKey: ["alerts", view, severity, q, page],
    queryFn: () => get<Page<AlertItem>>("/alerts", { status: VIEWS[view], severity: severity || undefined, q: q || undefined, page, page_size: 50, sort: view === "resolved" ? "-resolved_at" : undefined }),
    refetchInterval: 30_000,
    placeholderData: (p) => p,
  });
  const act = useMutation({
    mutationFn: (v: { id: string; action: string; comment: string }) => post<AlertItem>(`/alerts/${v.id}/${v.action}`, { comment: v.comment || null }),
    onSuccess: (_r, v) => {
      toast.success(v.action === "acknowledge" ? "Alert acknowledged" : "Alert resolved");
      setAck(null);
      setComment("");
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["alert-summary"] });
    },
    onError: (e) => toast.error(errorText(e)),
  });
  const canAck = can("alerts.acknowledge");
  const columns: ColumnDef<AlertItem, unknown>[] = [
    { header: "Severity", accessorKey: "severity", cell: ({ getValue }) => <SeverityBadge severity={getValue() as string} /> },
    { header: "Alert", accessorKey: "title", cell: ({ row: { original: a } }) => (
      <div className="min-w-[260px]">
        <div className="flex flex-wrap items-center gap-2 font-medium">{a.title}{a.suppressed && <Badge tone="neutral">Suppressed (maintenance)</Badge>}{a.is_demo && <SimulatedBadge />}</div>
        <div className="max-w-[520px] truncate text-[12px] text-muted">{a.message}</div>
      </div>
    ) },
    { header: "Server", accessorKey: "server_name", cell: ({ row: { original: a } }) => (
      <Link to={`/servers/${a.server_id}`} className="flex items-center gap-1.5 whitespace-nowrap hover:underline"><EngineMark engine={a.engine} />{a.server_name}</Link>
    ) },
    { header: "Status", accessorKey: "status", cell: ({ row: { original: a } }) => (
      <div><Badge tone={STATUS_TONE[a.status]}>{a.status}</Badge>{a.acknowledged_by && a.status === "acknowledged" && <div className="mt-0.5 text-2xs text-muted">by {a.acknowledged_by}</div>}</div>
    ) },
    { header: view === "resolved" ? "Resolved" : "Since", id: "since", accessorFn: (a) => (view === "resolved" ? a.resolved_at : a.fired_at ?? a.pending_since), cell: ({ getValue }) => <span title={dateTime(getValue() as string)} className="whitespace-nowrap text-[12px]">{relTime(getValue() as string)}</span> },
    ...(canAck && view !== "resolved" ? [{
      header: "", id: "actions", enableSorting: false,
      cell: ({ row: { original: a } }: { row: { original: AlertItem } }) => (
        <div className="flex justify-end gap-1">
          {a.status === "firing" && <Button size="sm" onClick={(e) => { e.stopPropagation(); setAck({ alert: a, action: "acknowledge" }); }}><Check /> Acknowledge</Button>}
          <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); setAck({ alert: a, action: "resolve" }); }}><CheckCheck /> Resolve</Button>
        </div>
      ),
    } as ColumnDef<AlertItem, unknown>] : []),
  ];
  return (
    <div>
      <PageHeader title="Alerts" description="Raised by alert rules against the latest collected values, server health and collector state."
        crumbs={[{ label: "Operations" }, { label: "Alerts" }]}
        actions={<Button size="sm" onClick={() => data.refetch()} disabled={data.isFetching}><RefreshCw className={data.isFetching ? "animate-spin" : ""} /> Refresh</Button>} />
      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <Tabs value={view} onValueChange={(v) => { setView(v); setPage(1); }}>
            <TabsList>
              <TabsTrigger value="active">Active</TabsTrigger>
              <TabsTrigger value="pending">Pending</TabsTrigger>
              <TabsTrigger value="resolved">Resolved</TabsTrigger>
            </TabsList>
          </Tabs>
          <Select aria-label="Severity" className="w-40" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }}>
            <option value="">All severities</option>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
          <Input aria-label="Search alerts" className="w-64" placeholder="Search alert or server" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        </div>
        <DataTable columns={columns} data={data.data?.items} loading={data.isLoading} error={data.error} onRetry={() => data.refetch()} dense
          manual={data.data ? { page, pageSize: 50, total: data.data.total, onPage: setPage } : undefined}
          empty={<EmptyState icon={<BellRing />} title={view === "active" ? "No active alerts" : view === "pending" ? "Nothing pending" : "No resolved alerts"}>
            {view === "active" ? <>Alerts appear here when a rule's condition holds. <Link className="text-accent hover:underline" to="/alerts/rules">Manage alert rules</Link></> : null}
          </EmptyState>} />
      </Card>
      {ack && (
        <Dialog open onOpenChange={(o) => !o && setAck(null)} title={ack.action === "acknowledge" ? "Acknowledge alert" : "Resolve alert"} description={ack.alert.title}
          footer={<>
            <Button variant="ghost" onClick={() => setAck(null)}>Cancel</Button>
            <Button variant="primary" disabled={act.isPending} onClick={() => act.mutate({ id: ack.alert.id, action: ack.action, comment })}>{ack.action === "acknowledge" ? "Acknowledge" : "Resolve"}</Button>
          </>}>
          <p className="mb-3 text-[13px] text-text-2">
            {ack.action === "acknowledge" ? "Acknowledged alerts stay active (no repeat notifications) until the condition clears." : "Resolving closes the alert now; it fires again if the condition still holds at the next evaluation."}
          </p>
          <Field label="Comment (optional)"><Textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={3} /></Field>
        </Dialog>
      )}
    </div>
  );
}

// ---- rules --------------------------------------------------------------------------------------------
const EMPTY_RULE: Omit<AlertRule, "id"> = {
  name: "", description: "", enabled: true, kind: "metric", metric: "connections.utilization", operator: ">=", threshold: 90,
  health_states: ["unavailable"], for_seconds: 300, severity: "high", scope_type: "all", scope_id: null, channel_ids: [], notify_on_resolve: true,
};

function RuleDialog({ rule, onClose }: { rule: AlertRule | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { canPlatform } = useAuth();
  const [f, setF] = useState<Omit<AlertRule, "id">>(rule ? { ...rule } : EMPTY_RULE);
  const [step, setStep] = useState(0);
  const catalog = useQuery({ queryKey: ["metric-catalog"], queryFn: () => get<CatalogMetric[]>("/metrics/catalog"), staleTime: 3_600_000 });
  const channels = useQuery({ queryKey: ["notification-channels"], queryFn: () => get<Channel[]>("/notification-channels"), enabled: canPlatform("channels.manage") || canPlatform("alerts.manage"), retry: false });
  const { envs, servers } = useScopeData();
  const metric = catalog.data?.find((m) => m.key === f.metric);
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((p) => ({ ...p, [k]: v }));
  const save = useMutation({
    mutationFn: () => {
      const body = { ...f, description: f.description || null, scope_id: f.scope_type === "all" ? null : f.scope_id };
      return rule ? put<AlertRule>(`/alert-rules/${rule.id}`, body) : post<AlertRule>("/alert-rules", body);
    },
    onSuccess: () => { toast.success(rule ? "Rule saved" : "Rule created"); qc.invalidateQueries({ queryKey: ["alert-rules"] }); onClose(); },
    onError: (e) => toast.error(errorText(e)),
  });
  const steps = ["Condition", "Scope", "Notify"];
  const valid = [
    f.name.trim().length > 0 && (f.kind !== "metric" || (!!f.metric && f.threshold !== null && !Number.isNaN(f.threshold))),
    f.scope_type === "all" || !!f.scope_id,
    true,
  ];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-2xl" title={rule ? `Edit rule ${rule.name}` : "New alert rule"}
      description={<span className="flex gap-3">{steps.map((s, i) => <span key={s} className={i === step ? "font-medium text-accent" : ""}>{i + 1}. {s}</span>)}</span>}
      footer={<>
        {step > 0 && <Button variant="ghost" onClick={() => setStep(step - 1)}>Back</Button>}
        {step < steps.length - 1
          ? <Button variant="primary" disabled={!valid[step]} onClick={() => setStep(step + 1)}>Continue</Button>
          : <Button variant="primary" disabled={!valid.every(Boolean) || save.isPending} onClick={() => save.mutate()}>{rule ? "Save rule" : "Create rule"}</Button>}
      </>}>
      {step === 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name" className="sm:col-span-2"><Input value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="Connection pool nearly exhausted" /></Field>
          <Field label="Rule type">
            <Select value={f.kind} onChange={(e) => set("kind", e.target.value as AlertRule["kind"])}>
              <option value="metric">Metric threshold</option>
              <option value="health">Server health state</option>
              <option value="collector">Collector failing</option>
            </Select>
          </Field>
          <Field label="Severity"><Select value={f.severity} onChange={(e) => set("severity", e.target.value)}>{SEVERITIES.map((s) => <option key={s}>{s}</option>)}</Select></Field>
          {f.kind === "metric" && <>
            <Field label="Metric" className="sm:col-span-2" hint={metric ? `${metric.description}${metric.unit ? ` (unit: ${metric.unit})` : ""}` : undefined}>
              <Select value={f.metric ?? ""} onChange={(e) => set("metric", e.target.value)}>
                {(catalog.data ?? []).map((m) => <option key={m.key} value={m.key}>{m.label} — {m.key}</option>)}
              </Select>
            </Field>
            <Field label="Operator"><Select value={f.operator ?? ">="} onChange={(e) => set("operator", e.target.value)}>{OPERATORS.map((o) => <option key={o}>{o}</option>)}</Select></Field>
            <Field label={`Threshold${metric?.unit ? ` (${metric.unit})` : ""}`}><Input inputMode="decimal" value={f.threshold ?? ""} onChange={(e) => set("threshold", e.target.value === "" ? null : Number(e.target.value))} /></Field>
          </>}
          {f.kind === "health" && (
            <Field label="Fire when health is" className="sm:col-span-2">
              <div className="flex flex-wrap gap-3 text-[13px]">
                {["unavailable", "critical", "warning", "unknown"].map((h) => (
                  <label key={h} className="flex items-center gap-1.5"><input type="checkbox" checked={f.health_states.includes(h)} onChange={(e) => set("health_states", e.target.checked ? [...f.health_states, h] : f.health_states.filter((x) => x !== h))} />{h}</label>
                ))}
              </div>
            </Field>
          )}
          {f.kind === "collector" && (
            <Field label="Consecutive failed collections" hint="Also fires while any collector component reports failed or unavailable" className="sm:col-span-2">
              <Input inputMode="numeric" value={f.threshold ?? 3} onChange={(e) => set("threshold", Number(e.target.value) || 3)} />
            </Field>
          )}
          <Field label="For (seconds)" hint="The condition must hold this long before the alert fires"><Input inputMode="numeric" value={f.for_seconds} onChange={(e) => set("for_seconds", Math.max(0, Number(e.target.value) || 0))} /></Field>
          <Field label="Description (optional)" className="sm:col-span-2"><Textarea rows={2} value={f.description ?? ""} onChange={(e) => set("description", e.target.value)} /></Field>
        </div>
      )}
      {step === 1 && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Applies to">
            <Select value={f.scope_type} onChange={(e) => setF((p) => ({ ...p, scope_type: e.target.value as AlertRule["scope_type"], scope_id: null }))}>
              <option value="all">All monitored servers</option><option value="environment">One environment</option><option value="server">One server</option>
            </Select>
          </Field>
          {f.scope_type === "environment" && <Field label="Environment"><Select value={f.scope_id ?? ""} onChange={(e) => set("scope_id", e.target.value || null)}><option value="">Choose…</option>{envs.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>}
          {f.scope_type === "server" && <Field label="Server"><Select value={f.scope_id ?? ""} onChange={(e) => set("scope_id", e.target.value || null)}><option value="">Choose…</option>{servers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></Field>}
          <div className="flex items-center gap-2 text-[13px] sm:col-span-2"><Switch checked={f.enabled} onCheckedChange={(v) => set("enabled", v)} label="Enabled" /> Rule enabled</div>
        </div>
      )}
      {step === 2 && (
        <div className="space-y-3 text-[13px]">
          {channels.error ? <p className="text-muted">You cannot list notification channels; the rule will only raise alerts in the app.</p> : (channels.data ?? []).length === 0 ? (
            <p className="text-muted">No notification channels yet. Alerts are still shown in the app; add a channel under Platform → Notification channels.</p>
          ) : (channels.data ?? []).map((c) => (
            <label key={c.id} className="flex items-center gap-2"><input type="checkbox" checked={f.channel_ids.includes(c.id)} onChange={(e) => set("channel_ids", e.target.checked ? [...f.channel_ids, c.id] : f.channel_ids.filter((x) => x !== c.id))} />
              {c.name} <Badge tone="neutral">{c.kind}</Badge>{!c.enabled && <Badge tone="warn">disabled</Badge>}</label>
          ))}
          <div className="flex items-center gap-2"><Switch checked={f.notify_on_resolve} onCheckedChange={(v) => set("notify_on_resolve", v)} label="Notify on resolve" /> Also notify when the alert resolves</div>
          <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[12.5px] text-muted">Summary: {f.kind === "metric" ? `${f.metric} ${f.operator} ${f.threshold}` : f.kind === "health" ? `health in ${f.health_states.join(", ")}` : "collector failing"} for {f.for_seconds}s on {scopeLabel(f.scope_type, f.scope_id, envs, servers).toLowerCase()} → {f.severity}</p>
        </div>
      )}
    </Dialog>
  );
}

export function AlertRulesPage() {
  const { canPlatform } = useAuth();
  const qc = useQueryClient();
  const [edit, setEdit] = useState<AlertRule | "new" | null>(null);
  const rules = useQuery({ queryKey: ["alert-rules"], queryFn: () => get<Page<AlertRule>>("/alert-rules", { page_size: 500 }) });
  const { envs, servers } = useScopeData();
  const manage = canPlatform("alerts.manage");
  const remove = useMutation({
    mutationFn: (id: string) => del(`/alert-rules/${id}`),
    onSuccess: () => { toast.success("Rule deleted"); qc.invalidateQueries({ queryKey: ["alert-rules"] }); },
    onError: (e) => toast.error(errorText(e)),
  });
  const columns = useMemo<ColumnDef<AlertRule, unknown>[]>(() => [
    { header: "Rule", accessorKey: "name", cell: ({ row: { original: r } }) => <div><div className="font-medium">{r.name}</div>{r.description && <div className="text-[12px] text-muted">{r.description}</div>}</div> },
    { header: "Condition", id: "cond", cell: ({ row: { original: r } }) => <code className="font-mono text-[12px]">{r.kind === "metric" ? `${r.metric} ${r.operator} ${r.threshold}` : r.kind === "health" ? `health ∈ {${r.health_states.join(", ")}}` : `collector failing (≥ ${r.threshold ?? 3})`}{r.for_seconds ? ` for ${r.for_seconds}s` : ""}</code> },
    { header: "Severity", accessorKey: "severity", cell: ({ getValue }) => <SeverityBadge severity={getValue() as string} /> },
    { header: "Scope", id: "scope", cell: ({ row: { original: r } }) => <span className="text-[12.5px]">{scopeLabel(r.scope_type, r.scope_id, envs, servers)}</span> },
    { header: "Channels", id: "ch", cell: ({ row: { original: r } }) => r.channel_ids.length || <span className="text-muted">in-app only</span> },
    { header: "State", accessorKey: "enabled", cell: ({ getValue }) => getValue() ? <Badge tone="ok">enabled</Badge> : <Badge tone="neutral">disabled</Badge> },
    ...(manage ? [{ header: "", id: "a", enableSorting: false, cell: ({ row: { original: r } }: { row: { original: AlertRule } }) => (
      <div className="flex justify-end gap-1">
        <Button size="sm" variant="ghost" aria-label={`Edit ${r.name}`} onClick={() => setEdit(r)}><Pencil /></Button>
        <Button size="sm" variant="ghost" aria-label={`Delete ${r.name}`} onClick={() => remove.mutate(r.id)}><Trash2 /></Button>
      </div>
    ) } as ColumnDef<AlertRule, unknown>] : []),
  ], [envs, servers, manage, remove]);
  return (
    <div>
      <PageHeader title="Alert rules" description="Threshold, health and collector rules evaluated every minute by the background scheduler."
        crumbs={[{ label: "Operations" }, { label: "Alerts", to: "/alerts" }, { label: "Rules" }]}
        actions={manage && <Button variant="primary" size="sm" onClick={() => setEdit("new")}><Plus /> New rule</Button>} />
      <Card>
        <DataTable columns={columns} data={rules.data?.items} loading={rules.isLoading} error={rules.error} dense
          empty={<EmptyState icon={<BellRing />} title="No alert rules yet" action={manage ? <Button variant="primary" onClick={() => setEdit("new")}><Plus /> Create the first rule</Button> : undefined}>
            For example: connection utilization ≥ 90 % for 5 minutes, or any production server unavailable.
          </EmptyState>} />
      </Card>
      {edit && <RuleDialog rule={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
    </div>
  );
}

// ---- maintenance windows ---------------------------------------------------------------------------
function toLocalInput(d: Date): string {
  const off = d.getTimezoneOffset() * 60_000;
  return new Date(d.getTime() - off).toISOString().slice(0, 16);
}

export function MaintenanceWindowsPage() {
  const { canPlatform } = useAuth();
  const qc = useQueryClient();
  const manage = canPlatform("alerts.manage");
  const [showEnded, setShowEnded] = useState(false);
  const [open, setOpen] = useState(false);
  const now = new Date();
  const [f, setF] = useState({ name: "", reason: "", scope_type: "all", scope_id: "", starts: toLocalInput(now), ends: toLocalInput(new Date(now.getTime() + 2 * 3_600_000)) });
  const { envs, servers } = useScopeData();
  const list = useQuery({ queryKey: ["maintenance-windows", showEnded], queryFn: () => get<Page<Window>>("/maintenance-windows", { include_ended: showEnded, page_size: 200 }) });
  const create = useMutation({
    mutationFn: () => post("/maintenance-windows", {
      name: f.name, reason: f.reason || null, scope_type: f.scope_type, scope_id: f.scope_type === "all" ? null : f.scope_id,
      starts_at: new Date(f.starts).toISOString(), ends_at: new Date(f.ends).toISOString(),
    }),
    onSuccess: () => { toast.success("Maintenance window scheduled"); setOpen(false); qc.invalidateQueries({ queryKey: ["maintenance-windows"] }); },
    onError: (e) => toast.error(errorText(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/maintenance-windows/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance-windows"] }),
    onError: (e) => toast.error(errorText(e)),
  });
  const columns: ColumnDef<Window, unknown>[] = [
    { header: "Window", accessorKey: "name", cell: ({ row: { original: w } }) => <div><div className="font-medium">{w.name}</div>{w.reason && <div className="text-[12px] text-muted">{w.reason}</div>}</div> },
    { header: "Scope", id: "scope", cell: ({ row: { original: w } }) => scopeLabel(w.scope_type, w.scope_id, envs, servers) },
    { header: "Starts", accessorKey: "starts_at", cell: ({ getValue }) => dateTime(getValue() as string) },
    { header: "Ends", accessorKey: "ends_at", cell: ({ getValue }) => dateTime(getValue() as string) },
    { header: "State", accessorKey: "state", cell: ({ getValue }) => <Badge tone={getValue() === "active" ? "warn" : getValue() === "scheduled" ? "info" : "neutral"}>{getValue() as string}</Badge> },
    ...(manage ? [{ header: "", id: "a", enableSorting: false, cell: ({ row: { original: w } }: { row: { original: Window } }) => <Button size="sm" variant="ghost" aria-label={`Remove ${w.name}`} onClick={() => remove.mutate(w.id)}><Trash2 /></Button> } as ColumnDef<Window, unknown>] : []),
  ];
  return (
    <div>
      <PageHeader title="Maintenance windows" description="Alerts on servers inside an active window are marked suppressed and send no notifications."
        crumbs={[{ label: "Operations" }, { label: "Alerts", to: "/alerts" }, { label: "Maintenance windows" }]}
        actions={<>
          <label className="flex items-center gap-2 text-[12.5px] text-muted"><Switch checked={showEnded} onCheckedChange={setShowEnded} label="Show ended" /> Show ended</label>
          {manage && <Button variant="primary" size="sm" onClick={() => setOpen(true)}><CalendarClock /> Schedule window</Button>}
        </>} />
      <Card>
        <DataTable columns={columns} data={list.data?.items} loading={list.isLoading} error={list.error} dense empty={<EmptyState icon={<CalendarClock />} title="No maintenance scheduled" />} />
      </Card>
      {open && (
        <Dialog open onOpenChange={setOpen} title="Schedule maintenance window"
          footer={<><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" disabled={!f.name || create.isPending || (f.scope_type !== "all" && !f.scope_id)} onClick={() => create.mutate()}>Schedule</Button></>}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name" className="sm:col-span-2"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Quarterly patching" /></Field>
            <Field label="Starts (local time)"><Input type="datetime-local" value={f.starts} onChange={(e) => setF({ ...f, starts: e.target.value })} /></Field>
            <Field label="Ends (local time)"><Input type="datetime-local" value={f.ends} onChange={(e) => setF({ ...f, ends: e.target.value })} /></Field>
            <Field label="Applies to"><Select value={f.scope_type} onChange={(e) => setF({ ...f, scope_type: e.target.value, scope_id: "" })}><option value="all">All servers</option><option value="environment">One environment</option><option value="server">One server</option></Select></Field>
            {f.scope_type === "environment" && <Field label="Environment"><Select value={f.scope_id} onChange={(e) => setF({ ...f, scope_id: e.target.value })}><option value="">Choose…</option>{envs.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>}
            {f.scope_type === "server" && <Field label="Server"><Select value={f.scope_id} onChange={(e) => setF({ ...f, scope_id: e.target.value })}><option value="">Choose…</option>{servers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></Field>}
            <Field label="Reason (optional)" className="sm:col-span-2"><Textarea rows={2} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
          </div>
        </Dialog>
      )}
    </div>
  );
}
