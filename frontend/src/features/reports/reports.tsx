import { useQuery } from "@tanstack/react-query";
import {
  Activity, CalendarClock, CalendarPlus, ClipboardList, Download, Eye, FileText, Gauge, HardDrive, KeyRound, Lightbulb, LineChart,
  Loader2, type LucideIcon, Server as ServerIcon, ShieldAlert, ShieldCheck, Sheet, TrendingDown, Timer,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { download, get } from "@/api/client";
import type { Environment, Page, Server } from "@/api/types";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/lib/auth";
import { ENGINES } from "@/lib/engines";
import { cn } from "@/lib/utils";
import { errMsg, GROUPS, type Preview, queryOf, type Report, type ReportParams, SEVERITIES, STATUSES } from "./api";
import { ScheduleWizard, SchedulesTab } from "./schedules";

const ICONS: Record<string, LucideIcon> = {
  "fleet-health": Activity, inventory: ServerIcon, capacity: HardDrive, "capacity-forecast": LineChart, performance: Gauge,
  "query-statistics": Sheet, "slow-queries": Timer, "query-regressions": TrendingDown, recommendations: Lightbulb,
  "security-findings": ShieldAlert, "security-posture": ShieldCheck, "privileged-users": KeyRound, "audit-activity": ClipboardList,
};

export function reportIcon(key: string): LucideIcon {
  return ICONS[key] ?? FileText;
}

export const defaultParams = (): ReportParams => ({ window_hours: 24, statuses: [], min_severity: "", scope: "server" });

/** The parameter fields of a report: scope filters for every report, plus the ones it declares. */
export function ParamFields({ report, value, onChange }: { report: Report; value: ReportParams; onChange: (p: ReportParams) => void }) {
  const set = (p: Partial<ReportParams>) => onChange({ ...value, ...p });
  const envs = useQuery({ queryKey: ["environments"], queryFn: () => get<Environment[]>("/environments") });
  const servers = useQuery({
    queryKey: ["report-servers"], queryFn: () => get<Page<Server>>("/servers", { page_size: 500, sort: "name", source: "all" }), select: (p) => p.items,
  });
  const has = (k: Report["params"][number]) => report.params.includes(k);
  const shown = (servers.data ?? []).filter((s) => (!value.environment_id || s.environment?.id === value.environment_id) && (!value.engine || s.engine === value.engine));
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {!report.params.length && <p className="text-[12.5px] text-muted sm:col-span-3">This report has no parameters.</p>}
      {has("environment_id") && <Field label="Environment">
        <Select value={value.environment_id ?? ""} onChange={(e) => set({ environment_id: e.target.value || undefined, server_id: undefined })}>
          <option value="">All environments</option>
          {envs.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
        </Select>
      </Field>}
      {has("engine") && <Field label="Engine">
        <Select value={value.engine ?? ""} onChange={(e) => set({ engine: e.target.value || undefined, server_id: undefined })}>
          <option value="">All engines</option>
          {Object.entries(ENGINES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </Select>
      </Field>}
      {has("server_id") && <Field label="Server">
        <Select value={value.server_id ?? ""} onChange={(e) => set({ server_id: e.target.value || undefined })}>
          <option value="">All visible servers</option>
          {shown.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </Select>
      </Field>}
      {has("window_hours") && (
        <Field label="Activity window (hours)" hint="1 to 744 (31 days)">
          <Input inputMode="numeric" value={String(value.window_hours ?? 24)}
            onChange={(e) => set({ window_hours: Math.min(744, Math.max(1, Number(e.target.value.replace(/\D/g, "")) || 1)) })} />
        </Field>
      )}
      {has("min_severity") && (
        <Field label="Minimum severity">
          <Select value={value.min_severity ?? ""} onChange={(e) => set({ min_severity: e.target.value })}>
            <option value="">Any severity</option>
            {SEVERITIES.map((s) => <option key={s} value={s}>{s} and above</option>)}
          </Select>
        </Field>
      )}
      {has("scope") && (
        <Field label="Forecast per">
          <Select value={value.scope ?? "server"} onChange={(e) => set({ scope: e.target.value as ReportParams["scope"] })}>
            <option value="server">Server</option><option value="database">Database</option><option value="tablespace">Tablespace</option>
          </Select>
        </Field>
      )}
      {has("statuses") && (
        <fieldset className="sm:col-span-3">
          <legend className="mb-1 text-[12.5px] font-medium text-text-2">Status (none selected = open and acknowledged)</legend>
          <div className="flex flex-wrap gap-3 text-[13px]">
            {STATUSES.map((s) => (
              <label key={s} className="flex items-center gap-1.5">
                <input type="checkbox" checked={!!value.statuses?.includes(s)}
                  onChange={(e) => set({ statuses: e.target.checked ? [...(value.statuses ?? []), s] : (value.statuses ?? []).filter((x) => x !== s) })} />
                {s}
              </label>
            ))}
          </div>
        </fieldset>
      )}
    </div>
  );
}

function PreviewTable({ preview }: { preview: Preview }) {
  return (
    <div>
      <p className="mb-2 text-[12.5px] text-muted" aria-live="polite">
        {preview.total_rows.toLocaleString()} row{preview.total_rows === 1 ? "" : "s"}
        {preview.total_rows > preview.rows.length && ` (first ${preview.rows.length} shown)`}
        {preview.scope.length > 0 && ` · ${preview.scope.join(" · ")}`}
      </p>
      {preview.rows.length ? (
        <div className="scroll-thin max-h-[40vh] overflow-auto rounded-md border border-border">
          <table className="w-full text-[12px]">
            <thead className="sticky top-0 bg-surface-2">
              <tr>{preview.columns.map((c) => <th key={c} scope="col" className="whitespace-nowrap px-2 py-1.5 text-left font-medium text-text-2">{c.replace(/_/g, " ")}</th>)}</tr>
            </thead>
            <tbody className="divide-y divide-border">
              {preview.rows.map((r, i) => (
                <tr key={i}>{r.map((v, j) => <td key={j} className="max-w-[28rem] truncate px-2 py-1 align-top tabular" title={v}>{v}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="rounded-md border border-dashed border-border px-3 py-6 text-center text-[13px] text-muted">Nothing matched for the servers you can see.</p>}
    </div>
  );
}

function GenerateDialog({ report, onClose, onSchedule }: { report: Report; onClose: () => void; onSchedule?: (p: ReportParams) => void }) {
  const [params, setParams] = useState<ReportParams>(defaultParams);
  const [busy, setBusy] = useState<string | null>(null);
  const query = queryOf(report, params);
  const preview = useQuery({
    queryKey: ["report-preview", report.key, query], queryFn: () => get<Preview>(`/reports/${report.key}/preview`, query), enabled: false, retry: false,
  });
  const run = async (fmt: string) => {
    setBusy(fmt);
    try {
      await download(`/reports/${report.key}.${fmt}`, `${report.key}.${fmt}`, { query });
      toast.success(`${report.title} downloaded as ${fmt.toUpperCase()}`);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  };
  const Icon = reportIcon(report.key);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-4xl"
      title={<span className="flex items-center gap-2"><Icon className="size-4 text-accent" aria-hidden />{report.title}</span>}
      description={report.description}
      footer={<>
        {onSchedule && <Button variant="ghost" className="mr-auto" onClick={() => onSchedule(params)}><CalendarPlus /> Schedule…</Button>}
        <Button onClick={() => preview.refetch()} disabled={preview.isFetching}>{preview.isFetching ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : <Eye />} Preview</Button>
        {report.formats.map((f) => (
          <Button key={f} variant={f === "pdf" ? "primary" : "secondary"} disabled={!!busy} onClick={() => run(f)} aria-label={`Download ${report.title} as ${f.toUpperCase()}`}>
            {busy === f ? <Loader2 className="animate-spin motion-reduce:animate-none" /> : <Download />} {f.toUpperCase()}
          </Button>
        ))}
      </>}>
      <div className="space-y-4">
        <ParamFields report={report} value={params} onChange={setParams} />
        <p className="text-[12px] text-muted">The report includes only the servers your role can see. Every download is audited. Query text is masked.</p>
        {preview.error ? <ErrorState error={preview.error} onRetry={() => preview.refetch()} /> : preview.data ? <PreviewTable preview={preview.data} /> : null}
      </div>
    </Dialog>
  );
}

function ReportCard({ report, onOpen }: { report: Report; onOpen: () => void }) {
  const Icon = reportIcon(report.key);
  return (
    <button type="button" onClick={onOpen} disabled={!report.available}
      className={cn(
        "group flex h-full flex-col gap-2 rounded-[var(--radius-panel)] border border-border bg-surface p-4 text-left transition-[border-color,box-shadow,transform] duration-150 motion-reduce:transition-none",
        report.available ? "hover:-translate-y-px hover:border-border-strong hover:shadow-[0_6px_18px_-12px_rgba(5,11,20,0.35)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] motion-reduce:hover:translate-y-0" : "cursor-not-allowed opacity-60",
      )}
      aria-label={`${report.title}${report.available ? "" : " (not available with your role)"}`}>
      <span className="flex items-center gap-2.5">
        <span className="grid size-8 shrink-0 place-items-center rounded-md bg-accent-soft text-accent"><Icon className="size-4" aria-hidden /></span>
        <span className="font-medium leading-tight">{report.title}</span>
      </span>
      <span className="text-[12.5px] text-muted">{report.description}</span>
      <span className="mt-auto flex flex-wrap gap-1 pt-1">
        {report.formats.map((f) => <Badge key={f} tone="outline">{f.toUpperCase()}</Badge>)}
        {!report.available && <Badge tone="neutral">needs {report.permission}</Badge>}
      </span>
    </button>
  );
}

export function ReportsPage() {
  const { canPlatform } = useAuth();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") === "schedules" && canPlatform("reports.schedule") ? "schedules" : "catalog";
  const q = useQuery({ queryKey: ["reports"], queryFn: () => get<Report[]>("/reports") });
  const [open, setOpen] = useState<Report | null>(null);
  const [scheduling, setScheduling] = useState<{ report: Report; params: ReportParams } | null>(null);
  const byKey = useMemo(() => new Map((q.data ?? []).map((r) => [r.key, r])), [q.data]);
  const grouped = GROUPS.map((g) => ({ ...g, reports: g.keys.map((k) => byKey.get(k)).filter((r): r is Report => !!r) }));
  const other = (q.data ?? []).filter((r) => !GROUPS.some((g) => g.keys.includes(r.key)));
  if (other.length) grouped.push({ id: "other", title: "Other", keys: [], reports: other });
  const canSchedule = canPlatform("reports.schedule");
  return (
    <div>
      <PageHeader title="Reports" crumbs={[{ label: "Reports" }]}
        description="Generate CSV or PDF reports, or e-mail them on a schedule. Reports include only the servers your role can see; scheduled reports run with their owner's access." />
      <Tabs value={tab} onValueChange={(v) => setSp((p) => { const n = new URLSearchParams(p); if (v === "catalog") n.delete("tab"); else n.set("tab", v); return n; })}>
        <TabsList className="mb-4">
          <TabsTrigger value="catalog"><FileText className="mr-1.5 inline size-3.5" aria-hidden />Reports</TabsTrigger>
          {canSchedule && <TabsTrigger value="schedules"><CalendarClock className="mr-1.5 inline size-3.5" aria-hidden />Schedules</TabsTrigger>}
        </TabsList>
        <TabsContent value="catalog">
          {q.isLoading ? <SkeletonRows /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : (
            <div className="space-y-6">
              {grouped.filter((g) => g.reports.length).map((g) => (
                <section key={g.id} aria-labelledby={`grp-${g.id}`}>
                  <h2 id={`grp-${g.id}`} className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-muted">{g.title}</h2>
                  <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                    {g.reports.map((r) => <ReportCard key={r.key} report={r} onOpen={() => setOpen(r)} />)}
                  </div>
                </section>
              ))}
            </div>
          )}
        </TabsContent>
        {canSchedule && (
          <TabsContent value="schedules">
            <Card><SchedulesTab reports={q.data ?? []} /></Card>
          </TabsContent>
        )}
      </Tabs>
      {open && (
        <GenerateDialog report={open} onClose={() => setOpen(null)}
          onSchedule={canSchedule ? (params) => { setScheduling({ report: open, params }); setOpen(null); } : undefined} />
      )}
      {scheduling && (
        <ScheduleWizard reports={q.data ?? []} schedule={null} initial={{ report_key: scheduling.report.key, params: scheduling.params }}
          onClose={(saved) => { setScheduling(null); if (saved) setSp((p) => { const n = new URLSearchParams(p); n.set("tab", "schedules"); return n; }); }} />
      )}
    </div>
  );
}

