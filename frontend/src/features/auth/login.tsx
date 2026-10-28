import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { DatabaseEngineIcon } from "@/components/engine-icon";
import { ENGINES } from "@/lib/engines";
import { BrandLogo, COMPANY_NAME, KbmCoverMotif, KbmLogo, PRODUCT_NAME, PRODUCT_TAGLINE } from "@/components/brand";
import { ApiError, get } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";

const SSO_ERRORS: Record<string, string> = {
  sso_failed: "Single sign-on didn't complete. Your account may not be permitted, or the identity provider rejected the request.",
  sso_cancelled: "Single sign-on was cancelled.",
};

export function LoginPage() {
  const { me, login, loginMfa } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(SSO_ERRORS[params.get("error") ?? ""] ?? null);
  const [busy, setBusy] = useState(false);
  /** set after the password step when the account uses an authenticator */
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => get<{ id: string; name: string }[]>("/auth/providers") });
  const version = useQuery({ queryKey: ["public-version"], queryFn: () => get<{ version: string }>("/version"), staleTime: Infinity, retry: false });
  const next = params.get("next") || "/";

  if (me) return <Navigate to={next} replace />;

  const go = () => navigate(next.startsWith("/") && !next.startsWith("//") ? next : "/", { replace: true });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await login(username, password);
      if ("mfa_required" in res && res.mfa_required) {
        setMfaToken(res.mfa_token);
        setCode("");
        return;
      }
      go();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (e: FormEvent) => {
    e.preventDefault();
    if (!mfaToken) return;
    setBusy(true);
    setError(null);
    try {
      await loginMfa(mfaToken, code.trim());
      go();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Sign-in failed. Check your connection and try again.";
      if (err instanceof ApiError && /expired/i.test(msg)) {
        // the challenge is only valid for a few minutes: start again with the password
        setMfaToken(null);
        setPassword("");
      }
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  const restart = () => {
    setMfaToken(null);
    setCode("");
    setPassword("");
    setError(null);
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(420px,1fr)_1.15fr]">
      <div className="flex flex-col bg-bg px-6 py-10">
        <div className="m-auto w-full max-w-[360px]">
          <div className="mb-9">
            <BrandLogo width={210} />
          </div>
          {mfaToken ? (
            <>
              <h1 className="text-[24px] font-semibold">Two-step verification</h1>
              <p className="mt-1 text-[13px] text-muted">
                Enter the 6-digit code from your authenticator app, or one of your recovery codes.
              </p>
              <form onSubmit={submitCode} className="mt-6 space-y-4" noValidate>
                <Field label="Authentication code">
                  <Input autoComplete="one-time-code" inputMode="text" autoFocus value={code} maxLength={32}
                    onChange={(e) => setCode(e.target.value)} required />
                </Field>
                {error && <p role="alert" className="rounded-md border border-crit/30 bg-crit-soft px-3 py-2 text-[13px] text-crit">{error}</p>}
                <Button type="submit" variant="primary" size="lg" className="w-full" disabled={busy || code.trim().length < 6}>
                  {busy ? "Verifying…" : "Verify"}
                </Button>
                <button type="button" onClick={restart} className="w-full text-center text-[12.5px] text-muted hover:text-text hover:underline">
                  Use a different account
                </button>
              </form>
            </>
          ) : (
          <>
          <h1 className="text-[24px] font-semibold">Sign in</h1>
          <p className="mt-1 text-[13px] text-muted">Use your platform account or your directory credentials.</p>
          <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
            <Field label="Username">
              <Input autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} required />
            </Field>
            <Field label="Password">
              <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </Field>
            {error && <p role="alert" className="rounded-md border border-crit/30 bg-crit-soft px-3 py-2 text-[13px] text-crit">{error}</p>}
            <Button type="submit" variant="primary" size="lg" className="w-full" disabled={busy || !username || !password}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
          {(providers.data?.length ?? 0) > 0 && (
            <div className="mt-6 space-y-2 border-t border-border pt-6">
              {providers.data!.map((p) => (
                <Button key={p.id} asChild size="lg" className="w-full">
                  <a href={`/api/v1/auth/oidc/${p.id}/start`}><KeyRound /> Continue with {p.name}</a>
                </Button>
              ))}
            </div>
          )}
          </>
          )}
          <p className="mt-8 text-[12px] leading-relaxed text-muted">
            Access is logged. Sessions end after 30 minutes of inactivity.
          </p>
        </div>
        <p className="mx-auto mt-8 w-full max-w-[360px] text-[11.5px] text-muted">
          {PRODUCT_NAME}{version.data?.version ? ` ${version.data.version}` : ""} · © {new Date().getFullYear()} {COMPANY_NAME}
        </p>
      </div>
      <div className="relative hidden overflow-hidden bg-brand lg:block">
        <LoginArt />
      </div>
    </div>
  );
}

/** The KBM cover: KBM blue with its band, dot-grid and ring motifs, the white KBM logo and the product statement. */
function LoginArt() {
  return (
    <div className="absolute inset-0 flex flex-col justify-between p-14 text-white">
      <KbmCoverMotif />
      <KbmLogo full width={170} tone="white" label="KBM — Khorafi Business Machines" className="relative" />
      <div className="relative max-w-lg">
        <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-white/70">{PRODUCT_TAGLINE}</p>
        <h2 className="mt-3 text-[40px] font-bold leading-[1.1] tracking-[-0.015em] text-white">{PRODUCT_NAME}</h2>
        <p className="mt-4 text-[15px] leading-relaxed text-white/85">
          One platform to monitor, manage, secure and optimise PostgreSQL, MySQL, MariaDB, SQL Server, Oracle and Db2
          fleets: health, query performance, access reviews, backups and evidence-based tuning advice. Nothing changes
          on a server until someone with the right role confirms it.
        </p>
        <div className="mt-7 h-px w-full max-w-md bg-white/25" />
        <ul className="mt-5 flex flex-wrap gap-x-4 gap-y-2.5 text-[12.5px] text-white/90" aria-label="Supported database engines">
          {Object.keys(ENGINES).map((e) => <li key={e}><DatabaseEngineIcon engine={e} size="sm" withLabel /></li>)}
        </ul>
      </div>
    </div>
  );
}
