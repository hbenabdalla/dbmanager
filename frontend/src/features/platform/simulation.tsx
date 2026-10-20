import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { AlertTriangle, FlaskConical, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, del, get, post } from "@/api/client";
import { ConfirmDialog } from "@/components/confirm";
import { KeyValue, PageHeader, Stat } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { useAuth } from "@/lib/auth";
import { compactNum, dateTime, relTime } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface SimulationRun {
  id: string; operation: "generate" | "regenerate" | "remove" | "legacy"; profile: string | null; seed: number | null;
  status: "pending" | "running" | "completed" | "failed"; progress: number; phase: string | null; requested_by: string | null;
  created_at: string; started_at: string | null; finished_at: string | null; stats: Record<string, unknown>; error: string | null;
}
export interface SimulationStatus {
  simulation_present: boolean; profile: string | null; seed: number | null; generated_at: string | null; generated_by: string | null;
  legacy: boolean; servers: number; real_servers: number; databases: number; metrics: number; queries: number;
  security_findings: number; recommendations: number; sites: number; clusters: number; applications: number;
  active_run: SimulationRun | null; last_run: SimulationRun | null; history: SimulationRun[];
}
export interface SimulationProfile {
  key: string; label: string; description: string; servers: number; environments: Record<string, number>;
  applications: number; history_days: number; query_history_hours: number; include_cloud: boolean; estimated_metric_rows: number;
}
interface Preview {
  counts: Record<string, number>;
  groupings: Record<string, { remove: string[]; keep: string[] }>;
  legacy_demo_accounts: string[];
}

const OPERATION: Record<string, string> = { generate: "Generate", regenerate: "Regenerate", remove: "Remove", legacy: "Old demo mode" };
const STATUS_TONE: Record<string, "ok" | "crit" | "info" | "neutral"> = { completed: "ok", failed: "crit", running: "info", pending: "neutral" };

function profileLabel(key: string | null, profiles: SimulationProfile[] | undefined): string {
  if (!key) return "—";
  if (key === "legacy") return "Old demo mode";
  return profiles?.find((p) => p.key === key)?.label ?? key;
}

function ProfilePicker({ profiles, value, onChange }: { profiles: SimulationProfile[]; value: string; onChange: (k: string) => void }) {
  return (
    <div role="radiogroup" aria-label="Simulation profile" className="grid gap-2">
      {profiles.map((p) => (
        <button key={p.key} type="button" role="radio" aria-checked={value === p.key} onClick={() => onChange(p.key)}
          className={cn("rounded-md border px-3.5 py-3 text-left transition-colors",
            value === p.key ? "border-sim bg-sim-soft" : "border-border hover:border-border-strong")}>
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[13.5px] font-semibold">{p.label}</span>
            <span className="tabular text-[12.5px] text-text-2">{p.servers} servers, {p.history_days} {p.history_days === 1 ? "day" : "days"} of history</span>
          </div>
          <div className="mt-1 text-[12.5px] text-muted">{p.description}</div>
          <div className="mt-1.5 text-[12px] text-muted">
            {Object.entries(p.environments).map(([e, n]) => `${e} ${n}`).join(", ")}
            {p.include_cloud ? ", including cloud-managed services" : ""}
          </div>
        </button>
      ))}
    </div>
  );
}

function GenerateDialog({ open, onOpenChange, profiles, replace, busy, onSubmit }: {
  open: boolean; onOpenChange: (o: boolean) => void; profiles: SimulationProfile[]; replace: boolean; busy: boolean;
  onSubmit: (profile: string, seed: number | null, confirm: string) => void;
}) {
  const [profile, setProfile] = useState("standard");
  const [seed, setSeed] = useState("");
  const [confirm, setConfirm] = useState("");
  useEffect(() => {
    if (open) {
      setSeed("");
      setConfirm("");
    }
  }, [open]);
  const p = profiles.find((x) => x.key === profile);
  const seedValid = seed === "" || /^\d{1,9}$/.test(seed);
  const ok = !!p && seedValid && (!replace || confirm === "REGENERATE");
  return (
    <Dialog open={open} onOpenChange={onOpenChange} width="max-w-xl"
      title={replace ? "Regenerate simulated data" : "Generate simulated data"}
      description="Creates a demonstration database fleet. Nothing connects to a real database."
      footer={<>
        <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" disabled={!ok || busy} onClick={() => onSubmit(profile, seed === "" ? null : Number(seed), confirm)}>
          <FlaskConical /> {busy ? "Starting…" : replace ? "Regenerate simulated data" : "Generate simulated data"}
        </Button>
      </>}>
      <div className="space-y-4">
        <p className="text-[13px] leading-relaxed text-text-2">
          The platform will generate {p ? <strong>{p.servers} simulated database servers</strong> : "a simulated fleet"} with
          metric history, query statistics, recommendations and security findings. Every simulated resource is labelled
          <em> Simulated</em>, is never contacted over the network, and can be removed later without affecting real servers,
          users or settings.
        </p>
        {replace && (
          <div className="flex gap-2.5 rounded-md border border-warn/30 bg-warn-soft px-3 py-2.5 text-[13px] text-warn">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <div>The current simulated fleet and its history are deleted first. Real resources are not affected.</div>
          </div>
        )}
        <ProfilePicker profiles={profiles} value={profile} onChange={setProfile} />
        <Field label="Seed (optional)" hint="The same profile and seed produce the same estate, which helps with demos and bug reports."
          error={seedValid ? undefined : "Use a whole number"}>
          <Input inputMode="numeric" value={seed} onChange={(e) => setSeed(e.target.value.trim())} placeholder="Random" className="w-40" />
        </Field>
        {replace && (
          <Field label="Type REGENERATE to confirm">
            <Input value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="REGENERATE" aria-label="Confirmation text" />
          </Field>
        )}
      </div>
    </Dialog>
  );
}

function RemoveSummary() {
  const q = useQuery({ queryKey: ["simulation-preview"], queryFn: () => get<Preview>("/simulation/cleanup-preview") });
  if (q.isLoading) return <SkeletonRows rows={3} className="p-0" />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} className="py-6" />;
  const c = q.data.counts;
  const rows: [string, number][] = [
    ["Simulated servers", c.servers], ["Databases", c.databases], ["Metric samples", c.metrics], ["Query statistics", c.queries],
    ["Security findings", c.security_findings], ["Recommendations", c.recommendations],
  ];
  const kept = Object.entries(q.data.groupings).filter(([, g]) => g.keep.length);
  return (
    <div className="space-y-3 text-[13px]">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 rounded-md border border-border bg-surface-2 px-3 py-2.5">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3"><dt className="text-muted">{k}</dt><dd className="tabular font-medium">{v.toLocaleString()}</dd></div>
        ))}
      </dl>
      <p className="text-muted">{c.real_servers} real {c.real_servers === 1 ? "server is" : "servers are"} not affected.</p>
      {kept.map(([k, g]) => <p key={k} className="text-muted">Kept because a real server uses them ({k}): {g.keep.join(", ")}</p>)}
      {q.data.legacy_demo_accounts.length > 0 && (
        <p className="text-muted">
          Accounts created by the old demo mode are not removed: {q.data.legacy_demo_accounts.join(", ")}. Review them under Platform, Users.
        </p>
      )}
    </div>
  );
}

export function SimulationPage() {
  const { canPlatform } = useAuth();
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [dialog, setDialog] = useState<"generate" | "regenerate" | "remove" | null>(params.get("generate") === "1" ? "generate" : null);
  const status = useQuery({
    queryKey: ["simulation-status"],
    queryFn: () => get<SimulationStatus>("/simulation/status"),
    refetchInterval: (q) => (q.state.data?.active_run ? 1500 : 30_000),
  });
  const profiles = useQuery({ queryKey: ["simulation-profiles"], queryFn: () => get<SimulationProfile[]>("/simulation/profiles"), staleTime: 300_000 });
  const d = status.data;
  const active = d?.active_run ?? null;
  useEffect(() => {
    // "?generate=1" (from the empty-fleet screen) only makes sense while nothing is generated
    if (dialog === "generate" && d && (d.simulation_present || !canPlatform("simulation.generate"))) setDialog(null);
  }, [d, dialog]); // eslint-disable-line react-hooks/exhaustive-deps

  const refreshFleet = () => {
    for (const key of ["fleet-sources", "dashboard", "servers", "simulation-preview", "recommendations", "security", "posture", "query-stats", "fleet-databases"]) {
      qc.invalidateQueries({ queryKey: [key] });
    }
  };
  // when a background run finishes, refresh every fleet view and say so
  const wasActive = useRef<string | null>(null);
  useEffect(() => {
    if (active) {
      wasActive.current = active.id;
    } else if (wasActive.current && d?.last_run?.id === wasActive.current) {
      const r = d.last_run;
      wasActive.current = null;
      refreshFleet();
      if (r.status === "completed") toast.success(`Simulated fleet ready: ${String(r.stats.servers_created ?? "")} servers`);
      else toast.error(`Simulation failed: ${r.error ?? "unknown error"}`);
    }
  }, [active, d?.last_run]); // eslint-disable-line react-hooks/exhaustive-deps

  const closeDialog = () => {
    setDialog(null);
    if (params.get("generate")) {
      const next = new URLSearchParams(params);
      next.delete("generate");
      setParams(next, { replace: true });
    }
  };
  const onError = (e: unknown) => toast.error(e instanceof ApiError ? e.message : String(e));
  const generate = useMutation({
    mutationFn: (v: { profile: string; seed: number | null; confirm: string; replace: boolean }) =>
      v.replace ? post("/simulation/regenerate", { profile: v.profile, seed: v.seed, confirm: v.confirm }) : post("/simulation/generate", { profile: v.profile, seed: v.seed }),
    onSuccess: (_r, v) => {
      toast.message(v.replace ? "Regeneration started" : "Generation started", { description: "This page shows the progress." });
      closeDialog();
      qc.invalidateQueries({ queryKey: ["simulation-status"] });
    },
    onError,
  });
  const remove = useMutation({
    mutationFn: () => del<Record<string, number>>("/simulation", { confirm: "REMOVE" }),
    onSuccess: (r) => {
      toast.success(`Removed ${r.servers_deleted} simulated servers and their data`);
      closeDialog();
      qc.invalidateQueries({ queryKey: ["simulation-status"] });
      refreshFleet();
    },
    onError,
  });

  const canGenerate = canPlatform("simulation.generate");
  const canDelete = canPlatform("simulation.delete");
  const columns: ColumnDef<SimulationRun, unknown>[] = [
    { header: "Action", accessorKey: "operation", cell: ({ getValue }) => OPERATION[getValue() as string] ?? String(getValue()) },
    { header: "Profile", accessorKey: "profile", cell: ({ row: { original: r } }) => r.operation === "remove" ? "—" : <span>{profileLabel(r.profile, profiles.data)}{r.seed !== null ? <span className="text-muted">, seed {r.seed}</span> : null}</span> },
    { header: "Result", accessorKey: "status", cell: ({ row: { original: r } }) => <span title={r.error ?? undefined}><Badge tone={STATUS_TONE[r.status]}>{r.status}</Badge></span> },
    {
      header: "Objects", id: "objects", cell: ({ row: { original: r } }) => {
        const s = r.stats;
        const n = (s.servers_created ?? s.servers_deleted) as number | undefined;
        if (n === undefined) return <span className="text-muted">—</span>;
        return <span className="tabular text-[12.5px]">{n} servers {r.operation === "remove" ? "removed" : "created"}{s.metrics_created ? `, ${compactNum(s.metrics_created as number)} metrics` : ""}</span>;
      },
    },
    { header: "By", accessorKey: "requested_by" },
    { header: "When", accessorKey: "created_at", cell: ({ getValue }) => <span title={dateTime(getValue() as string)}>{relTime(getValue() as string)}</span> },
  ];

  return (
    <div>
      <PageHeader title="Simulation" crumbs={[{ label: "Platform" }, { label: "Simulation" }]}
        description="Generate a clearly labelled demo fleet for demonstrations, training and testing. Simulated servers never connect to a database, and removing them never touches real servers, users, permissions or settings." />
      {status.error ? <ErrorState error={status.error} onRetry={() => status.refetch()} /> : !d ? <SkeletonRows /> : (
        <>
          <Card className="mb-4">
            <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2.5">
                  <span className={cn("flex size-9 items-center justify-center rounded-lg", d.simulation_present ? "bg-sim-soft text-sim" : "bg-surface-3 text-muted")}>
                    <FlaskConical className="size-[18px]" />
                  </span>
                  <div>
                    <div className="text-[16px] font-semibold" data-testid="simulation-state">{d.simulation_present ? "Active" : "Not generated"}</div>
                    <div className="text-[12.5px] text-muted">
                      {d.simulation_present ? `${d.servers} simulated servers alongside ${d.real_servers} real` : `${d.real_servers} real servers, no simulated data`}
                    </div>
                  </div>
                </div>
                {d.simulation_present && (
                  <KeyValue className="mt-4" items={[
                    ["Profile", d.legacy ? "Created by the old demo mode (DBM_DEMO_MODE)" : !d.profile && active ? `${profileLabel(active.profile, profiles.data)} (generating)` : <span>{profileLabel(d.profile, profiles.data)}{d.seed !== null ? <span className="text-muted">, seed {d.seed}</span> : null}</span>],
                    ["Generated", d.generated_at ? <span title={dateTime(d.generated_at)}>{dateTime(d.generated_at)} ({relTime(d.generated_at)})</span> : "—"],
                    ["Generated by", d.generated_by ?? "—"],
                  ]} />
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {canGenerate && !d.simulation_present && (
                  <Button variant="primary" disabled={!!active} onClick={() => setDialog("generate")}><FlaskConical /> Generate simulated data</Button>
                )}
                {canGenerate && canDelete && d.simulation_present && (
                  <Button disabled={!!active} onClick={() => setDialog("regenerate")}><RefreshCw /> Regenerate</Button>
                )}
                {canDelete && d.simulation_present && (
                  <Button variant="danger-outline" disabled={!!active} onClick={() => setDialog("remove")}><Trash2 /> Remove simulated data</Button>
                )}
              </div>
            </div>
            {active && (
              <div className="border-t border-border px-5 py-3.5" role="status" aria-live="polite">
                <div className="mb-1.5 flex justify-between text-[12.5px]">
                  <span className="font-medium">{OPERATION[active.operation]} {profileLabel(active.profile, profiles.data)}: {active.phase ?? active.status}</span>
                  <span className="tabular text-muted">{active.progress}%</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-surface-3" aria-hidden>
                  <div className="h-full rounded-full bg-sim transition-[width] duration-500" style={{ width: `${Math.max(active.progress, 3)}%` }} />
                </div>
              </div>
            )}
            {!active && d.last_run?.status === "failed" && (
              <div className="border-t border-border bg-crit-soft px-5 py-2.5 text-[12.5px] text-crit">Last run failed: {d.last_run.error}</div>
            )}
          </Card>

          <Card className="mb-4 grid grid-cols-2 divide-border sm:grid-cols-3 lg:grid-cols-6 lg:divide-x [&>*]:border-b [&>*]:border-border lg:[&>*]:border-b-0">
            <Stat label="Servers" value={d.servers} sub={`${d.clusters} clusters, ${d.sites} sites`} />
            <Stat label="Databases" value={compactNum(d.databases)} />
            <Stat label="Metric samples" value={compactNum(d.metrics)} />
            <Stat label="Query statistics" value={compactNum(d.queries)} />
            <Stat label="Security findings" value={compactNum(d.security_findings)} />
            <Stat label="Recommendations" value={compactNum(d.recommendations)} />
          </Card>

          <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <Card className="min-w-0">
              <CardHeader title="History" description="Every simulation action is also written to the audit log." />
              <DataTable columns={columns} data={d.history} dense empty={<div className="px-4 py-8 text-center text-[13px] text-muted">No simulation actions yet.</div>} />
            </Card>
            <Card>
              <CardHeader title="How simulated data is kept apart" />
              <CardBody className="space-y-2.5 text-[12.5px] leading-relaxed text-text-2">
                <p>Every simulated server, site, cluster and application is stored with the data source <em>simulated</em>. Metrics, query statistics, findings and recommendations belong to their server and are removed with it.</p>
                <p>Simulated servers are served by the simulation adapter: no network connections, no credentials, and SQL is classified but never executed.</p>
                <p>Fleet views show real servers by default whenever any exist. Use the indicator at the top of the page to include simulated ones.</p>
              </CardBody>
            </Card>
          </div>
        </>
      )}

      {profiles.data && (
        <GenerateDialog open={dialog === "generate" || dialog === "regenerate"} onOpenChange={(o) => !o && closeDialog()}
          profiles={profiles.data} replace={dialog === "regenerate"} busy={generate.isPending}
          onSubmit={(profile, seed, confirm) => generate.mutate({ profile, seed, confirm, replace: dialog === "regenerate" })} />
      )}
      <ConfirmDialog open={dialog === "remove"} onOpenChange={(o) => !o && closeDialog()} danger expected="REMOVE"
        title="Remove simulated data" confirmLabel="Remove simulated data" busy={remove.isPending} onConfirm={() => remove.mutate()}>
        <p className="mb-3 text-[13px] text-text-2">
          Remove all simulated database servers, metrics, query statistics, findings and recommendations? Real database resources and platform configuration will not be affected.
        </p>
        {dialog === "remove" && <RemoveSummary />}
      </ConfirmDialog>
    </div>
  );
}
