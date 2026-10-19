import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { CalendarClock, FileSpreadsheet, FileText, History, Mail, Pencil, Plus, Send, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { del, get, post, put } from "@/api/client";
import type { Page } from "@/api/types";
import { ConfirmDialog } from "@/components/confirm";
import { KeyValue } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/states";
import { Switch } from "@/components/ui/switch";
import { ChoiceCard, Wizard, type WizardStep } from "@/components/wizard";
import { useAuth } from "@/lib/auth";
import { bytes, dateTime, relTime } from "@/lib/format";
import {
  describeSchedule, errMsg, paramsBody, type Report, type ReportParams, type ReportRun, type ReportSchedule, type ScheduleSpec, WEEKDAYS,
} from "./api";
import { defaultParams, ParamFields, reportIcon } from "./reports";

interface Channel { id: string; name: string; kind: string; enabled: boolean; config?: { recipients?: string[] } }
interface UserRef { id: string; username: string; display_name: string | null; is_active: boolean }

interface Form {
  name: string; report_key: string; format: "csv" | "pdf"; params: ReportParams; schedule: ScheduleSpec; channel_ids: string[];
  owner_id: string; enabled: boolean;
}

const tz = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

function toForm(s: ReportSchedule | null, initial?: { report_key?: string; params?: ReportParams }, reports: Report[] = []): Form {
  const key = s?.report_key ?? initial?.report_key ?? reports.find((r) => r.available)?.key ?? "";
  return {
    name: s?.name ?? (initial?.report_key ? `${reports.find((r) => r.key === key)?.title ?? key} report` : ""),
    report_key: key, format: s?.format ?? "pdf",
    params: { ...defaultParams(), ...(s?.params ?? initial?.params ?? {}) },
    schedule: s?.schedule ?? { frequency: "weekly", time: "07:00", time_zone: tz(), weekday: 1, day_of_month: 1 },
    channel_ids: s?.channel_ids ?? [], owner_id: s?.owner_id ?? "", enabled: s?.enabled ?? true,
  };
}

const STEPS: WizardStep[] = [
  { id: "report", title: "Report", description: "What to send, and for which servers." },
  { id: "format", title: "Format", description: "PDF for reading, CSV for spreadsheets and further processing." },
  { id: "schedule", title: "Schedule", description: "When the report is generated and sent." },
  { id: "delivery", title: "Delivery", description: "Who receives it, and whose access it runs with." },
  { id: "review", title: "Review" },
];

export function ScheduleWizard({ reports, schedule, initial, onClose }: {
  reports: Report[]; schedule: ReportSchedule | null; initial?: { report_key?: string; params?: ReportParams }; onClose: (saved: boolean) => void;
}) {
  const qc = useQueryClient();
  const { me, canPlatform } = useAuth();
  const [f, setF] = useState<Form>(() => toForm(schedule, initial, reports));
  const [step, setStep] = useState("report");
  const [done, setDone] = useState<string[]>(schedule ? STEPS.map((s) => s.id) : []);
  const set = (p: Partial<Form>) => setF((x) => ({ ...x, ...p }));
  const setSch = (p: Partial<ScheduleSpec>) => set({ schedule: { ...f.schedule, ...p } });
  const channels = useQuery({ queryKey: ["notification-channels"], queryFn: () => get<Channel[]>("/notification-channels"), retry: false });
  const mail = (channels.data ?? []).filter((c) => c.kind === "email");
  const manageUsers = canPlatform("users.manage");
  const users = useQuery({
    queryKey: ["users-active"], enabled: manageUsers, queryFn: () => get<Page<UserRef>>("/users", { page_size: 200 }), select: (p) => p.items.filter((u) => u.is_active),
  });
  const report = reports.find((r) => r.key === f.report_key);
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: f.name.trim(), report_key: f.report_key, format: f.format, params: paramsBody(report, f.params), schedule: f.schedule,
        channel_ids: f.channel_ids, owner_id: f.owner_id || null, enabled: f.enabled,
      };
      return schedule ? put<ReportSchedule>(`/report-schedules/${schedule.id}`, body) : post<ReportSchedule>("/report-schedules", body);
    },
    onSuccess: (s) => {
      toast.success(schedule ? `${s.name} saved` : `${s.name} scheduled${s.next_run_at ? ` — first report ${relTime(s.next_run_at)}` : ""}`);
      qc.invalidateQueries({ queryKey: ["report-schedules"] });
      onClose(true);
    },
    onError: (e) => toast.error(errMsg(e)),
  });
  const problem: Record<string, string | null> = {
    report: !report ? "Choose a report" : !f.name.trim() ? "Enter a name" : null,
    format: null,
    schedule: !/^([01]\d|2[0-3]):[0-5]\d$/.test(f.schedule.time) ? "Time is HH:MM (24 h)" : null,
    delivery: !f.channel_ids.length ? "Choose at least one e-mail channel" : null,
    review: null,
  };
  const order = STEPS.map((s) => s.id);
  const idx = order.indexOf(step);
  const next = () => {
    if (problem[step]) return toast.error(problem[step]);
    setDone((d) => [...new Set([...d, step])]);
    if (step === "review") save.mutate();
    else setStep(order[idx + 1]);
  };
  const ownerName = f.owner_id ? users.data?.find((u) => u.id === f.owner_id)?.username : (schedule?.owner ?? me?.username);
  const Icon = reportIcon(f.report_key);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose(false)} width="max-w-4xl" title={schedule ? `Edit ${schedule.name}` : "Schedule a report"}
      description="The report is generated with the owner's current access and e-mailed as an attachment to the selected channels.">
      <Wizard steps={STEPS} current={step} completed={done} onStepChange={setStep} title="Schedule"
        onBack={() => setStep(order[Math.max(0, idx - 1)])} onNext={next} busy={save.isPending}
        nextLabel={step === "review" ? (schedule ? "Save schedule" : "Create schedule") : "Continue"}>
        {step === "report" && (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Report">
                <Select value={f.report_key} onChange={(e) => set({ report_key: e.target.value })}>
                  {reports.map((r) => <option key={r.key} value={r.key} disabled={!r.available && !manageUsers}>{r.title}</option>)}
                </Select>
              </Field>
              <Field label="Schedule name"><Input value={f.name} onChange={(e) => set({ name: e.target.value })} maxLength={128} placeholder="Weekly security findings" /></Field>
            </div>
            {report && <p className="flex items-start gap-2 text-[12.5px] text-muted"><Icon className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden />{report.description}</p>}
            {report && <ParamFields report={report} value={f.params} onChange={(params) => set({ params })} />}
          </div>
        )}
        {step === "format" && (
          <div className="grid gap-2 sm:grid-cols-2">
            <ChoiceCard name="format" selected={f.format === "pdf"} onSelect={() => set({ format: "pdf" })} title="PDF" icon={<FileText />}>
              A formatted document with the DBManager logo, the scope and the generation time. Up to 5,000 rows.
            </ChoiceCard>
            <ChoiceCard name="format" selected={f.format === "csv"} onSelect={() => set({ format: "csv" })} title="CSV" icon={<FileSpreadsheet />}>
              Every row, for spreadsheets and scripts. Values that could be read as formulas are neutralised.
            </ChoiceCard>
          </div>
        )}
        {step === "schedule" && (
          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="Frequency">
              <Select value={f.schedule.frequency} onChange={(e) => setSch({ frequency: e.target.value as ScheduleSpec["frequency"] })}>
                <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
              </Select>
            </Field>
            {f.schedule.frequency === "weekly" && (
              <Field label="Day"><Select value={String(f.schedule.weekday)} onChange={(e) => setSch({ weekday: Number(e.target.value) })}>{WEEKDAYS.map((d, i) => <option key={d} value={i + 1}>{d}</option>)}</Select></Field>
            )}
            {f.schedule.frequency === "monthly" && (
              <Field label="Day of month (1–28)">
                <Input inputMode="numeric" value={String(f.schedule.day_of_month)} onChange={(e) => setSch({ day_of_month: Math.min(28, Math.max(1, Number(e.target.value.replace(/\D/g, "")) || 1)) })} />
              </Field>
            )}
            <Field label="Time (24 h)"><Input value={f.schedule.time} onChange={(e) => setSch({ time: e.target.value })} placeholder="07:00" /></Field>
            <Field label="Time zone" hint="IANA name, e.g. Europe/Paris"><Input value={f.schedule.time_zone} onChange={(e) => setSch({ time_zone: e.target.value })} /></Field>
            <p className="text-[12.5px] text-muted sm:col-span-4">{describeSchedule(f.schedule)}. Runs on the scheduler leader; a run missed while DBManager was down is sent at the next start.</p>
          </div>
        )}
        {step === "delivery" && (
          <div className="space-y-4">
            {channels.error ? <p className="text-[13px] text-crit">Notification channels are not visible to you: {errMsg(channels.error)}</p> : (
              <fieldset className="rounded-md border border-border p-3">
                <legend className="px-1 text-[12.5px] font-medium">E-mail channels</legend>
                {mail.length ? (
                  <div className="space-y-1.5 text-[13px]">
                    {mail.map((c) => (
                      <label key={c.id} className="flex items-start gap-2">
                        <input type="checkbox" className="mt-1" checked={f.channel_ids.includes(c.id)}
                          onChange={(e) => set({ channel_ids: e.target.checked ? [...f.channel_ids, c.id] : f.channel_ids.filter((x) => x !== c.id) })} />
                        <span><span className="flex items-center gap-1.5 font-medium"><Mail className="size-3.5 text-muted" aria-hidden />{c.name}{!c.enabled && <Badge tone="warn">disabled</Badge>}</span>
                          {c.config?.recipients?.length ? <span className="block text-[12px] text-muted">{c.config.recipients.join(", ")}</span> : null}</span>
                      </label>
                    ))}
                  </div>
                ) : <p className="text-[13px] text-muted">No e-mail channel yet. Create one under Platform → Notification channels; reports are only sent by e-mail.</p>}
              </fieldset>
            )}
            {manageUsers ? (
              <Field label="Runs as" hint="The report shows what this user may see when it runs. A disabled owner, or one who lost access, fails the run without sending anything.">
                <Select value={f.owner_id} onChange={(e) => set({ owner_id: e.target.value })}>
                  <option value="">{schedule?.owner ? `${schedule.owner} (current owner)` : `Me (${me?.username ?? "current user"})`}</option>
                  {users.data?.map((u) => <option key={u.id} value={u.id}>{u.username}{u.display_name && u.display_name !== u.username ? ` — ${u.display_name}` : ""}</option>)}
                </Select>
              </Field>
            ) : <p className="text-[12.5px] text-muted">The report runs with your access ({me?.username}).</p>}
            <div className="flex items-center gap-2"><Switch id="sched-enabled" checked={f.enabled} onCheckedChange={(v) => set({ enabled: v })} /><label htmlFor="sched-enabled" className="text-[13px]">Enabled</label></div>
          </div>
        )}
        {step === "review" && (
          <KeyValue items={[
            ["Name", f.name],
            ["Report", report?.title ?? f.report_key],
            ["Format", f.format.toUpperCase()],
            ["Schedule", describeSchedule(f.schedule)],
            ["Channels", mail.filter((c) => f.channel_ids.includes(c.id)).map((c) => c.name).join(", ") || "—"],
            ["Runs as", ownerName ?? "—"],
            ["State", f.enabled ? "enabled" : "disabled"],
          ]} />
        )}
      </Wizard>
    </Dialog>
  );
}

const RUN_TONE = { success: "ok", partial: "warn", failed: "crit" } as const;

function RunStatus({ status }: { status: string | null }) {
  if (!status) return <span className="text-muted">never run</span>;
  return <Badge tone={RUN_TONE[status as keyof typeof RUN_TONE] ?? "neutral"}>{status}</Badge>;
}

function RunsDialog({ schedule, onClose }: { schedule: ReportSchedule; onClose: () => void }) {
  const q = useQuery({ queryKey: ["report-runs", schedule.id], queryFn: () => get<ReportRun[]>(`/report-schedules/${schedule.id}/runs`), refetchInterval: 15_000 });
  const columns: ColumnDef<ReportRun, unknown>[] = [
    { header: "Started", accessorKey: "started_at", cell: ({ getValue }) => <span title={dateTime(getValue() as string)}>{relTime(getValue() as string)}</span> },
    { header: "Status", accessorKey: "status", cell: ({ getValue }) => <RunStatus status={getValue() as string} /> },
    { header: "Trigger", accessorKey: "trigger", cell: ({ row: { original: r } }) => <span className="text-[12.5px]">{r.trigger === "manual" ? `sent by ${r.triggered_by ?? "?"}` : "schedule"}</span> },
    { header: "Rows", accessorKey: "rows", cell: ({ getValue }) => <span className="tabular">{(getValue() as number | null)?.toLocaleString() ?? "—"}</span> },
    { header: "Size", accessorKey: "size_bytes", cell: ({ getValue }) => <span className="tabular">{getValue() != null ? bytes(getValue() as number) : "—"}</span> },
    { header: "Delivery", id: "d", enableSorting: false, cell: ({ row: { original: r } }) => (
      <div className="space-y-0.5 text-[12px]">
        {r.deliveries.map((d) => <div key={d.channel_id}>{d.name}: {d.status === "sent" ? <span className="text-ok">sent to {d.recipients}</span> : <span className="text-crit">{d.error}</span>}</div>)}
        {r.error && !r.deliveries.length && <div className="text-crit">{r.error}</div>}
      </div>
    ) },
  ];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-4xl" title={`History of ${schedule.name}`} description="The last 50 runs, newest first.">
      <DataTable columns={columns} data={q.data} loading={q.isLoading} error={q.error} dense
        empty={<EmptyState icon={<History />} title="No runs yet">Use “Send now” to try the schedule.</EmptyState>} />
    </Dialog>
  );
}

export function SchedulesTab({ reports }: { reports: Report[] }) {
  const qc = useQueryClient();
  const [edit, setEdit] = useState<ReportSchedule | "new" | null>(null);
  const [history, setHistory] = useState<ReportSchedule | null>(null);
  const [removing, setRemoving] = useState<ReportSchedule | null>(null);
  const q = useQuery({ queryKey: ["report-schedules"], queryFn: () => get<ReportSchedule[]>("/report-schedules"), refetchInterval: 30_000 });
  const send = useMutation({
    mutationFn: (s: ReportSchedule) => post<ReportRun>(`/report-schedules/${s.id}/run`),
    onSuccess: (r, s) => {
      if (r.status === "success") toast.success(`${s.name} sent (${r.deliveries.reduce((n, d) => n + (d.recipients ?? 0), 0)} recipients)`);
      else toast.error(`${s.name}: ${r.error ?? r.status}`);
      qc.invalidateQueries({ queryKey: ["report-schedules"] });
      qc.invalidateQueries({ queryKey: ["report-runs", s.id] });
    },
    onError: (e) => toast.error(errMsg(e)),
  });
  const remove = useMutation({
    mutationFn: (s: ReportSchedule) => del(`/report-schedules/${s.id}`),
    onSuccess: (_r, s) => { toast.success(`${s.name} deleted`); setRemoving(null); qc.invalidateQueries({ queryKey: ["report-schedules"] }); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const columns: ColumnDef<ReportSchedule, unknown>[] = [
    { header: "Schedule", accessorKey: "name", cell: ({ row: { original: s } }) => {
      const Icon = reportIcon(s.report_key);
      return <div className="flex items-start gap-2"><Icon className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden /><div><div className="font-medium">{s.name}</div><span className="text-[12px] text-muted">{s.report_title} · {s.format.toUpperCase()} · runs as {s.owner ?? "—"}</span></div></div>;
    } },
    { header: "When", accessorKey: "schedule", cell: ({ row: { original: s } }) => (
      <span className="text-[12.5px]">{describeSchedule(s.schedule)}{s.enabled && s.next_run_at && <span className="block text-muted" title={dateTime(s.next_run_at)}>next {relTime(s.next_run_at)}</span>}</span>
    ) },
    { header: "Channels", accessorKey: "channel_ids", cell: ({ getValue }) => <span className="tabular">{(getValue() as string[]).length}</span> },
    { header: "Last run", accessorKey: "last_status", cell: ({ row: { original: s } }) => (
      <span className="flex flex-col gap-0.5 text-[12.5px]"><RunStatus status={s.last_status} />{s.last_run_at && <span className="text-muted">{relTime(s.last_run_at)}</span>}
        {s.last_error && <span className="max-w-[22rem] text-crit" title={s.last_error}>{s.last_error}</span>}</span>
    ) },
    { header: "State", accessorKey: "enabled", cell: ({ getValue }) => getValue() ? <Badge tone="ok">enabled</Badge> : <Badge tone="neutral">disabled</Badge> },
    { header: "", id: "a", enableSorting: false, cell: ({ row: { original: s } }) => (
      <div className="flex justify-end gap-1">
        <Button size="sm" onClick={() => send.mutate(s)} disabled={send.isPending && send.variables?.id === s.id}><Send /> Send now</Button>
        <Button size="sm" variant="ghost" aria-label={`History of ${s.name}`} onClick={() => setHistory(s)}><History /></Button>
        <Button size="sm" variant="ghost" aria-label={`Edit ${s.name}`} onClick={() => setEdit(s)}><Pencil /></Button>
        <Button size="sm" variant="ghost" aria-label={`Delete ${s.name}`} onClick={() => setRemoving(s)}><Trash2 /></Button>
      </div>
    ) },
  ];
  return (
    <>
      <div className="flex justify-end border-b border-border p-3"><Button size="sm" variant="primary" onClick={() => setEdit("new")}><Plus /> New schedule</Button></div>
      <DataTable columns={columns} data={q.data} loading={q.isLoading} error={q.error} onRetry={() => q.refetch()} dense
        empty={<EmptyState icon={<CalendarClock />} title="No scheduled reports" action={<Button size="sm" variant="primary" onClick={() => setEdit("new")}><Plus /> New schedule</Button>}>
          E-mail a report daily, weekly or monthly to a notification channel.</EmptyState>} />
      {edit && <ScheduleWizard reports={reports} schedule={edit === "new" ? null : edit} onClose={() => setEdit(null)} />}
      {history && <RunsDialog schedule={history} onClose={() => setHistory(null)} />}
      <ConfirmDialog open={!!removing} onOpenChange={(o) => !o && setRemoving(null)} title={`Delete ${removing?.name ?? ""}?`} confirmLabel="Delete schedule" danger
        busy={remove.isPending} onConfirm={() => removing && remove.mutate(removing)}>
        No further reports are sent. Its run history is deleted; every delivery stays in the audit log.
      </ConfirmDialog>
    </>
  );
}
