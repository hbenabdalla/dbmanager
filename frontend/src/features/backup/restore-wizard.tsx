import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { get, post } from "@/api/client";
import { CodeBlock, KeyValue, PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { Wizard, type WizardStep } from "@/components/wizard";
import { dateTime } from "@/lib/format";
import { type Backup, COMPAT_LABEL, type DatabasePreview, errMsg, type Job, PLATFORM_CONFIRM, type PlatformPreview } from "./api";
import { asset } from "./pages";
import { useBackupServers } from "./policies";
import { JobProgress, KeyInput, StatusBadge, useJob } from "./shared";
import { DatabaseEngineIcon } from "@/components/engine-icon";
import { engineLabel } from "@/lib/engines";

const STEPS: WizardStep[] = [
  { id: "select", title: "Select backup" }, { id: "manifest", title: "Validate manifest" }, { id: "integrity", title: "Verify integrity" },
  { id: "compat", title: "Check compatibility" }, { id: "components", title: "Select components" }, { id: "impact", title: "Impact analysis" },
  { id: "confirm", title: "Confirmation" }, { id: "restore", title: "Restore" }, { id: "validation", title: "Validation" }, { id: "result", title: "Result" },
];
const ACTIVE = ["QUEUED", "RUNNING", "VERIFYING"];

export function RestoreWizard() {
  const { id = "" } = useParams();
  const q = useQuery({ queryKey: ["backup", id], queryFn: () => get<Backup>(`/backups/${id}`) });
  if (q.isLoading) return <SkeletonRows rows={8} />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  return <RestoreFlow backup={q.data} />;
}

function RestoreFlow({ backup }: { backup: Backup }) {
  const platform = backup.kind === "platform";
  const [step, setStep] = useState("select");
  const [done, setDone] = useState<string[]>([]);
  const [identity, setIdentity] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [components, setComponents] = useState<string[] | null>(null);
  const [serverId, setServerId] = useState(backup.server_id ?? "");
  const [targetDb, setTargetDb] = useState(backup.database ? `${backup.database}_restored` : "");
  const [preview, setPreview] = useState<PlatformPreview | DatabasePreview | null>(null);
  const [confirm, setConfirm] = useState("");
  const [preBackup, setPreBackup] = useState(true);
  const [acceptMigration, setAcceptMigration] = useState(false);
  const [acceptKeys, setAcceptKeys] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const servers = useBackupServers();
  const keys = { identity: identity || null, passphrase: passphrase || null };

  const runPreview = useMutation({
    mutationFn: (body: Record<string, unknown>) => post<PlatformPreview | DatabasePreview>(`/backups/${backup.id}/restore/preview`, { ...keys, ...body }),
    onError: (e) => toast.error(errMsg(e)),
  });
  const start = useMutation({
    mutationFn: () => post<Job>(`/backups/${backup.id}/restore`, platform
      ? { ...keys, components, confirm, pre_restore_backup: preBackup, accept_migration: acceptMigration, accept_missing_keys: acceptKeys }
      : { ...keys, server_id: serverId || null, target_database: targetDb, confirm }),
    onSuccess: (j) => setJobId(j.id),
    onError: (e) => toast.error(errMsg(e)),
  });

  const pp = platform ? (preview as PlatformPreview | null) : null;
  const dp = platform ? null : (preview as DatabasePreview | null);
  const expected = platform ? PLATFORM_CONFIRM : targetDb;
  const finished = !!job.data && !ACTIVE.includes(job.data.status);
  const idx = STEPS.findIndex((s) => s.id === step);
  const go = (to: string) => { setDone((d) => [...new Set([...d, step])]); setStep(to); };

  const next = async () => {
    try {
      if (step === "select") {
        const r = await runPreview.mutateAsync(platform ? {} : { server_id: serverId || null, target_database: targetDb || null });
        setPreview(r);
        if (platform) setComponents((r as PlatformPreview).components.map((c) => c.key));
      }
      if (step === "components") {
        setPreview(await runPreview.mutateAsync(platform ? { components } : { server_id: serverId || null, target_database: targetDb }));
      }
      if (step === "confirm") { start.mutate(); }
      go(STEPS[Math.min(idx + 1, STEPS.length - 1)].id);
    } catch {
      /* toast shown by the mutation */
    }
  };

  const blocked: Record<string, string | null> = {
    select: null,
    manifest: null,
    integrity: pp && !pp.integrity.ok ? "Integrity check failed" : null,
    compat: pp?.compatibility.status === "unsupported" ? "This backup cannot be restored by this version" : dp && !dp.restore_supported ? "Restore is done with the engine's own tools" : dp?.warnings.some((w) => w.includes("cannot be restored")) ? "Different engine" : null,
    components: platform ? (components?.length ? null : "Select at least one component") : (/^[A-Za-z_][A-Za-z0-9_$#@-]{0,127}$/.test(targetDb) ? null : "Enter a valid new database name"),
    impact: pp?.impact?.error ? "The trial restore failed" : null,
    confirm: confirm !== expected ? `Type ${expected} to confirm`
      : pp?.compatibility.status === "migration_required" && !acceptMigration ? "Confirm the column mapping"
        : pp?.keys.missing.length && !acceptKeys ? "Confirm the missing master keys" : null,
    restore: finished ? null : "Restoring…",
    validation: null,
    result: null,
  };
  const sameEngine = (servers.data ?? []).filter((s) => s.engine === backup.engine);
  return (
    <div>
      <PageHeader title={`Restore ${platform ? "KBM DBManager platform" : asset(backup)}`}
        crumbs={[{ label: "Platform" }, { label: "Backup & Restore", to: "/platform/backups?tab=history" }, { label: "Restore" }]}
        description={`Backup ${backup.id.slice(0, 8)} from ${dateTime(backup.created_at)}`} />
      <Card className="p-4">
        <Wizard title="Restore" steps={STEPS} current={step} completed={done}
          onBack={idx > 0 && !jobId ? () => setStep(STEPS[idx - 1].id) : undefined}
          onNext={step === "result" ? undefined : () => void next()} busy={runPreview.isPending || start.isPending}
          nextDisabled={!!blocked[step]} nextLabel={step === "confirm" ? "Restore" : "Continue"}>
          {step === "select" && (
            <div className="space-y-3">
              <KeyValue items={[
                ["Backup", <span key="i" className="font-mono">{backup.id}</span>], ["Asset", asset(backup)], ["Status", <StatusBadge key="s" status={backup.status} />],
                ["Encryption", backup.encrypted ? backup.encryption.mode : "none"], ["Created", dateTime(backup.created_at)],
              ]} />
              {backup.encrypted && <KeyInput mode={backup.encryption.mode} identity={identity} passphrase={passphrase} onIdentity={setIdentity} onPassphrase={setPassphrase} />}
              {!platform && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label={`Restore to server (${engineLabel(backup.engine ?? "")})`}>
                    <Select value={serverId} onChange={(e) => setServerId(e.target.value)}>
                      {sameEngine.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                      {!sameEngine.some((s) => s.id === serverId) && <option value={serverId}>{backup.server_name ?? serverId}</option>}
                    </Select>
                  </Field>
                </div>
              )}
            </div>
          )}
          {step === "manifest" && (pp ? (
            <KeyValue items={[
              ["Format", pp.manifest.format], ["Created", `${dateTime(pp.manifest.created_at)}${pp.manifest.created_by ? ` by ${pp.manifest.created_by}` : ""}`],
              ["KBM DBManager version", pp.manifest.dbm_version], ["Schema", pp.manifest.schema_version], ["Components", pp.manifest.components.join(", ")],
            ]} />
          ) : dp ? (
            <KeyValue items={[["Engine", backup.engine ? <DatabaseEngineIcon engine={backup.engine} withLabel /> : "—"], ["Method", `${backup.method} (${backup.tool ?? ""})`], ["Source database", backup.database ?? "—"], ["Location", backup.location ?? "—"]]} />
          ) : null)}
          {step === "integrity" && (pp ? (
            <p className="text-[13px] text-ok" role="status">The stored archive matches its SHA-256 and all {pp.integrity.tables} tables match their checksums.</p>
          ) : (
            <p className="text-[13px]" role="status">Last verification: {dp?.integrity?.level ?? "never"} {dp?.integrity?.ok === false ? "(failed)" : dp?.integrity?.ok ? "(passed)" : ""}. The restore job checks the SHA-256 and the dump again before restoring.</p>
          ))}
          {step === "compat" && (pp ? (
            <div className="space-y-2 text-[13px]">
              <div className="flex items-center gap-2">Compatibility <Badge tone={pp.compatibility.status === "compatible" ? "ok" : pp.compatibility.status === "migration_required" ? "warn" : "crit"}>{COMPAT_LABEL[pp.compatibility.status]}</Badge></div>
              <p className="text-muted">Backup: KBM DBManager {pp.compatibility.backup_version}, schema {pp.compatibility.backup_schema}. This installation: {pp.compatibility.current_version}, schema {pp.compatibility.current_schema}.</p>
              {pp.compatibility.notes.map((n) => <p key={n}>{n}</p>)}
              {pp.keys.missing.length > 0 && <p className="text-crit">Master keys not configured: {pp.keys.missing.join(", ")} (set DBM_PREVIOUS_MASTER_KEYS)</p>}
            </div>
          ) : dp && (
            <div className="space-y-2 text-[13px]">
              <div className="flex items-center gap-2">Engine support <Badge tone={dp.restore_supported ? "ok" : "warn"}>{dp.capabilities.status.replaceAll("_", " ")}</Badge></div>
              {dp.warnings.map((w) => <p key={w} className="text-warn">{w}</p>)}
              {!dp.restore_supported && dp.restore_command && <><p>Restore this backup with the engine's tools on the database host:</p><CodeBlock code={dp.restore_command} /></>}
            </div>
          ))}
          {step === "components" && (platform && pp ? (
            <fieldset className="space-y-1 text-[13px]">
              {pp.components.map((c) => (
                <label key={c.key} className="flex items-start gap-2">
                  <input type="checkbox" className="mt-1" checked={components?.includes(c.key) ?? false} onChange={(e) => setComponents((v) => (e.target.checked ? [...(v ?? []), c.key] : (v ?? []).filter((x) => x !== c.key)))} />
                  <span><span className="font-medium">{c.key}</span> — {c.label}{c.merge_only && <Badge tone="outline" className="ml-1">merged</Badge>}</span>
                </label>
              ))}
            </fieldset>
          ) : (
            <Field label="New database (or schema) name" hint="Restores never overwrite an existing database: the name must not exist on the target server.">
              <Input value={targetDb} onChange={(e) => setTargetDb(e.target.value)} className="font-mono" />
            </Field>
          ))}
          {step === "impact" && (pp?.impact ? (
            <div className="space-y-3 text-[13px]">
              {pp.impact.error && <p className="rounded-md border border-crit/30 bg-crit-soft px-3 py-2 text-crit">{pp.impact.error}</p>}
              <table className="w-full">
                <thead className="text-left text-[12px] text-muted"><tr><th className="py-1">Table</th><th>In backup</th><th>Added</th><th>Overwritten</th><th>Removed</th></tr></thead>
                <tbody>{Object.entries(pp.impact.tables).map(([t, r]) => <tr key={t} className="border-t border-border"><td className="py-1 font-mono">{t}</td><td>{r.in_backup}</td><td>{r.inserted}</td><td>{r.updated}</td><td className={r.deleted ? "text-crit" : undefined}>{r.deleted}</td></tr>)}</tbody>
              </table>
              {Object.keys(pp.impact.side_effects).length > 0 && (
                <div><div className="font-medium">Other tables changed by cascading deletes</div>{Object.entries(pp.impact.side_effects).map(([t, n]) => <div key={t}><span className="font-mono">{t}</span>: {n}</div>)}</div>
              )}
              {pp.warnings.map((w) => <p key={w} className="flex gap-2 text-warn"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{w}</p>)}
              <p className="text-muted">Computed by a trial restore that was rolled back: nothing has changed yet.</p>
            </div>
          ) : dp && (
            <div className="space-y-2 text-[13px]">
              <p>Creates <span className="font-mono">{dp.impact.creates}</span> on {dp.server.name}. {dp.impact.note}</p>
              {dp.warnings.map((w) => <p key={w} className="text-warn">{w}</p>)}
            </div>
          ))}
          {step === "confirm" && (
            <div className="space-y-3 text-[13px]">
              <p className="flex gap-2 rounded-md border border-crit/30 bg-crit-soft px-3 py-2 text-crit"><AlertTriangle className="mt-0.5 size-4 shrink-0" />
                {platform ? "The selected components of KBM DBManager's configuration are replaced by the backup. This is recorded in the audit log." : "A new database is created from the backup and recorded in the audit log."}</p>
              {platform && <label className="flex items-center gap-2"><input type="checkbox" checked={preBackup} onChange={(e) => setPreBackup(e.target.checked)} />Take a safety backup of the current platform first (recommended)</label>}
              {platform && !preBackup && <p className="text-[12.5px] text-muted">Restoring without a safety backup is refused unless an administrator turns on <span className="font-mono">backup.allow_restore_without_safety_backup</span> in Platform → Configuration → Settings.</p>}
              {pp?.compatibility.status === "migration_required" && <label className="flex items-center gap-2"><input type="checkbox" checked={acceptMigration} onChange={(e) => setAcceptMigration(e.target.checked)} />I accept that columns added since the backup take their default values</label>}
              {!!pp?.keys.missing.length && <label className="flex items-center gap-2"><input type="checkbox" checked={acceptKeys} onChange={(e) => setAcceptKeys(e.target.checked)} />Restore anyway: encrypted values that need {pp.keys.missing.join(", ")} cannot be decrypted</label>}
              <Field label={`Type ${expected} to confirm`}><Input value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-label="Confirmation text" placeholder={expected} /></Field>
            </div>
          )}
          {(step === "restore" || step === "validation") && (job.data ? <JobProgress job={job.data} /> : <p className="text-[13px] text-muted">{start.isError ? "The restore could not be started." : "Starting…"}</p>)}
          {step === "result" && job.data && (
            <div className="space-y-2 text-[13px]">
              <div className="flex items-center gap-2">Restore <StatusBadge status={job.data.status} /></div>
              {job.data.status === "SUCCESS" && platform && (job.data.result as { pre_restore_backup?: string } | null)?.pre_restore_backup && (
                <p>Safety backup taken before the restore: <span className="font-mono">{String((job.data.result as { pre_restore_backup?: string }).pre_restore_backup).slice(0, 8)}</span></p>
              )}
              {job.data.status === "SUCCESS" && !platform && <p>Database <span className="font-mono">{targetDb}</span> was created.</p>}
              {job.data.error && <p className="text-crit">{job.data.error}</p>}
              <Button size="sm" asChild><Link to="/platform/backups?tab=history">Back to backup history</Link></Button>
            </div>
          )}
        </Wizard>
      </Card>
    </div>
  );
}
