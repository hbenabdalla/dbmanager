/** Platform Setup Wizard. Progress is saved on the server after every step (PUT /platform/setup),
 * so the wizard resumes where the administrator left off, on any device. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, CheckCircle2, Database, Gauge, KeyRound, Layers, MapPin, Plus, Rocket, ShieldCheck, Timer, UserCog } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { BrandLogo } from "@/components/brand";
import { ApiError, get, post, put } from "@/api/client";
import { useEnvironments, useSites } from "@/api/hooks";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { ChoiceCard, SuccessPanel, Wizard, type WizardStep } from "@/components/wizard";
import { OnboardingWizard } from "@/features/onboarding/onboarding-wizard";
import { IdentityWizardDialog } from "@/features/platform/idp-wizards";
import { useAuth } from "@/lib/auth";

export interface SetupState {
  completed: boolean; needs_setup: boolean; current_step: string | null; completed_steps: string[]; steps: string[];
  data: Record<string, Record<string, unknown>>; organization?: { id: string; name: string; timezone: string | null };
}

export const SETUP_STEPS: WizardStep[] = [
  { id: "welcome", title: "Welcome", description: "A few minutes to get KBM DBManager ready for your team. Progress is saved after every step." },
  { id: "admin", title: "Administrator", description: "Secure the bootstrap administrator account." },
  { id: "organization", title: "Organization", description: "Your organization's name and the time zone reports and schedules use." },
  { id: "sites", title: "Sites", description: "Data centers, cloud regions or locations your servers run in.", optional: true },
  { id: "environments", title: "Environments", description: "Production, DR, UAT, development… Environments drive risk levels and approvals." },
  { id: "authentication", title: "Sign-in", description: "How people sign in to KBM DBManager.", optional: true },
  { id: "monitoring", title: "Monitoring profile", description: "How often servers are sampled by default." },
  { id: "retention", title: "Retention", description: "How long metric history is kept." },
  { id: "database", title: "First database", description: "Register the first server to monitor.", optional: true },
  { id: "review", title: "Review" },
  { id: "complete", title: "Complete" },
];

const PROFILES: [string, string, string][] = [
  ["light", "Light", "Every 5 minutes; slow statistics hourly. For large estates or constrained networks."],
  ["standard", "Standard", "Every minute; slow statistics every 15 minutes. Recommended."],
  ["intensive", "Intensive", "Every 15 seconds; slow statistics every 5 minutes. For critical servers; higher load."],
];
const TIERS = ["production", "dr", "uat", "development", "test"];

function timeZones(): string[] {
  try {
    const f = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
    if (f) return f("timeZone");
  } catch { /* old browser */ }
  return ["UTC", "Europe/London", "Europe/Berlin", "Asia/Riyadh", "Asia/Kuwait", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "America/New_York", "America/Chicago", "America/Los_Angeles", "Australia/Sydney"];
}

function Row({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return <div className="flex gap-3 rounded-md border border-border p-3"><span className="text-muted [&_svg]:size-5">{icon}</span><div><div className="text-[13.5px] font-medium">{title}</div><div className="text-[12.5px] text-muted">{children}</div></div></div>;
}

export function SetupWizardPage() {
  const { me, can, refresh } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ["setup"], queryFn: () => get<SetupState>("/platform/setup") });
  const sites = useSites();
  const envs = useEnvironments();
  const [step, setStep] = useState<string>("welcome");
  const [done, setDone] = useState<string[]>([]);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });
  const [changePw, setChangePw] = useState(false);
  const [org, setOrg] = useState({ name: "", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" });
  const [site, setSite] = useState({ name: "", region: "", provider: "on-prem" });
  const [env, setEnv] = useState({ name: "", tier: "production" });
  const [auth, setAuth] = useState("local");
  const [idpDialog, setIdpDialog] = useState<"ldap" | "oidc" | null>(null);
  const [profile, setProfile] = useState("standard");
  const [days, setDays] = useState("30");
  const [onboarding, setOnboarding] = useState(false);
  const [busy, setBusy] = useState(false);
  const zones = useMemo(timeZones, []);

  const retention = useQuery({ queryKey: ["monitoring-storage"], queryFn: () => get<{ retention: { desired_days: number } }>("/platform/monitoring-storage"), enabled: can("collectors.view") });
  useEffect(() => { if (retention.data) setDays(String(retention.data.retention.desired_days)); }, [retention.data]);

  // resume from the server-side state once
  const [resumed, setResumed] = useState(false);
  useEffect(() => {
    const d = q.data;
    if (!d || resumed) return;
    setResumed(true);
    setDone(d.completed_steps ?? []);
    if (d.current_step && SETUP_STEPS.some((s) => s.id === d.current_step)) setStep(d.completed ? "complete" : d.current_step);
    if (d.organization) setOrg({ name: d.organization.name ?? "", timezone: d.organization.timezone || org.timezone });
    const data = d.data ?? {};
    if (typeof data.authentication?.method === "string") setAuth(data.authentication.method);
    if (typeof data.monitoring?.profile === "string") setProfile(data.monitoring.profile);
  }, [q.data, resumed]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveProgress = useMutation({
    mutationFn: (body: { current_step: string; completed_steps: string[]; data?: Record<string, unknown> }) => put<SetupState>("/platform/setup", body),
    onSuccess: (d) => qc.setQueryData(["setup"], d),
  });

  const ids = SETUP_STEPS.map((s) => s.id);
  const advance = async (data?: Record<string, unknown>) => {
    const nextId = ids[ids.indexOf(step) + 1];
    const completed = [...new Set([...done, step])];
    setDone(completed);
    setStep(nextId);
    try { await saveProgress.mutateAsync({ current_step: nextId, completed_steps: completed, data: data ? { [step]: data } : undefined }); }
    catch (e) { toast.error(`Progress not saved: ${(e as ApiError).message}`); }
  };

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); } catch (e) {
      const err = e as ApiError;
      const details = (err.details?.password as string[] | undefined)?.join("; ");
      toast.error(details ? `${err.message}: ${details}` : err.message);
      return false;
    } finally { setBusy(false); }
    return true;
  };

  const next = async () => {
    const e: Record<string, string> = {};
    switch (step) {
      case "admin": {
        if (!me?.must_change_password && !changePw) return advance({ password_changed: true });
        if (!pw.current) e.current = "Enter the current password";
        if (pw.next.length < 12) e.next = "At least 12 characters";
        if (pw.next !== pw.confirm) e.confirm = "The passwords don't match";
        setErrors(e);
        if (Object.keys(e).length) return;
        if (await run(() => post("/auth/change-password", { current_password: pw.current, new_password: pw.next }))) {
          setPw({ current: "", next: "", confirm: "" }); setChangePw(false); refresh(); toast.success("Password changed");
          return advance({ password_changed: true });
        }
        return;
      }
      case "organization":
        if (!org.name.trim()) { setErrors({ org: "Enter the organization name" }); return; }
        if (await run(() => post("/platform/setup/organization", { name: org.name.trim(), timezone: org.timezone }))) return advance({ name: org.name.trim(), timezone: org.timezone });
        return;
      case "environments":
        if (!(envs.data ?? []).length) { setErrors({ env: "Create at least one environment" }); return; }
        return advance({ count: envs.data?.length });
      case "sites": return advance({ count: sites.data?.length ?? 0 });
      case "authentication": return advance({ method: auth });
      case "monitoring":
        if (await run(() => post("/platform/setup/monitoring", { profile }))) return advance({ profile });
        return;
      case "retention": {
        const n = Number(days);
        if (!Number.isInteger(n) || n < 1 || n > 3650) { setErrors({ days: "1–3650 days" }); return; }
        if (await run(() => put("/platform/monitoring-storage/retention", { days: n }))) return advance({ days: n });
        return;
      }
      case "review":
        if (await run(async () => { const d = await post<SetupState>("/platform/setup/complete"); qc.setQueryData(["setup"], d); })) {
          setDone((x) => [...new Set([...x, "review"])]); setStep("complete");
          qc.invalidateQueries({ queryKey: ["setup-checklist"] });
        }
        return;
      default: return advance();
    }
  };

  const addSite = useMutation({
    mutationFn: () => post("/sites", { name: site.name.trim(), region: site.region || null, provider: site.provider || null }),
    onSuccess: () => { setSite({ name: "", region: "", provider: site.provider }); qc.invalidateQueries({ queryKey: ["sites"] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const addEnv = useMutation({
    mutationFn: () => post("/environments", { name: env.name.trim(), tier: env.tier }),
    onSuccess: () => { setEnv({ name: "", tier: env.tier }); setErrors({}); qc.invalidateQueries({ queryKey: ["environments"] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });

  if (!can("settings.manage")) return <Card><EmptyState title="Setup is for platform administrators">Ask an administrator with the settings.manage permission to complete the setup.</EmptyState></Card>;
  if (q.isLoading) return <SkeletonRows rows={10} />;
  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;

  const content = () => {
    switch (step) {
      case "welcome": return (
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="mb-2 flex flex-wrap items-center gap-4 sm:col-span-2">
            <BrandLogo width={150} />
            <p className="max-w-sm text-[13px] text-muted">Welcome to KBM DBManager. These steps prepare the platform; each one can be changed later under Platform.</p>
          </div>
          <Row icon={<UserCog />} title="Secure the administrator">Replace the bootstrap password.</Row>
          <Row icon={<Building2 />} title="Organization and time zone">Used by reports, schedules and the audit trail.</Row>
          <Row icon={<Layers />} title="Sites and environments">Where servers live and how critical they are.</Row>
          <Row icon={<KeyRound />} title="Sign-in">Local accounts, LDAP / Active Directory or single sign-on.</Row>
          <Row icon={<Gauge />} title="Monitoring and retention">Sampling frequency and how long history is kept.</Row>
          <Row icon={<Database />} title="First database">Register a server with a least-privilege account.</Row>
        </div>);
      case "admin": return me?.must_change_password || changePw ? (
        <div className="grid max-w-md gap-3">
          <p className="text-[12.5px] text-muted">The bootstrap password must be replaced. At least 12 characters with three of: lowercase, uppercase, digit, symbol.</p>
          <Field label="Current password" error={errors.current}><Input type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
          <Field label="New password" error={errors.next}><Input type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></Field>
          <Field label="Confirm new password" error={errors.confirm}><Input type="password" autoComplete="new-password" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} /></Field>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="flex items-center gap-2 text-[13px] text-ok"><CheckCircle2 className="size-4" aria-hidden /> {me?.username} already uses a personal password.</p>
          <Button size="sm" variant="ghost" onClick={() => setChangePw(true)}>Change it anyway</Button>
        </div>);
      case "organization": return (
        <div className="grid max-w-xl gap-3 sm:grid-cols-2">
          <Field label="Organization name" error={errors.org}><Input value={org.name} onChange={(e) => { setOrg({ ...org, name: e.target.value }); setErrors({}); }} /></Field>
          <Field label="Time zone" hint="IANA time zone"><Select value={org.timezone} onChange={(e) => setOrg({ ...org, timezone: e.target.value })}>{zones.map((z) => <option key={z} value={z}>{z}</option>)}</Select></Field>
        </div>);
      case "sites": return (
        <div className="space-y-3">
          <ul className="flex flex-wrap gap-1.5" aria-label="Sites">{(sites.data ?? []).map((x) => <li key={x.id}><Badge tone="outline"><MapPin className="size-3" aria-hidden /> {x.name}{x.region ? ` · ${x.region}` : ""}</Badge></li>)}{!sites.data?.length && <li className="text-[12.5px] text-muted">No sites yet.</li>}</ul>
          <form className="grid gap-2 sm:grid-cols-[1fr_160px_150px_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); if (site.name.trim()) addSite.mutate(); }}>
            <Field label="Site name"><Input value={site.name} onChange={(e) => setSite({ ...site, name: e.target.value })} placeholder="Riyadh DC1" /></Field>
            <Field label="Region"><Input value={site.region} onChange={(e) => setSite({ ...site, region: e.target.value })} placeholder="me-central-1" /></Field>
            <Field label="Provider"><Select value={site.provider} onChange={(e) => setSite({ ...site, provider: e.target.value })}>{["on-prem", "aws", "azure", "gcp", "oci", "ibm"].map((p) => <option key={p} value={p}>{p}</option>)}</Select></Field>
            <Button type="submit" disabled={!site.name.trim() || addSite.isPending}><Plus /> Add site</Button>
          </form>
        </div>);
      case "environments": return (
        <div className="space-y-3">
          <ul className="flex flex-wrap gap-1.5" aria-label="Environments">{(envs.data ?? []).map((x) => <li key={x.id}><Badge tone={x.tier === "production" ? "crit" : x.tier === "dr" ? "warn" : "neutral"}>{x.name} · {x.tier}</Badge></li>)}</ul>
          {errors.env && <p role="alert" className="text-[12.5px] text-crit">{errors.env}</p>}
          <form className="grid gap-2 sm:grid-cols-[1fr_180px_auto] sm:items-end" onSubmit={(e) => { e.preventDefault(); if (env.name.trim()) addEnv.mutate(); }}>
            <Field label="Environment name"><Input value={env.name} onChange={(e) => setEnv({ ...env, name: e.target.value })} placeholder="Production" /></Field>
            <Field label="Tier"><Select value={env.tier} onChange={(e) => setEnv({ ...env, tier: e.target.value })}>{TIERS.map((t) => <option key={t} value={t}>{t}</option>)}</Select></Field>
            <Button type="submit" disabled={!env.name.trim() || addEnv.isPending}><Plus /> Add environment</Button>
          </form>
          <p className="text-[12.5px] text-muted">Production servers get stricter confirmation for administration actions and stricter security rules.</p>
        </div>);
      case "authentication": return (
        <div className="space-y-2" role="radiogroup" aria-label="Sign-in method">
          <ChoiceCard name="auth" selected={auth === "local"} onSelect={() => setAuth("local")} icon={<UserCog />} title="Local accounts only">Users are created in KBM DBManager. You can add a directory later.</ChoiceCard>
          <ChoiceCard name="auth" selected={auth === "ldap"} onSelect={() => setAuth("ldap")} icon={<ShieldCheck />} title="LDAP / Active Directory">Users sign in with their directory account; groups map to roles.</ChoiceCard>
          <ChoiceCard name="auth" selected={auth === "oidc"} onSelect={() => setAuth("oidc")} icon={<KeyRound />} title="Single sign-on (OpenID Connect)">Keycloak, Microsoft Entra ID, Okta or any OIDC provider.</ChoiceCard>
          {auth !== "local" && <Button onClick={() => setIdpDialog(auth as "ldap" | "oidc")}>Configure {auth === "ldap" ? "LDAP" : "OIDC"} now</Button>}
          <p className="text-[12.5px] text-muted">Local administrator sign-in always stays available as a break-glass path.</p>
        </div>);
      case "monitoring": return (
        <div className="space-y-2" role="radiogroup" aria-label="Monitoring profile">
          {PROFILES.map(([k, l, h]) => <ChoiceCard key={k} name="profile" selected={profile === k} onSelect={() => setProfile(k)} icon={<Timer />} title={l} badge={k === "standard" ? <Badge tone="ok">recommended</Badge> : undefined}>{h}</ChoiceCard>)}
          <p className="text-[12.5px] text-muted">Each server can override its interval later.</p>
        </div>);
      case "retention": return (
        <div className="max-w-xs">
          <Field label="Keep metric history for (days)" hint="Applied to the time-series storage" error={errors.days}><Input inputMode="numeric" value={days} onChange={(e) => { setDays(e.target.value); setErrors({}); }} /></Field>
        </div>);
      case "database": return (
        <div className="space-y-3">
          <p className="text-[13px]">Register a server now with a least-privilege monitoring account, or skip and do it later from Fleet.</p>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" onClick={() => setOnboarding(true)}><Database /> Add a database server</Button>
            <Button asChild><Link to="/fleet/onboarding/bulk">Bulk onboarding</Link></Button>
          </div>
        </div>);
      case "review": {
        const items: [string, ReactNode][] = [
          ["Organization", `${org.name || q.data?.organization?.name || "—"} (${org.timezone})`],
          ["Sites", String(sites.data?.length ?? 0)], ["Environments", (envs.data ?? []).map((e) => e.name).join(", ") || "—"],
          ["Sign-in", auth === "local" ? "Local accounts" : auth.toUpperCase()], ["Monitoring profile", profile], ["Retention", `${days} days`],
        ];
        return <dl className="divide-y divide-border rounded-md border border-border text-[13px]">{items.map(([k, v]) => <div key={k} className="grid grid-cols-[170px_1fr] gap-3 px-3 py-2"><dt className="text-muted">{k}</dt><dd>{v}</dd></div>)}</dl>;
      }
      case "complete": return (
        <SuccessPanel title="KBM DBManager is ready" actions={<><Button variant="primary" onClick={() => navigate("/")}><Rocket /> Go to the dashboard</Button><Button asChild><Link to="/setup/checklist">Open the checklist</Link></Button></>}>
          Setup is complete. The checklist shows what is left to make the most of the platform.
        </SuccessPanel>);
      default: return null;
    }
  };

  return (
    <div>
      <PageHeader title="Platform setup" crumbs={[{ label: "Platform" }, { label: "Setup" }]}
        meta={q.data?.completed ? <Badge tone="ok">completed</Badge> : <span>{done.length} of {SETUP_STEPS.length - 1} steps done</span>} />
      <Card><CardBody>
        <Wizard title="Setup" steps={SETUP_STEPS} current={step} completed={done} onStepChange={setStep} hideNav={step === "complete"} busy={busy || saveProgress.isPending}
          onBack={() => setStep(ids[Math.max(0, ids.indexOf(step) - 1)])} onNext={next}
          nextLabel={step === "welcome" ? "Get started" : step === "review" ? "Finish setup" : SETUP_STEPS.find((s) => s.id === step)?.optional ? "Continue (optional)" : "Continue"}>
          {content()}
        </Wizard>
      </CardBody></Card>
      {idpDialog && <IdentityWizardDialog kind={idpDialog} onClose={() => setIdpDialog(null)} />}
      <OnboardingWizard open={onboarding} onOpenChange={setOnboarding} />
    </div>
  );
}
