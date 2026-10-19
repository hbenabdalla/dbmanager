import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Copy, Download, KeyRound, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { get, post } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { ACTIVE, type Encryption, type EncryptionMode, errMsg, type Job, STATUS_TONE } from "./api";

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{status.replace("_", " ")}</Badge>;
}

/** Poll a job every 2 s until it ends. */
export function useJob(id: string | null | undefined) {
  return useQuery({
    queryKey: ["backup-job", id],
    queryFn: () => get<Job>(`/backup/jobs/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && !ACTIVE.includes(q.state.data.status) ? false : 2000),
  });
}

export function JobProgress({ job }: { job: Job }) {
  const cancel = useMutation({
    mutationFn: () => post<Job>(`/backup/jobs/${job.id}/cancel`),
    onSuccess: () => toast.success("Cancellation requested"),
    onError: (e) => toast.error(errMsg(e)),
  });
  const p = job.progress ?? {};
  const active = ACTIVE.includes(job.status);
  return (
    <div className="space-y-3" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        {active && <Loader2 className="size-4 animate-spin text-accent motion-reduce:animate-none" aria-hidden />}
        <span>Job <span className="font-mono">{job.id.slice(0, 8)}</span></span>
        <StatusBadge status={job.status} />
        {p.step && <span className="text-muted">{p.step.replaceAll("_", " ")}</span>}
        {p.total ? <span className="text-muted">{p.done ?? 0} of {p.total} assets</span> : null}
        {active && job.kind !== "platform_restore" && (
          <Button size="sm" className="ml-auto" disabled={cancel.isPending || job.cancel_requested} onClick={() => cancel.mutate()}>
            {job.cancel_requested ? "Cancelling…" : "Cancel"}
          </Button>
        )}
      </div>
      {!!p.assets?.length && (
        <ul className="divide-y divide-border rounded-md border border-border text-[13px]">
          {p.assets.map((a) => (
            <li key={a.key} className="flex flex-wrap items-center gap-2 px-3 py-1.5">
              <span className="min-w-0 flex-1 truncate">{a.name}</span>
              <StatusBadge status={a.status} />
              {a.error && <span className="w-full text-[12px] text-crit">{a.error}</span>}
            </li>
          ))}
        </ul>
      )}
      {!!p.steps?.length && (
        <ol className="space-y-1 text-[13px]">
          {p.steps.map((s, i) => (
            <li key={`${s.step}-${i}`} className="flex gap-2"><Badge tone={s.status === "ok" ? "ok" : "info"}>{s.status}</Badge>{s.step.replaceAll("_", " ")}{s.detail && <span className="text-muted">— {s.detail}</span>}</li>
          ))}
        </ol>
      )}
      {job.error && <p className="rounded-md border border-crit/30 bg-crit-soft px-3 py-2 text-[13px] text-crit">{job.error}</p>}
    </div>
  );
}

function saveText(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** POST /backup/keys/generate: the identity is shown once and never stored by DBManager. */
export function KeyPairGenerator({ onRecipient }: { onRecipient: (r: string) => void }) {
  const [key, setKey] = useState<{ recipient: string; identity: string } | null>(null);
  const gen = useMutation({
    mutationFn: () => post<{ recipient: string; identity: string; note: string }>("/backup/keys/generate"),
    onSuccess: (r) => { setKey({ recipient: r.recipient, identity: r.identity }); onRecipient(r.recipient); },
    onError: (e) => toast.error(errMsg(e)),
  });
  if (!key) return <Button size="sm" onClick={() => gen.mutate()} disabled={gen.isPending}><KeyRound /> Generate key pair</Button>;
  return (
    <div className="space-y-2 rounded-md border border-warn/40 bg-warn-soft p-3 text-[13px]" role="alert">
      <div className="flex gap-2 font-medium text-warn"><AlertTriangle className="mt-0.5 size-4 shrink-0" />Save this private key now — it is shown only once.</div>
      <p className="text-text-2">DBManager does not keep it. Store it in your password manager or vault, away from the backup storage. Backups encrypted to this recipient cannot be restored without it.</p>
      <code className="block break-all rounded bg-surface px-2 py-1 font-mono text-[12px]" data-testid="age-identity">{key.identity}</code>
      <div className="flex gap-2">
        <Button size="sm" onClick={() => { void navigator.clipboard?.writeText(key.identity); toast.success("Private key copied"); }}><Copy /> Copy</Button>
        <Button size="sm" onClick={() => saveText("dbmanager-backup-identity.txt", `# DBManager backup identity\n# recipient: ${key.recipient}\n${key.identity}\n`)}><Download /> Download</Button>
        <Button size="sm" variant="ghost" onClick={() => setKey(null)}>I have saved it — hide</Button>
      </div>
    </div>
  );
}

export function EncryptionFields({ value, onChange, allowRunPassphrase, passphrase, onPassphrase }: {
  value: Encryption; onChange: (e: Encryption) => void; allowRunPassphrase?: boolean; passphrase?: string; onPassphrase?: (p: string) => void;
}) {
  return (
    <div className="space-y-3">
      <Field label="Encryption">
        <Select value={value.mode} onChange={(e) => onChange({ ...value, mode: e.target.value as EncryptionMode })}>
          <option value="recipient">age public key (recommended)</option>
          <option value="passphrase">Passphrase</option>
          <option value="none">None</option>
        </Select>
      </Field>
      {value.mode === "recipient" && (
        <>
          <Field label="Recipient (age public key)" hint="Only the holder of the matching private key (AGE-SECRET-KEY-1…) can decrypt.">
            <Input value={value.recipient ?? ""} onChange={(e) => onChange({ ...value, recipient: e.target.value })} placeholder="age1…" className="font-mono" />
          </Field>
          <KeyPairGenerator onRecipient={(r) => onChange({ ...value, recipient: r })} />
        </>
      )}
      {value.mode === "passphrase" && (
        <>
          <Field label="Passphrase reference" hint="env:NAME or file:/path, read when the backup runs. Required for scheduled backups.">
            <Input value={value.passphrase_ref ?? ""} onChange={(e) => onChange({ ...value, passphrase_ref: e.target.value })} placeholder="env:DBM_BACKUP_PASSPHRASE" className="font-mono" />
          </Field>
          {allowRunPassphrase && (
            <Field label="Or a passphrase for this backup only" hint="At least 12 characters. Kept encrypted only until the job ends; you need it again to restore.">
              <Input type="password" autoComplete="new-password" value={passphrase ?? ""} onChange={(e) => onPassphrase?.(e.target.value)} />
            </Field>
          )}
        </>
      )}
      {value.mode === "none" && (
        <p className="flex gap-2 rounded-md border border-warn/40 bg-warn-soft px-3 py-2 text-[13px] text-warn">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          Without encryption anyone who can read the storage can read the backup (password hashes, configuration, database contents). Credentials stay encrypted with the master key.
        </p>
      )}
    </div>
  );
}

/** Supplying a key for verification / restore of an encrypted backup (never stored). */
export function KeyInput({ mode, identity, passphrase, onIdentity, onPassphrase }: {
  mode: EncryptionMode | undefined; identity: string; passphrase: string; onIdentity: (v: string) => void; onPassphrase: (v: string) => void;
}) {
  if (mode === "recipient") {
    return (
      <Field label="Private key (AGE-SECRET-KEY-1…)" hint="Used for this run only. Leave empty if DBM_BACKUP_IDENTITY_FILE is configured.">
        <Input type="password" autoComplete="off" className="font-mono" value={identity} onChange={(e) => onIdentity(e.target.value)} aria-label="age identity" />
      </Field>
    );
  }
  if (mode === "passphrase") {
    return (
      <Field label="Passphrase" hint="Used for this run only. Leave empty if the backup's policy has a passphrase reference.">
        <Input type="password" autoComplete="off" value={passphrase} onChange={(e) => onPassphrase(e.target.value)} aria-label="backup passphrase" />
      </Field>
    );
  }
  return null;
}
