import { useMutation, useQuery } from "@tanstack/react-query";
import { Archive, BarChart3, Database, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { get, post } from "@/api/client";
import { KeyValue, PageHeader } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { ChoiceCard, Wizard, type WizardStep } from "@/components/wizard";
import { type Backup, cleanEncryption, COMPONENTS, DEFAULT_COMPONENTS, type Encryption, encryptionProblem, errMsg, type Job } from "./api";
import { VerifyDialog } from "./pages";
import { ComponentChecks, DatabaseScopeFields, type PolicyForm, toPolicyForm, useTargets } from "./policies";
import { EncryptionFields, JobProgress, StatusBadge, useJob } from "./shared";

export type BackupType = "platform" | "platform_metrics" | "database" | "custom";
const STEPS: WizardStep[] = [
  { id: "type", title: "Type" }, { id: "scope", title: "Scope" }, { id: "target", title: "Storage target" }, { id: "encryption", title: "Encryption" },
  { id: "retention", title: "Retention" }, { id: "review", title: "Review" }, { id: "execute", title: "Execute" }, { id: "verify", title: "Verification" },
];

export function CreateBackupWizard() {
  const [params] = useSearchParams();
  const prefill = params.get("server_id");
  const [step, setStep] = useState("type");
  const [done, setDone] = useState<string[]>([]);
  const [type, setType] = useState<BackupType>(prefill ? "database" : "platform");
  const [components, setComponents] = useState<string[]>([...DEFAULT_COMPONENTS]);
  const [metricsDays, setMetricsDays] = useState(7);
  const [scope, setScope] = useState<PolicyForm>(() => toPolicyForm(null, prefill));
  const [targetId, setTargetId] = useState("");
  const [enc, setEnc] = useState<Encryption>({ mode: "recipient", recipient: "" });
  const [passphrase, setPassphrase] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [verifying, setVerifying] = useState<Backup | null>(null);
  const targets = useTargets();
  const job = useJob(jobId);
  const isDb = type === "database";
  const backupId = ((job.data?.result as { backup_ids?: string[] } | null)?.backup_ids ?? [])[0];
  const backup = useQuery({ queryKey: ["backup", backupId], queryFn: () => get<Backup>(`/backups/${backupId}`), enabled: !!backupId && step === "verify" });

  const platformScope = { components: type === "custom" ? components : DEFAULT_COMPONENTS, metrics: type === "platform_metrics" || (type === "custom" && metricsDays > 0) ? "export" : "none", metrics_days: type === "platform_metrics" || type === "custom" ? metricsDays : 0 };
  const start = useMutation({
    mutationFn: () => {
      const common = { target_id: targetId, encryption: cleanEncryption(enc), passphrase: enc.mode === "passphrase" && passphrase ? passphrase : null };
      if (isDb) {
        const split = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
        return post<Job>("/backups/database", {
          ...common,
          scope: {
            server_ids: scope.scope.server_ids, environment_ids: scope.scope.environment_ids, site_ids: scope.scope.site_ids, tags: split(scope.tags_text),
            databases: split(scope.databases_text), ...(scope.scope.server_directory ? { server_directory: scope.scope.server_directory } : {}),
            ...(scope.scope.directory ? { directory: scope.scope.directory } : {}),
          },
        });
      }
      return post<Job>("/backups/platform", { ...common, scope: platformScope });
    },
    onSuccess: (j) => setJobId(j.id),
    onError: (e) => toast.error(errMsg(e)),
  });

  const scopeProblem = isDb
    ? (!(scope.scope.server_ids.length || scope.scope.environment_ids.length || scope.scope.site_ids.length || scope.tags_text.trim()) ? "Select servers, environments, sites or tags" : null)
    : type === "custom" && !components.length ? "Select at least one component" : null;
  const problem: Record<string, string | null> = {
    type: null, scope: scopeProblem, target: targetId ? null : "Choose a storage target", encryption: encryptionProblem(enc, false, passphrase),
    retention: null, review: null, execute: job.data && !["QUEUED", "RUNNING", "VERIFYING"].includes(job.data.status) ? null : "Waiting for the job", verify: null,
  };
  const idx = STEPS.findIndex((s) => s.id === step);
  const next = () => {
    if (step === "review") { start.mutate(); setDone((d) => [...d, "review"]); setStep("execute"); return; }
    setDone((d) => [...new Set([...d, step])]);
    setStep(STEPS[Math.min(idx + 1, STEPS.length - 1)].id);
  };
  const target = targets.data?.find((t) => t.id === targetId);
  return (
    <div>
      <PageHeader title="Create backup" crumbs={[{ label: "Platform" }, { label: "Backup & Restore", to: "/platform/backups" }, { label: "Create backup" }]} />
      <Card className="p-4">
        <Wizard title="Create backup" steps={STEPS} current={step} completed={done} onStepChange={jobId ? undefined : setStep}
          onBack={idx > 0 && !jobId ? () => setStep(STEPS[idx - 1].id) : undefined}
          onNext={step === "verify" ? undefined : next} nextDisabled={!!problem[step]} busy={start.isPending}
          nextLabel={step === "review" ? "Start backup" : "Continue"}>
          {step === "type" && (
            <div className="grid gap-2 sm:grid-cols-2">
              <ChoiceCard name="type" selected={type === "platform"} onSelect={() => setType("platform")} icon={<Archive />} title="Platform configuration">Organizations, inventory, identity, findings, alerting, settings and backup configuration.</ChoiceCard>
              <ChoiceCard name="type" selected={type === "platform_metrics"} onSelect={() => setType("platform_metrics")} icon={<BarChart3 />} title="Platform + metrics">The same plus a VictoriaMetrics export of recent telemetry.</ChoiceCard>
              <ChoiceCard name="type" selected={type === "database"} onSelect={() => setType("database")} icon={<Database />} title="Managed database">Back up databases with the engine's own tooling, one asset at a time.</ChoiceCard>
              <ChoiceCard name="type" selected={type === "custom"} onSelect={() => setType("custom")} icon={<SlidersHorizontal />} title="Custom">Choose the platform components yourself (for example, include the audit log).</ChoiceCard>
            </div>
          )}
          {step === "scope" && (isDb ? <DatabaseScopeFields f={scope} set={(p) => setScope((x) => ({ ...x, ...p }))} /> : (
            <div className="space-y-3">
              {type === "custom" ? <ComponentChecks value={components} onChange={setComponents} /> : (
                <ul className="list-disc pl-5 text-[13px]">{COMPONENTS.filter(([k]) => DEFAULT_COMPONENTS.includes(k)).map(([k, l]) => <li key={k}><span className="font-medium">{k}</span> — {l}</li>)}</ul>
              )}
              {(type === "platform_metrics" || type === "custom") && (
                <Field label="Days of telemetry to export" hint={type === "custom" ? "0 = no telemetry" : "0 = everything retained"}>
                  <Input inputMode="numeric" value={String(metricsDays)} onChange={(e) => setMetricsDays(Number(e.target.value.replace(/\D/g, "")) || 0)} />
                </Field>
              )}
              <p className="text-[12.5px] text-muted">Secrets stay encrypted with the master key inside the archive; the master key itself is never included.</p>
            </div>
          ))}
          {step === "target" && (
            <div className="space-y-2">
              <Field label="Storage target">
                <Select value={targetId} onChange={(e) => setTargetId(e.target.value)}>
                  <option value="">Choose…</option>{(targets.data ?? []).filter((t) => t.enabled).map((t) => <option key={t.id} value={t.id}>{t.name} ({t.kind})</option>)}
                </Select>
              </Field>
              {!targets.data?.length && <p className="text-[13px] text-muted">No storage target yet: add one under <Link className="text-accent underline" to="/platform/backups?tab=targets">Storage targets</Link>.</p>}
              {isDb && <p className="text-[12.5px] text-muted">SQL Server, Oracle and Db2 write their backup files on the database server itself; KBM DBManager records and verifies them there.</p>}
            </div>
          )}
          {step === "encryption" && <EncryptionFields value={enc} onChange={setEnc} allowRunPassphrase passphrase={passphrase} onPassphrase={setPassphrase} />}
          {step === "retention" && (
            <div className="space-y-2 text-[13px]">
              <p>Backups created here are manual: retention rules never remove them. Delete them yourself when you no longer need them.</p>
              <p>For regular backups with automatic retention, <Link className="text-accent underline" to={`/platform/backups?tab=policies&policy=new${prefill ? `&server_id=${prefill}` : ""}`}>create a policy instead</Link>.</p>
            </div>
          )}
          {step === "review" && (
            <KeyValue items={[
              ["Type", { platform: "Platform configuration", platform_metrics: "Platform + metrics", database: "Managed database", custom: "Custom" }[type]],
              ["Scope", isDb ? `${scope.scope.server_ids.length} servers, ${scope.scope.environment_ids.length} environments, ${scope.scope.site_ids.length} sites${scope.tags_text ? `, tags ${scope.tags_text}` : ""}` : platformScope.components.join(", ")],
              ["Storage target", target ? `${target.name} (${target.kind})` : "—"],
              ["Encryption", enc.mode === "recipient" ? `age recipient ${enc.recipient?.slice(0, 16)}…` : enc.mode === "passphrase" ? (passphrase ? "passphrase (this run)" : `passphrase from ${enc.passphrase_ref}`) : "none"],
            ]} />
          )}
          {step === "execute" && (job.data ? <JobProgress job={job.data} /> : <p className="text-[13px] text-muted">{start.isError ? "The backup could not be started." : "Starting…"}</p>)}
          {step === "verify" && (
            <div className="space-y-3 text-[13px]">
              {job.data && <div className="flex gap-2">Job result <StatusBadge status={job.data.status} /></div>}
              {backup.data ? (
                <>
                  <KeyValue items={[
                    ["Backup", <span key="b" className="font-mono">{backup.data.id.slice(0, 8)}</span>],
                    ["Status", <StatusBadge key="s" status={backup.data.status} />],
                    ["Verification", `${backup.data.verification?.level ?? "—"}${backup.data.verification?.note ? ` — ${backup.data.verification.note}` : ""}`],
                  ]} />
                  {backup.data.encrypted && backup.data.verification?.level === "checksum" && (
                    <Button size="sm" onClick={() => setVerifying(backup.data)}>Verify contents with the key</Button>
                  )}
                </>
              ) : !backupId && <p className="text-muted">No backup was produced.</p>}
              <Button size="sm" asChild><Link to="/platform/backups?tab=history">Go to backup history</Link></Button>
            </div>
          )}
        </Wizard>
      </Card>
      {verifying && <VerifyDialog backup={verifying} onClose={() => setVerifying(null)} />}
    </div>
  );
}
