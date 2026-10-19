import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ShieldCheck, ShieldAlert, Smartphone } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { ApiError, get, post } from "@/api/client";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { useAuth } from "@/lib/auth";
import { dateTime } from "@/lib/format";

export interface MfaStatus {
  available: boolean; enabled: boolean; confirmed_at: string | null; recovery_codes_left: number;
  required: boolean; enrollment_pending: boolean;
}
interface Setup { secret: string; otpauth_uri: string; qr_svg: string }

/** The backend renders the QR code as SVG; show it as an image (no markup is injected into the page). */
export function qrDataUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function downloadCodes(codes: string[], username: string) {
  const text = `DBManager recovery codes for ${username}\nEach code works once. Keep them somewhere safe.\n\n${codes.join("\n")}\n`;
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `dbmanager-recovery-codes-${username}.txt`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function RecoveryCodes({ codes, username, onDone }: { codes: string[]; username: string; onDone: () => void }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      toast.success("Recovery codes copied");
    } catch {
      toast.error("Copy failed: select the codes and copy them manually");
    }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onDone()} title="Store your recovery codes now"
      description="Each code signs you in once if you lose your authenticator. They are not shown again."
      footer={<Button variant="primary" onClick={onDone}>I have stored them</Button>}>
      <ul data-testid="recovery-codes" className="grid grid-cols-2 gap-2 font-mono text-[13px]">
        {codes.map((c) => <li key={c} className="select-all rounded-md border border-border bg-surface-2 px-3 py-1.5">{c}</li>)}
      </ul>
      <div className="mt-4 flex gap-2">
        <Button onClick={copy}>Copy</Button>
        <Button onClick={() => downloadCodes(codes, username)}><Download /> Download .txt</Button>
      </div>
    </Dialog>
  );
}

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Request failed";
}

/** After a refused step-up (401), re-check the session: if it really expired, the app signs out. */
function useStepUpError(setError: (m: string) => void) {
  const { refresh } = useAuth();
  return (e: unknown) => {
    setError(errText(e));
    if (e instanceof ApiError && e.status === 401) refresh();
  };
}

function EnrollDialog({ onClose, onEnabled }: { onClose: () => void; onEnabled: (codes: string[]) => void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<Setup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fail = useStepUpError(setError);
  const start = useMutation({
    mutationFn: () => post<Setup>("/me/mfa/totp/setup", { password }),
    onSuccess: (s) => { setSetup(s); setError(null); },
    onError: fail,
  });
  const enable = useMutation({
    mutationFn: () => post<{ enabled: boolean; recovery_codes: string[] }>("/me/mfa/totp/enable", { code: code.trim() }),
    onSuccess: (r) => onEnabled(r.recovery_codes),
    onError: fail,
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (setup) enable.mutate();
    else start.mutate();
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Set up an authenticator app" width="max-w-xl"
      description={setup ? "Scan the code with an authenticator app (for example Microsoft Authenticator, Google Authenticator, 1Password), then enter the 6-digit code it shows." : "Confirm your password to start."}>
      <form id="mfa-enroll" onSubmit={submit} className="space-y-4" noValidate>
        {!setup ? (
          <Field label="Current password">
            <Input type="password" autoComplete="current-password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        ) : (
          <>
            <div className="flex flex-wrap items-start gap-5">
              <img src={qrDataUrl(setup.qr_svg)} alt="QR code for your authenticator app" className="size-44 rounded-md border border-border bg-white p-1" />
              <div className="min-w-0 flex-1 space-y-2 text-[12.5px]">
                <div className="text-muted">Can't scan it? Enter this key manually:</div>
                <code data-testid="totp-secret" className="block select-all break-all rounded-md border border-border bg-surface-2 px-3 py-2 font-mono text-[13px]">{setup.secret}</code>
                <a href={setup.otpauth_uri} className="text-accent hover:underline">Open in an authenticator app on this device</a>
              </div>
            </div>
            <Field label="6-digit code">
              <Input inputMode="numeric" autoComplete="one-time-code" autoFocus maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} />
            </Field>
          </>
        )}
        {error && <p role="alert" className="rounded-md border border-crit/30 bg-crit-soft px-3 py-2 text-[13px] text-crit">{error}</p>}
      </form>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button type="submit" form="mfa-enroll" variant="primary"
          disabled={setup ? code.trim().length !== 6 || enable.isPending : !password || start.isPending}>
          {setup ? "Turn on" : "Continue"}
        </Button>
      </div>
    </Dialog>
  );
}

/** Password + current code, for turning MFA off and for new recovery codes. */
function ReauthDialog({ title, action, confirmLabel, danger, onClose, onSubmit }: {
  title: string; action: string; confirmLabel: string; danger?: boolean; onClose: () => void;
  onSubmit: (password: string, code: string) => Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fail = useStepUpError(setError);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await onSubmit(password, code.trim());
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={title} description={action}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        <Field label="Current password">
          <Input type="password" autoComplete="current-password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="Authenticator or recovery code">
          <Input autoComplete="one-time-code" maxLength={32} value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
        {error && <p role="alert" className="rounded-md border border-crit/30 bg-crit-soft px-3 py-2 text-[13px] text-crit">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant={danger ? "danger" : "primary"} disabled={busy || !password || code.trim().length < 6}>{confirmLabel}</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function SecurityPage() {
  const { me, refresh } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["my-mfa"], queryFn: () => get<MfaStatus>("/me/mfa") });
  const [enrolling, setEnrolling] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [mode, setMode] = useState<"disable" | "regenerate" | null>(null);
  const reload = () => { qc.invalidateQueries({ queryKey: ["my-mfa"] }); refresh(); };
  const s = q.data;

  return (
    <div>
      <PageHeader title="Security" crumbs={[{ label: "Account" }, { label: "Security" }]}
        description="Protect your DBManager account with a second factor." />
      {me?.mfa_enrollment_required && (
        <div role="alert" className="mb-4 flex items-start gap-2 rounded-md border border-warn/40 bg-warn-soft px-4 py-3 text-[13px] text-warn">
          <ShieldAlert className="mt-0.5 size-4 shrink-0" />
          <span>Your administrator requires multi-factor authentication for your account. Set up an authenticator app to continue using DBManager.</span>
        </div>
      )}
      <Card>
        <CardHeader title={<span className="flex items-center gap-2"><Smartphone className="size-4" /> Two-step verification</span>}
          description="An authenticator app generates a 6-digit code you enter after your password." />
        <CardBody>
          {q.isLoading ? <SkeletonRows rows={3} /> : q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !s?.available ? (
            <p className="text-[13px] text-text-2">
              Your account signs in through {me?.auth_source === "oidc" ? "single sign-on" : "your directory"}; multi-factor authentication is provided by your identity provider.
            </p>
          ) : s.enabled ? (
            <div className="space-y-3 text-[13px]">
              <div className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-ok" /> <Badge tone="ok">On</Badge>
                <span className="text-text-2">since {s.confirmed_at ? dateTime(s.confirmed_at) : "—"}</span>
              </div>
              <p className="text-text-2">Recovery codes left: <strong data-testid="codes-left">{s.recovery_codes_left}</strong>{s.recovery_codes_left <= 3 ? " — generate new ones soon." : ""}</p>
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => setMode("regenerate")}>New recovery codes</Button>
                <Button variant="danger" onClick={() => setMode("disable")}>Turn off</Button>
              </div>
              {s.required && <p className="text-[12.5px] text-muted">Your administrator requires MFA: after turning it off you must set it up again.</p>}
            </div>
          ) : (
            <div className="space-y-3 text-[13px]">
              <div className="flex items-center gap-2"><Badge tone="neutral">Off</Badge>{s.required && <Badge tone="warn">Required</Badge>}</div>
              <Button variant="primary" onClick={() => setEnrolling(true)}>Set up authenticator</Button>
            </div>
          )}
        </CardBody>
      </Card>
      {enrolling && <EnrollDialog onClose={() => setEnrolling(false)} onEnabled={(c) => { setEnrolling(false); setCodes(c); reload(); }} />}
      {codes && <RecoveryCodes codes={codes} username={me?.username ?? "user"} onDone={() => setCodes(null)} />}
      {mode === "disable" && (
        <ReauthDialog title="Turn off two-step verification?" danger confirmLabel="Turn off" onClose={() => setMode(null)}
          action="Your account will be protected by the password only."
          onSubmit={async (password, code) => {
            await post("/me/mfa/totp/disable", { password, code });
            toast.success("Two-step verification turned off");
            setMode(null);
            reload();
          }} />
      )}
      {mode === "regenerate" && (
        <ReauthDialog title="Generate new recovery codes" confirmLabel="Generate" onClose={() => setMode(null)}
          action="Your current recovery codes stop working."
          onSubmit={async (password, code) => {
            const r = await post<{ recovery_codes: string[] }>("/me/mfa/recovery-codes", { password, code });
            setMode(null);
            setCodes(r.recovery_codes);
            reload();
          }} />
      )}
    </div>
  );
}
