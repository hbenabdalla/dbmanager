import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, get } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth";

const SSO_ERRORS: Record<string, string> = {
  sso_failed: "Single sign-on didn't complete. Your account may not be permitted, or the identity provider rejected the request.",
  sso_cancelled: "Single sign-on was cancelled.",
};

export function LoginPage() {
  const { me, login } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(SSO_ERRORS[params.get("error") ?? ""] ?? null);
  const [busy, setBusy] = useState(false);
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => get<{ id: string; name: string }[]>("/auth/providers") });
  const next = params.get("next") || "/";

  if (me) return <Navigate to={next} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      navigate(next.startsWith("/") && !next.startsWith("//") ? next : "/", { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(420px,1fr)_1.1fr]">
      <div className="flex items-center justify-center bg-bg px-6 py-12">
        <div className="w-full max-w-[360px]">
          <div className="mb-8 flex items-center gap-2.5">
            <img src="/favicon.svg" alt="" className="size-8" />
            <span className="text-[15px] font-semibold">DB Manager</span>
          </div>
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
          <p className="mt-8 text-[12px] leading-relaxed text-muted">
            Access is logged. Sessions end after 30 minutes of inactivity.
          </p>
        </div>
      </div>
      <div className="relative hidden overflow-hidden bg-nav lg:block" aria-hidden>
        <LoginArt />
      </div>
    </div>
  );
}

/** A quiet field of server tiles — the same visual language as the dashboard's fleet strip. */
function LoginArt() {
  const cols = 22;
  const rows = 15;
  const tiles = Array.from({ length: cols * rows }, (_, i) => {
    const h = (Math.sin(i * 12.9898) * 43758.5453) % 1;
    const v = Math.abs(h);
    return v > 0.985 ? "var(--crit)" : v > 0.95 ? "var(--warn)" : v > 0.93 ? "#3b4b62" : "#37c48f";
  });
  return (
    <div className="absolute inset-0 flex flex-col justify-between p-14">
      <div className="grid gap-[6px] opacity-90" style={{ gridTemplateColumns: `repeat(${cols}, 18px)` }}>
        {tiles.map((c, i) => (
          <span key={i} className="block size-[18px] rounded-[3px]" style={{ background: c, opacity: c === "#37c48f" ? 0.22 + ((i * 7) % 10) / 40 : 0.95 }} />
        ))}
      </div>
      <div className="max-w-md">
        <p className="text-[26px] font-semibold leading-snug text-nav-strong">Every database you run, on one screen.</p>
        <p className="mt-3 text-[14px] leading-relaxed text-nav-text">
          PostgreSQL, Oracle, SQL Server, Db2 and MySQL fleets with health, query performance, access reviews and
          evidence-based tuning advice. Nothing changes on a server until someone with the right role confirms it.
        </p>
      </div>
    </div>
  );
}
