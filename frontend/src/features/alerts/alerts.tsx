import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { BellRing, CalendarClock, Check, CheckCheck, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, del, get, post, put } from "@/api/client";
import type { Page, Server } from "@/api/types";
import { PageHeader } from "@/components/page";
import { DatabaseEngineIcon, SeverityBadge, SimulatedBadge } from "@/components/status";
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
  notified_at?: string | null; notify_count?: number;
}
export interface AlertComment { id: string; author: string; body: string; created_at: string }
export interface AlertNotification { channel: string; event: string; status: string; attempts: number; error: string | null; created_at: string; next_attempt_at: string | null }
export interface AlertDetail extends AlertItem { comments: AlertComment[]; notifications: AlertNotification[] }
export type ScopeType = "all" | "environment" | "server" | "tag";
export interface AlertRule {
  id: string; name: string; description: string | null; enabled: boolean; kind: "metric" | "health" | "collector";
  metric: string | null; operator: string | null; threshold: number | null; health_states: string[]; for_seconds: number;
  severity: string; scope_type: ScopeType; scope_id: string | null; channel_ids: string[];
  notify_on_resolve: boolean; aggregation: "latest" | "avg" | "min" | "max" | "zscore"; window_seconds: number; baseline_days: number; renotify_minutes: number;
}
export interface MaintenanceWindow {
  id: string; name: string; reason: string | null; scope_type: ScopeType; scope_id: string | null; starts_at: string; ends_at: string;
  recurrence: "none" | "daily" | "weekly"; weekdays: number[]; time_zone: string; repeat_until: string | null;
  state: "active" | "scheduled" | "ended"; current_or_next_start: string | null; current_or_next_end: string | null; created_by: string | null;
}
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

export function scopeLabel(type: string, id: string | null, envs: Env[], servers: Server[]): string {
  if (type === "all") return "All servers";
  if (type === "environment") return `Environment: ${envs.find((e) => e.id === id)?.name ?? id}`;
  if (type === "tag") return `Tag: ${id}`;
  return `Server: ${servers.find((s) => s.id === id)?.name ?? id}`;
}

const TAG_RE = /^[A-Za-z0-9_.:/-]{1,64}(=[^\x00-\x1f]{0,64})?$/;
export const validTag = (v: string | null | undefined) => !!v && TAG_RE.test(v);
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function ScopeFields({ type, id, onType, onId, envs, servers, noun = "Applies to" }: {
  type: ScopeType; id: string | null; onType: (t: ScopeType) => void; onId: (v: string | null) => void; envs: Env[]; servers: Server[]; noun?: string;
}) {
  return <>
    <Field label={noun}>
      <Select value={type} onChange={(e) => onType(e.target.value as ScopeType)}>
        <option value="all">All monitored servers</option><option value="environment">One environment</option><option value="server">One server</option><option value="tag">Servers with a tag</option>
      </Select>
    </Field>
    {type === "environment" && <Field label="Environment"><Select value={id ?? ""} onChange={(e) => onId(e.target.value || null)}><option value="">Choose…</option>{envs.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>}
    {type === "server" && <Field label="Server"><Select value={id ?? ""} onChange={(e) => onId(e.target.value || null)}><option value="">Choose…</option>{servers.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select></Field>}
    {type === "tag" && <Field label="Tag" hint="key (any value) or key=value" error={id && !validTag(id) ? "Use key or key=value" : undefined}><Input value={id ?? ""} onChange={(e) => onId(e.target.value || null)} placeholder="team=payments" /></Field>}
  </>;
}

export function ruleCondition(r: Pick<AlertRule, "kind" | "metric" | "operator" | "threshold" | "health_states" | "for_seconds" | "aggregation" | "window_seconds"> & { baseline_days?: number }): string {
  let metric = r.aggregation && r.aggregation !== "latest" && r.window_seconds ? `${r.aggregation === "zscore" ? "avg" : r.aggregation}(${r.metric}, ${Math.round(r.window_seconds / 60)}m)` : r.metric;
  if (r.aggregation === "zscore") metric = `zscore(${metric}, ${r.baseline_days ?? 7}d baseline)`;
  const base = r.kind === "metric" ? `${metric} ${r.operator} ${r.threshold}` : r.kind === "health" ? `health ∈ {${r.health_states.join(", ")}}` : `collector failing (≥ ${r.threshold ?? 3})`;
  return base + (r.for_seconds ? ` for ${r.for_seconds}s` : "");
}

function useScopeData() {
  const envs = useQuery({ queryKey: ["environments"], queryFn: () => get<Env[]>("/environments"), staleTime: 300_000 });
  const servers = useQuery({ queryKey: ["servers-all-names"], queryFn: () => get<Page<Server>>("/servers", { page_size: 500, source: "all" }), staleTime: 60_000 });
  return { envs: envs.data ?? [], servers: servers.data?.items ?? [] };
}

function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

// ---- alert detail -----------------------------------------------------------------------------------
const NOTIFY_TONE: Record<string, "ok" | "crit" | "warn" | "neutral"> = { sent: "ok", failed: "crit", retrying: "warn", skipped: "neutral" };

export function AlertDetailDialog({ alertId, onClose }: { alertId: string; onClose: () => void }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [body, setBody] = useState("");
  const q = useQuery({ queryKey: ["alert", alertId], queryFn: () => get<AlertDetail>(`/alerts/${alertId}`) });
  const comment = useMutation({
    mutationFn: () => post<AlertComment>(`/alerts/${alertId}/comments`, { body }),
    onSuccess: () => { setBody(""); qc.invalidateQueries({ queryKey: ["alert", alertId] }); },
    onError: (e) => toast.error(errorText(e)),
  });
  const a = q.data;
  const timeline: [string, string | null | undefined, string?][] = a ? [
    ["Pending since", a.pending_since],
    ["Fired", a.fired_at],
    ["Last notified", a.notified_at, a.notify_count ? `${a.notify_count} notification${a.notify_count === 1 ? "" : "s"}` : a.suppressed ? "suppressed by maintenance" : undefined],
    ["Acknowledged", a.acknowledged_at, a.acknowledged_by ? `by ${a.acknowledged_by}` : undefined],
    ["Resolved", a.resolved_at, a.resolved_by ? `by ${a.resolved_by}` : undefined],
  ] : [];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-3xl" title={a?.title ?? "Alert"} description={a ? `${a.rule_name} · ${a.server_name}` : undefined}>
      {q.isLoading && <p className="text-[13px] text-muted">Loading…</p>}
      {q.error && <p className="text-[13px] text-crit">{errorText(q.error)}</p>}
      {a && (
        <div className="space-y-4 text-[13px]">
          <div className="flex flex-wrap items-center gap-2"><SeverityBadge severity={a.severity} /><Badge tone={STATUS_TONE[a.status]}>{a.status}</Badge>{a.suppressed && <Badge tone="neutral">Suppressed (maintenance)</Badge>}{a.is_demo && <SimulatedBadge />}</div>
          <p className="font-mono text-[12.5px]">{a.message}</p>
          <section aria-label="Timeline">
            <h3 className="mb-1.5 text-[12.5px] font-semibold">Timeline</h3>
            <dl className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1">
              {timeline.filter(([, v]) => v).map(([k, v, extra]) => <Fragment key={k}><dt className="text-muted">{k}</dt><dd>{dateTime(v as string)}{extra ? <span className="text-muted"> — {extra}</span> : null}</dd></Fragment>)}
            </dl>
          </section>
          <section aria-label="Comments">
            <h3 className="mb-1.5 text-[12.5px] font-semibold">Comments</h3>
            {a.comments.length === 0 ? <p className="text-muted">No comments yet.</p> : (
              <ul className="space-y-2">{a.comments.map((c) => <li key={c.id} className="rounded-md border border-border bg-surface-2 px-3 py-2"><div className="text-2xs text-muted">{c.author} · {dateTime(c.created_at)}</div><div className="whitespace-pre-wrap">{c.body}</div></li>)}</ul>
            )}
            {can("alerts.acknowledge") && (
              <div className="mt-2 flex items-end gap-2">
                <Field label="Add a comment" className="flex-1"><Textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
                <Button variant="primary" disabled={!body.trim() || comment.isPending} onClick={() => comment.mutate()}>Comment</Button>
              </div>
            )}
          </section>
          <section aria-label="Notifications">
            <h3 className="mb-1.5 text-[12.5px] font-semibold">Notifications</h3>
            {a.notifications.length === 0 ? <p className="text-muted">No notifications for this alert.</p> : (
              <table className="w-full text-[12.5px]">
                <thead><tr className="border-b border-border text-left text-muted"><th className="py-1 font-medium">When</th><th className="font-medium">Channel</th><th className="font-medium">Event</th><th className="font-medium">Result</th><th className="font-medium">Attempts</th><th className="font-medium">Detail</th></tr></thead>
                <tbody>{a.notifications.map((n, i) => (
                  <tr key={i} className="border-b border-border/60">
                    <td className="py-1 whitespace-nowrap">{relTime(n.created_at)}</td><td>{n.channel}</td><td>{n.event}</td>
                    <td><Badge tone={NOTIFY_TONE[n.status] ?? "neutral"}>{n.status}</Badge></td><td>{n.attempts}</td>
                    <td className="text-muted">{n.error ?? ""}{n.next_attempt_at ? ` — next attempt ${dateTime(n.next_attempt_at)}` : ""}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </section>
        </div>
      )}
    </Dialog>
  );
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
  const [detail, setDetail] = useState<string | null>(null);
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
        <div className="flex flex-wrap items-center gap-2 font-medium"><button type="button" className="text-left hover:underline" onClick={() => setDetail(a.id)}>{a.title}</button>{a.suppressed && <Badge tone="neutral">Suppressed (maintenance)</Badge>}{a.is_demo && <SimulatedBadge />}</div>
        <div className="max-w-[520px] truncate text-[12px] text-muted">{a.message}</div>
      </div>
    ) },
    { header: "Server", accessorKey: "server_name", cell: ({ row: { original: a } }) => (
      <Link to={`/servers/${a.server_id}`} className="flex items-center gap-1.5 whitespace-nowrap hover:underline"><DatabaseEngineIcon engine={a.engine} />{a.server_name}</Link>
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
      <PageHeader title="Alerts" description="Raised by alert rules on collected values or metric history, server health and collector state."
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
      {detail && <AlertDetailDialog alertId={detail} onClose={() => setDetail(null)} />}
      {ack && (
        <Dialog open onOpenChange={(o) => !o && setAck(null)} title={ack.action === "acknowledge" ? "Acknowledge alert" : "Resolve alert"} description={ack.alert.title}
          footer={<>
            <Button variant="ghost" onClick={() => setAck(null)}>Cancel</Button>
            <Button variant="primary" disabled={act.isPending} onClick={() => act.mutate({ id: ack.alert.id, action: ack.action, comment })}>{ack.action === "acknowledge" ? "Acknowledge" : "Resolve"}</Button>
          </>}>
          <p className="mb-3 text-[13px] text-text-2">
            {ack.action === "acknowledge" ? "Acknowledged alerts stay active until the condition clears, without further notifications or re-notification." : "Resolving closes the alert now; it fires again if the condition still holds at the next evaluation."}
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
  aggregation: "latest", window_seconds: 0, baseline_days: 7, renotify_minutes: 0,
};

function RuleDialog({ rule, onClose }: { rule: AlertRule | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { canPlatform } = useAuth();
  const [f, setF] = useState<Omit<AlertRule, "id">>(rule ? { ...EMPTY_RULE, ...rule } : EMPTY_RULE);
  const [step, setStep] = useState(0);
  const catalog = useQuery({ queryKey: ["metric-catalog"], queryFn: () => get<CatalogMetric[]>("/metrics/catalog"), staleTime: 3_600_000 });
  const channels = useQuery({ queryKey: ["notification-channels"], queryFn: () => get<Channel[]>("/notification-channels"), enabled: canPlatform("channels.manage") || canPlatform("alerts.manage"), retry: false });
  const { envs, servers } = useScopeData();
  const metric = catalog.data?.find((m) => m.key === f.metric);
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((p) => ({ ...p, [k]: v }));
  const save = useMutation({
    mutationFn: () => {
      const ranged = f.kind === "metric" && f.aggregation !== "latest" && !(f.aggregation === "zscore" && f.window_seconds === 0);
      const body = {
        ...f, description: f.description || null, scope_id: f.scope_type === "all" ? null : f.scope_id,
        aggregation: f.kind === "metric" ? f.aggregation : "latest", window_seconds: ranged ? f.window_seconds : 0,
      };
      return rule ? put<AlertRule>(`/alert-rules/${rule.id}`, body) : post<AlertRule>("/alert-rules", body);
    },
    onSuccess: () => { toast.success(rule ? "Rule saved" : "Rule created"); qc.invalidateQueries({ queryKey: ["alert-rules"] }); onClose(); },
    onError: (e) => toast.error(errorText(e)),
  });
  const steps = ["Condition", "Scope", "Notify"];
  const valid = [
    f.name.trim().length > 0 && (f.kind !== "metric" || (!!f.metric && f.threshold !== null && !Number.isNaN(f.threshold)
      && (f.aggregation === "latest" || f.window_seconds >= 60 || (f.aggregation === "zscore" && f.window_seconds === 0))
      && (f.aggregation !== "zscore" || (f.baseline_days >= 1 && f.baseline_days <= 90 && f.window_seconds < f.baseline_days * 86400)))),
    f.scope_type === "all" || (f.scope_type === "tag" ? validTag(f.scope_id) : !!f.scope_id),
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
            <Field label={f.aggregation === "zscore" ? "Threshold (z-score)" : `Threshold${metric?.unit ? ` (${metric.unit})` : ""}`}
              hint={f.aggregation === "zscore" ? "Standard deviations from the server's own baseline: > 3 fires on unusually high values, < -3 on unusually low ones" : undefined}><Input inputMode="decimal" value={f.threshold ?? ""} onChange={(e) => set("threshold", e.target.value === "" ? null : Number(e.target.value))} /></Field>
            <Field label="Evaluate" hint="Latest collected value, or a summary of the metric history in the time-series database">
              <Select value={f.aggregation} onChange={(e) => {
                const a = e.target.value as AlertRule["aggregation"];
                setF((p) => ({
                  ...p, aggregation: a,
                  window_seconds: a === "latest" ? 0 : a === "zscore" ? p.window_seconds : Math.max(p.window_seconds, 600),
                  threshold: a === "zscore" && p.aggregation !== "zscore" ? 3 : p.threshold, operator: a === "zscore" && p.aggregation !== "zscore" ? ">" : p.operator,
                }));
              }}>
                <option value="latest">Latest value</option><option value="avg">Average over a window</option><option value="min">Minimum over a window</option><option value="max">Maximum over a window</option>
                <option value="zscore">Deviation from baseline (z-score)</option>
              </Select>
            </Field>
            {f.aggregation === "zscore" && (
              <Field label="Baseline (days)" hint="Mean and standard deviation of the server's own history; needs at least 60 samples"
                error={f.baseline_days < 1 || f.baseline_days > 90 ? "1 to 90 days" : f.window_seconds >= f.baseline_days * 86400 ? "Must be longer than the window" : undefined}>
                <Input inputMode="numeric" value={f.baseline_days} onChange={(e) => set("baseline_days", Number(e.target.value.replace(/\D/g, "")) || 0)} />
              </Field>
            )}
            {f.aggregation !== "latest" && (
              <Field label={f.aggregation === "zscore" ? "Compare (minutes, 0 = latest value)" : "Window (minutes)"} hint={f.aggregation === "zscore" ? "0 compares the latest value; a window compares its average" : "At least 1 minute"}
                error={f.window_seconds < 60 && !(f.aggregation === "zscore" && f.window_seconds === 0) ? "At least 1 minute" : undefined}>
                <Input inputMode="numeric" value={Math.round(f.window_seconds / 60)} onChange={(e) => set("window_seconds", Math.max(0, Number(e.target.value.replace(/\D/g, "")) || 0) * 60)} />
              </Field>
            )}
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
          <ScopeFields type={f.scope_type} id={f.scope_id} envs={envs} servers={servers}
            onType={(v) => setF((p) => ({ ...p, scope_type: v, scope_id: null }))} onId={(v) => set("scope_id", v)} />
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
          <Field label="Re-notify every (minutes, 0 = never)" hint="Repeats the firing notification until someone acknowledges or the alert resolves">
            <Input inputMode="numeric" value={f.renotify_minutes} onChange={(e) => set("renotify_minutes", Math.max(0, Number(e.target.value.replace(/\D/g, "")) || 0))} />
          </Field>
          {f.kind === "metric" && f.aggregation === "zscore" && f.scope_type === "server" && f.scope_id && f.metric && (
            <BaselinePreview serverId={f.scope_id} metric={f.metric} days={f.baseline_days} threshold={f.threshold} />
          )}
          <p className="rounded-md border border-border bg-surface-2 px-3 py-2 text-[12.5px] text-muted">Summary: {ruleCondition(f)} on {scopeLabel(f.scope_type, f.scope_id, envs, servers).toLowerCase()} → {f.severity}{f.renotify_minutes ? `, re-notify every ${f.renotify_minutes} min` : ""}</p>
        </div>
      )}
    </Dialog>
  );
}

type Baseline = { sufficient_data: boolean; samples: number; reason?: string; mean?: number; stddev?: number; current?: number | null; z_score?: number | null };

/** What the z-score rule would see for one server right now (the baseline of its own history). */
export function BaselinePreview({ serverId, metric, days, threshold }: { serverId: string; metric: string; days: number; threshold: number | null }) {
  const q = useQuery({
    queryKey: ["baseline", serverId, metric, days],
    queryFn: () => get<Baseline>(`/servers/${serverId}/metrics/baseline`, { metric, days }),
    enabled: days >= 1 && days <= 90, retry: false,
  });
  if (q.isLoading) return <p className="text-[12.5px] text-muted">Loading the baseline…</p>;
  if (q.error) return <p className="text-[12.5px] text-muted">Baseline preview unavailable: {errorText(q.error)}</p>;
  const b = q.data;
  if (!b) return null;
  if (!b.sufficient_data) return <p className="rounded-md border border-border px-3 py-2 text-[12.5px] text-warn" role="status">No baseline yet: {b.reason}. The rule reports “no data” until enough history exists.</p>;
  const fmt = (v: number | null | undefined) => (v === null || v === undefined ? "—" : Number(v.toPrecision(4)).toLocaleString());
  const fireAt = threshold !== null && b.mean !== undefined && b.stddev !== undefined ? b.mean + threshold * b.stddev : null;
  return (
    <div className="rounded-md border border-border px-3 py-2 text-[12.5px]" data-testid="baseline-preview">
      <div className="font-medium">Baseline over {days} days ({b.samples.toLocaleString()} samples)</div>
      <div className="text-text-2">mean {fmt(b.mean)} ± {fmt(b.stddev)} · current {fmt(b.current)} (z = {fmt(b.z_score)})
        {fireAt !== null && <> · with threshold {threshold} the rule fires around {fmt(fireAt)}</>}</div>
    </div>
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
    { header: "Condition", id: "cond", cell: ({ row: { original: r } }) => <code className="font-mono text-[12px]">{ruleCondition(r)}</code> },
    { header: "Severity", accessorKey: "severity", cell: ({ getValue }) => <SeverityBadge severity={getValue() as string} /> },
    { header: "Scope", id: "scope", cell: ({ row: { original: r } }) => <span className="text-[12.5px]">{scopeLabel(r.scope_type, r.scope_id, envs, servers)}</span> },
    { header: "Channels", id: "ch", cell: ({ row: { original: r } }) => <span>{r.channel_ids.length || <span className="text-muted">routing only</span>}{r.renotify_minutes ? <div className="text-2xs text-muted">re-notify {r.renotify_minutes} min</div> : null}</span> },
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

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function zones(current: string): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  const all = intl.supportedValuesOf ? intl.supportedValuesOf("timeZone") : [];
  return Array.from(new Set(["UTC", current, ...all]));
}

export function recurrenceLabel(w: Pick<MaintenanceWindow, "recurrence" | "weekdays" | "time_zone">): string {
  if (w.recurrence === "daily") return `Daily (${w.time_zone})`;
  if (w.recurrence === "weekly") return `Weekly on ${w.weekdays.map((d) => WEEKDAYS[d - 1]).join(", ")} (${w.time_zone})`;
  return "Once";
}

interface WindowForm {
  name: string; reason: string; scope_type: ScopeType; scope_id: string | null; starts: string; ends: string;
  recurrence: MaintenanceWindow["recurrence"]; weekdays: number[]; time_zone: string; until: string;
}

function windowForm(w: MaintenanceWindow | null): WindowForm {
  const now = new Date();
  return w ? {
    name: w.name, reason: w.reason ?? "", scope_type: w.scope_type, scope_id: w.scope_id, starts: toLocalInput(new Date(w.starts_at)),
    ends: toLocalInput(new Date(w.ends_at)), recurrence: w.recurrence, weekdays: w.weekdays, time_zone: w.time_zone,
    until: w.repeat_until ? toLocalInput(new Date(w.repeat_until)) : "",
  } : {
    name: "", reason: "", scope_type: "all", scope_id: null, starts: toLocalInput(now), ends: toLocalInput(new Date(now.getTime() + 2 * 3_600_000)),
    recurrence: "none", weekdays: [], time_zone: browserTimeZone(), until: "",
  };
}

function WindowDialog({ window: w, onClose }: { window: MaintenanceWindow | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { envs, servers } = useScopeData();
  const [f, setF] = useState<WindowForm>(() => windowForm(w));
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: f.name, reason: f.reason || null, scope_type: f.scope_type, scope_id: f.scope_type === "all" ? null : f.scope_id,
        starts_at: new Date(f.starts).toISOString(), ends_at: new Date(f.ends).toISOString(),
        recurrence: f.recurrence, weekdays: f.recurrence === "weekly" ? f.weekdays : [], time_zone: f.time_zone,
        repeat_until: f.recurrence !== "none" && f.until ? new Date(f.until).toISOString() : null,
      };
      return w ? put(`/maintenance-windows/${w.id}`, body) : post("/maintenance-windows", body);
    },
    onSuccess: () => { toast.success(w ? "Maintenance window saved" : "Maintenance window scheduled"); onClose(); qc.invalidateQueries({ queryKey: ["maintenance-windows"] }); },
    onError: (e) => toast.error(errorText(e)),
  });
  const scopeOk = f.scope_type === "all" || (f.scope_type === "tag" ? validTag(f.scope_id) : !!f.scope_id);
  const valid = !!f.name && scopeOk && new Date(f.ends) > new Date(f.starts) && (f.recurrence !== "weekly" || f.weekdays.length > 0);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-2xl" title={w ? `Edit ${w.name}` : "Schedule maintenance window"}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!valid || save.isPending} onClick={() => save.mutate()}>{w ? "Save" : "Schedule"}</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" className="sm:col-span-2"><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Quarterly patching" /></Field>
        <Field label={f.recurrence === "none" ? "Starts (local time)" : "First occurrence starts (local time)"}><Input type="datetime-local" value={f.starts} onChange={(e) => setF({ ...f, starts: e.target.value })} /></Field>
        <Field label={f.recurrence === "none" ? "Ends (local time)" : "First occurrence ends (local time)"}><Input type="datetime-local" value={f.ends} onChange={(e) => setF({ ...f, ends: e.target.value })} /></Field>
        <Field label="Repeats">
          <Select value={f.recurrence} onChange={(e) => {
            const recurrence = e.target.value as WindowForm["recurrence"];
            const iso = ((new Date(f.starts).getDay() + 6) % 7) + 1;
            setF({ ...f, recurrence, weekdays: recurrence === "weekly" && !f.weekdays.length ? [iso] : f.weekdays });
          }}>
            <option value="none">Does not repeat</option><option value="daily">Every day</option><option value="weekly">Every week</option>
          </Select>
        </Field>
        <Field label="Time zone" hint="Recurring windows keep their local time across daylight-saving changes">
          <Select value={f.time_zone} onChange={(e) => setF({ ...f, time_zone: e.target.value })}>{zones(f.time_zone).map((z) => <option key={z} value={z}>{z}</option>)}</Select>
        </Field>
        {f.recurrence === "weekly" && (
          <fieldset className="sm:col-span-2">
            <legend className="mb-1 text-[12.5px] font-medium">On</legend>
            <div className="flex flex-wrap gap-3 text-[13px]">
              {WEEKDAYS.map((d, i) => (
                <label key={d} className="flex items-center gap-1.5"><input type="checkbox" checked={f.weekdays.includes(i + 1)}
                  onChange={(e) => setF({ ...f, weekdays: e.target.checked ? [...f.weekdays, i + 1].sort() : f.weekdays.filter((x) => x !== i + 1) })} />{d}</label>
              ))}
            </div>
          </fieldset>
        )}
        {f.recurrence !== "none" && <Field label="Repeat until (optional, local time)"><Input type="datetime-local" value={f.until} onChange={(e) => setF({ ...f, until: e.target.value })} /></Field>}
        <ScopeFields type={f.scope_type} id={f.scope_id} envs={envs} servers={servers}
          onType={(v) => setF({ ...f, scope_type: v, scope_id: null })} onId={(v) => setF({ ...f, scope_id: v })} />
        <Field label="Reason (optional)" className="sm:col-span-2"><Textarea rows={2} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
      </div>
    </Dialog>
  );
}

const WINDOW_TONE: Record<string, "warn" | "info" | "neutral"> = { active: "warn", scheduled: "info", ended: "neutral" };

export function windowWhen(w: MaintenanceWindow): string {
  if (!w.current_or_next_start) return `${dateTime(w.starts_at)} – ${dateTime(w.ends_at)}`;
  return `${dateTime(w.current_or_next_start)} – ${dateTime(w.current_or_next_end)}`;
}

export function MaintenanceWindowsPage() {
  const { canPlatform } = useAuth();
  const qc = useQueryClient();
  const manage = canPlatform("alerts.manage");
  const [showEnded, setShowEnded] = useState(false);
  const [edit, setEdit] = useState<MaintenanceWindow | "new" | null>(null);
  const { envs, servers } = useScopeData();
  const list = useQuery({ queryKey: ["maintenance-windows", showEnded], queryFn: () => get<Page<MaintenanceWindow>>("/maintenance-windows", { include_ended: showEnded, page_size: 200 }) });
  const remove = useMutation({
    mutationFn: (id: string) => del(`/maintenance-windows/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance-windows"] }),
    onError: (e) => toast.error(errorText(e)),
  });
  const columns: ColumnDef<MaintenanceWindow, unknown>[] = [
    { header: "Window", accessorKey: "name", cell: ({ row: { original: w } }) => <div><div className="font-medium">{w.name}</div>{w.reason && <div className="text-[12px] text-muted">{w.reason}</div>}</div> },
    { header: "Scope", id: "scope", cell: ({ row: { original: w } }) => scopeLabel(w.scope_type, w.scope_id, envs, servers) },
    { header: "Repeats", id: "rec", cell: ({ row: { original: w } }) => <span className="text-[12.5px]">{recurrenceLabel(w)}{w.repeat_until ? <div className="text-2xs text-muted">until {dateTime(w.repeat_until)}</div> : null}</span> },
    { header: "Current / next occurrence", id: "when", cell: ({ row: { original: w } }) => <span className="whitespace-nowrap text-[12.5px]">{windowWhen(w)}</span> },
    { header: "State", accessorKey: "state", cell: ({ getValue }) => <Badge tone={WINDOW_TONE[getValue() as string] ?? "neutral"}>{getValue() as string}</Badge> },
    ...(manage ? [{ header: "", id: "a", enableSorting: false, cell: ({ row: { original: w } }: { row: { original: MaintenanceWindow } }) => (
      <div className="flex justify-end gap-1">
        <Button size="sm" variant="ghost" aria-label={`Edit ${w.name}`} onClick={() => setEdit(w)}><Pencil /></Button>
        <Button size="sm" variant="ghost" aria-label={`Remove ${w.name}`} onClick={() => remove.mutate(w.id)}><Trash2 /></Button>
      </div>
    ) } as ColumnDef<MaintenanceWindow, unknown>] : []),
  ];
  return (
    <div>
      <PageHeader title="Maintenance windows" description="Alerts on servers inside an active window are marked suppressed; if still firing when the window ends, they are notified then."
        crumbs={[{ label: "Operations" }, { label: "Alerts", to: "/alerts" }, { label: "Maintenance windows" }]}
        actions={<>
          <label className="flex items-center gap-2 text-[12.5px] text-muted"><Switch checked={showEnded} onCheckedChange={setShowEnded} label="Show ended" /> Show ended</label>
          {manage && <Button variant="primary" size="sm" onClick={() => setEdit("new")}><CalendarClock /> Schedule window</Button>}
        </>} />
      <Card>
        <DataTable columns={columns} data={list.data?.items} loading={list.isLoading} error={list.error} dense empty={<EmptyState icon={<CalendarClock />} title="No maintenance scheduled" />} />
      </Card>
      {edit && <WindowDialog window={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
    </div>
  );
}
