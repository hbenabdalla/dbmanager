/** LDAP and OIDC configuration wizards with staged draft tests (POST /identity-providers/test-draft).
 * Stored secrets are never displayed: an existing secret shows as "stored" and is reused by the test. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Play } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, get, patch, post } from "@/api/client";
import type { IdentityProvider } from "@/api/types";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { ChoiceCard, StageList, SuccessPanel, Wizard, type Stage, type WizardStep } from "@/components/wizard";
import { useAuth } from "@/lib/auth";
import { EmptyState } from "@/components/ui/states";
import { OidcMfaFields } from "./oidc-mfa";

interface DraftResult { ok: boolean; stages: Stage[] }
export interface OidcPreset {
  key: string; label: string; issuer_template: string; issuer_help?: string; scopes: string[];
  username_claim?: string; email_claim?: string; groups_claim?: string; notes?: string;
}

export const LDAP_STAGE_LABELS: Record<string, string> = {
  dns: "Resolve directory host", tcp: "Open connection", tls: "TLS (LDAPS / StartTLS)", bind: "Service account bind",
  user_search: "Find the test user", group_lookup: "Read group membership", user_bind: "Test user sign-in",
};
export const OIDC_STAGE_LABELS: Record<string, string> = {
  discovery: "Fetch discovery document", issuer_match: "Issuer matches", jwks: "Signing keys (JWKS)", endpoints: "Authorization and token endpoints",
  redirect_uri: "Redirect URI",
};

function useDraftTest() {
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => post<DraftResult>("/identity-providers/test-draft", body),
    onError: (e: ApiError) => toast.error(e.message),
  });
}

function SecretField({ label, has, value, onChange }: { label: string; has: boolean; value: string; onChange: (v: string) => void }) {
  return (
    <Field label={label} hint={has ? "A secret is stored (encrypted). Leave empty to keep it." : "Stored encrypted; never shown again."}>
      <Input type="password" autoComplete="new-password" value={value} placeholder={has ? "•••••••• stored" : ""} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

function strip(cfg: Record<string, unknown>) {
  return Object.fromEntries(Object.entries(cfg).filter(([k, v]) => k !== "redirect_uri_effective" && v !== "" && v !== undefined));
}

/* ------------------------------------------------------------------ LDAP */
const LDAP_STEPS: WizardStep[] = [
  { id: "server", title: "Directory server", description: "LDAPS (ldaps://, port 636) or ldap:// with StartTLS. Plain LDAP is not accepted for sign-in." },
  { id: "bind", title: "Service account", description: "The account KBM DBManager uses to search for users. Users then bind with their own password." },
  { id: "search", title: "User and group search", description: "How users and their groups are found." },
  { id: "test", title: "Test", description: "Staged checks against the directory. Optionally sign in as a test user." },
  { id: "save", title: "Save", description: "Name the provider and decide whether it is offered on the sign-in page." },
  { id: "done", title: "Done" },
];

export function LdapWizard({ provider, onDone, onCancel }: { provider?: IdentityProvider | null; onDone?: (p: IdentityProvider) => void; onCancel?: () => void }) {
  const [step, setStep] = useState("server");
  const [done, setDone] = useState<string[]>([]);
  const [cfg, setCfg] = useState<Record<string, unknown>>(provider?.config ?? {
    server_uri: "", start_tls: false, verify_cert: true, bind_dn: "", user_base_dn: "",
    user_filter: "(&(objectClass=user)(sAMAccountName={username}))", attr_username: "sAMAccountName", attr_email: "mail",
    attr_display_name: "displayName", group_attribute: "memberOf",
  });
  const [secret, setSecret] = useState("");
  const [testUser, setTestUser] = useState("");
  const [testPw, setTestPw] = useState("");
  const [name, setName] = useState(provider?.name ?? "Active Directory");
  const [enabled, setEnabled] = useState(provider?.enabled ?? false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<IdentityProvider | null>(null);
  const test = useDraftTest();
  const qc = useQueryClient();
  const s = (k: string, v: unknown) => { setCfg((c) => ({ ...c, [k]: v })); setErrors((e) => ({ ...e, [k]: "" })); };
  const uri = String(cfg.server_uri ?? "");

  const validate = (id: string) => {
    const e: Record<string, string> = {};
    if (id === "server") {
      if (!/^ldaps?:\/\/[^\s/]+/i.test(uri)) e.server_uri = "Use ldaps://host[:port] or ldap://host[:port]";
      else if (uri.toLowerCase().startsWith("ldap://") && !cfg.start_tls) e.server_uri = "ldap:// needs StartTLS; or use ldaps://";
    }
    if (id === "bind") {
      if (!String(cfg.bind_dn ?? "").trim()) e.bind_dn = "Enter the service account DN";
      if (!secret && !provider?.has_secret) e.secret = "Enter the service account password";
    }
    if (id === "search") {
      if (!String(cfg.user_base_dn ?? "").trim()) e.user_base_dn = "Enter the search base";
      if (!String(cfg.user_filter ?? "").includes("{username}")) e.user_filter = "The filter must contain {username}";
    }
    if (id === "save" && !name.trim()) e.name = "Enter a display name";
    return e;
  };
  const ids = LDAP_STEPS.map((x) => x.id);
  const save = useMutation({
    mutationFn: () => {
      const config = strip(cfg);
      return provider
        ? patch<IdentityProvider>(`/identity-providers/${provider.id}`, { name, enabled, config, secret: secret || undefined })
        : post<IdentityProvider>("/identity-providers", { kind: "ldap", name, enabled, config, secret: secret || undefined });
    },
    onSuccess: (p) => { setSecret(""); setTestPw(""); setSaved(p); setStep("done"); qc.invalidateQueries({ queryKey: ["idps"] }); qc.invalidateQueries({ queryKey: ["setup-checklist"] }); },
    onError: (e: ApiError) => toast.error(e.message, { description: (e.details?.missing as string[] | undefined)?.join(", ") }),
  });
  const next = () => {
    const e = validate(step); setErrors(e);
    if (Object.values(e).some(Boolean)) return;
    setDone((d) => [...new Set([...d, step])]);
    if (step === "save") { save.mutate(); return; }
    setStep(ids[ids.indexOf(step) + 1]);
  };
  const runTest = () => test.mutate({
    kind: "ldap", config: strip(cfg), secret: secret || undefined, provider_id: provider?.id,
    test_username: testUser || undefined, test_password: testPw || undefined,
  });
  const stages = test.data?.stages ?? [];

  const body = () => {
    switch (step) {
      case "server": return (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Server URI" className="sm:col-span-2" hint="e.g. ldaps://dc01.corp.example:636" error={errors.server_uri}><Input value={uri} onChange={(e) => s("server_uri", e.target.value)} placeholder="ldaps://dc01.corp.example:636" /></Field>
          <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={Boolean(cfg.start_tls)} onChange={(e) => s("start_tls", e.target.checked)} /> Use StartTLS (ldap:// URIs)</label>
          <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={cfg.verify_cert !== false} onChange={(e) => s("verify_cert", e.target.checked)} /> Verify the directory certificate</label>
          {cfg.verify_cert === false && <p className="text-[12.5px] text-warn sm:col-span-2">Without certificate verification, passwords can be intercepted by an impersonating server.</p>}
        </div>);
      case "bind": return (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Service account DN" className="sm:col-span-2" error={errors.bind_dn}><Input value={String(cfg.bind_dn ?? "")} onChange={(e) => s("bind_dn", e.target.value)} placeholder="CN=svc-dbm,OU=Service,DC=corp,DC=example" /></Field>
          <div><SecretField label="Service account password" has={!!provider?.has_secret} value={secret} onChange={(v) => { setSecret(v); setErrors((e) => ({ ...e, secret: "" })); }} />{errors.secret && <p className="mt-1 text-[12px] text-crit">{errors.secret}</p>}</div>
        </div>);
      case "search": return (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="User search base" className="sm:col-span-2" error={errors.user_base_dn}><Input value={String(cfg.user_base_dn ?? "")} onChange={(e) => s("user_base_dn", e.target.value)} placeholder="OU=Users,DC=corp,DC=example" /></Field>
          <Field label="User filter" className="sm:col-span-2" hint="{username} is replaced (escaped) with the sign-in name" error={errors.user_filter}><Input value={String(cfg.user_filter ?? "")} onChange={(e) => s("user_filter", e.target.value)} /></Field>
          {([["attr_username", "Username attribute"], ["attr_email", "Email attribute"], ["attr_display_name", "Display name attribute"], ["group_attribute", "Group attribute"]] as const).map(([k, l]) => (
            <Field key={k} label={l}><Input value={String(cfg[k] ?? "")} onChange={(e) => s(k, e.target.value)} /></Field>
          ))}
        </div>);
      case "test": return (
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Test user (optional)" hint="Checks search, groups and a real sign-in"><Input autoComplete="off" value={testUser} onChange={(e) => setTestUser(e.target.value)} /></Field>
            <Field label="Test user password (optional)" hint="Used once for this test; never stored"><Input type="password" autoComplete="new-password" value={testPw} onChange={(e) => setTestPw(e.target.value)} /></Field>
          </div>
          <Button variant="primary" onClick={runTest} disabled={test.isPending}><Play /> {test.isPending ? "Testing…" : stages.length ? "Run again" : "Run test"}</Button>
          {test.isPending && !stages.length && <StageList stages={Object.keys(LDAP_STAGE_LABELS).map((k) => ({ stage: k, status: k === "dns" ? "running" : "pending" }))} labels={LDAP_STAGE_LABELS} />}
          {stages.length > 0 && <StageList stages={stages} labels={LDAP_STAGE_LABELS} />}
          {test.data && <p role="status" className={test.data.ok ? "text-[13px] text-ok" : "text-[13px] text-crit"}>{test.data.ok ? "All checks passed." : "A check failed: fix the step it points to and test again."}</p>}
        </div>);
      case "save": return (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Display name" error={errors.name}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <label className="flex items-center gap-2 pt-6 text-[13px]"><Switch checked={enabled} onCheckedChange={setEnabled} label="Enabled" /> Offer on the sign-in page</label>
          {!test.data?.ok && <p className="text-[12.5px] text-warn sm:col-span-2">The configuration has not passed a test yet. You can save it disabled and test later.</p>}
          <p className="text-[12.5px] text-muted sm:col-span-2">After saving, map directory groups to platform groups so users receive roles.</p>
        </div>);
      case "done": return (
        <SuccessPanel title={`${saved?.name ?? name} saved`} actions={<Button variant="primary" onClick={() => saved && onDone?.(saved)}>Map groups</Button>}>
          {enabled ? "Users can now sign in with their directory account." : "The provider is saved but disabled."} Group mappings decide which roles they receive.
        </SuccessPanel>);
      default: return null;
    }
  };
  return (
    <Wizard title="LDAP" steps={LDAP_STEPS} current={step} completed={done} onStepChange={step === "done" ? undefined : setStep} hideNav={step === "done"}
      onBack={() => setStep(ids[Math.max(0, ids.indexOf(step) - 1)])} onNext={next} busy={save.isPending}
      nextLabel={step === "save" ? "Save provider" : "Continue"} extraActions={onCancel && <Button variant="ghost" onClick={onCancel}>Cancel</Button>}>
      {body()}
    </Wizard>
  );
}

/* ------------------------------------------------------------------ OIDC */
const OIDC_STEPS: WizardStep[] = [
  { id: "preset", title: "Provider", description: "Pick your identity provider for sensible defaults." },
  { id: "client", title: "Client", description: "The application registration in your identity provider." },
  { id: "claims", title: "Claims", description: "Which token claims identify the user and their groups." },
  { id: "test", title: "Test", description: "Checks discovery, issuer, signing keys and endpoints." },
  { id: "save", title: "Save" },
  { id: "done", title: "Done" },
];

const FALLBACK_PRESETS: OidcPreset[] = [{ key: "generic", label: "Generic OpenID Connect", issuer_template: "https://idp.example.com", scopes: ["openid", "profile", "email"], username_claim: "preferred_username", email_claim: "email", groups_claim: "groups" }];

export function OidcWizard({ provider, onDone, onCancel }: { provider?: IdentityProvider | null; onDone?: (p: IdentityProvider) => void; onCancel?: () => void }) {
  const presetsQ = useQuery({ queryKey: ["idp-presets"], queryFn: () => get<OidcPreset[]>("/identity-providers/presets"), staleTime: 300_000 });
  const presets = presetsQ.data?.length ? presetsQ.data : FALLBACK_PRESETS;
  const [step, setStep] = useState(provider ? "client" : "preset");
  const [done, setDone] = useState<string[]>(provider ? ["preset"] : []);
  const [preset, setPreset] = useState<string>(String(provider?.config.preset ?? "generic"));
  const [cfg, setCfg] = useState<Record<string, unknown>>(provider?.config ?? { issuer: "", client_id: "", scopes: "openid profile email", username_claim: "preferred_username", email_claim: "email", groups_claim: "groups" });
  const [secret, setSecret] = useState("");
  const [name, setName] = useState(provider?.name ?? "Single sign-on");
  const [enabled, setEnabled] = useState(provider?.enabled ?? false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<IdentityProvider | null>(null);
  const test = useDraftTest();
  const qc = useQueryClient();
  const s = (k: string, v: unknown) => { setCfg((c) => ({ ...c, [k]: v })); setErrors((e) => ({ ...e, [k]: "" })); };
  const p = useMemo(() => presets.find((x) => x.key === preset) ?? presets[0], [presets, preset]);

  const applyPreset = (k: string) => {
    const pr = presets.find((x) => x.key === k);
    setPreset(k);
    if (pr) {
      setCfg((c) => ({ ...c, preset: k, issuer: c.issuer || "", scopes: pr.scopes.join(" "), username_claim: pr.username_claim ?? c.username_claim, email_claim: pr.email_claim ?? c.email_claim, groups_claim: pr.groups_claim ?? c.groups_claim }));
      setName((n) => (n === "Single sign-on" || presets.some((x) => x.label === n) ? pr.label : n));
    }
  };
  const validate = (id: string) => {
    const e: Record<string, string> = {};
    if (id === "client") {
      const iss = String(cfg.issuer ?? "");
      if (!/^https:\/\/\S+$/.test(iss)) e.issuer = "The issuer must be an https:// URL";
      else if (iss.includes("<")) e.issuer = "Replace the <placeholders> in the issuer";
      if (!String(cfg.client_id ?? "").trim()) e.client_id = "Enter the client ID";
    }
    if (id === "claims" && !String(cfg.scopes ?? "").split(/\s+/).includes("openid")) e.scopes = "Scopes must include openid";
    if (id === "save" && !name.trim()) e.name = "Enter a display name";
    return e;
  };
  const ids = OIDC_STEPS.map((x) => x.id);
  const save = useMutation({
    mutationFn: () => {
      const config = strip(cfg);
      return provider
        ? patch<IdentityProvider>(`/identity-providers/${provider.id}`, { name, enabled, config, secret: secret || undefined })
        : post<IdentityProvider>("/identity-providers", { kind: "oidc", name, enabled, config, secret: secret || undefined });
    },
    onSuccess: (r) => { setSecret(""); setSaved(r); setStep("done"); qc.invalidateQueries({ queryKey: ["idps"] }); qc.invalidateQueries({ queryKey: ["setup-checklist"] }); },
    onError: (e: ApiError) => toast.error(e.message, { description: (e.details?.missing as string[] | undefined)?.join(", ") }),
  });
  const next = () => {
    const e = validate(step); setErrors(e);
    if (Object.values(e).some(Boolean)) return;
    setDone((d) => [...new Set([...d, step])]);
    if (step === "save") { save.mutate(); return; }
    setStep(ids[ids.indexOf(step) + 1]);
  };
  const stages = test.data?.stages ?? [];

  const body = () => {
    switch (step) {
      case "preset": return (
        <div role="radiogroup" aria-label="Identity provider" className="grid gap-2 sm:grid-cols-2">
          {presets.map((x) => <ChoiceCard key={x.key} name="preset" selected={preset === x.key} onSelect={() => applyPreset(x.key)} icon={<KeyRound />} title={x.label}>{x.notes ?? x.issuer_template}</ChoiceCard>)}
        </div>);
      case "client": return (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Issuer URL" className="sm:col-span-2" hint={p?.issuer_help ?? `e.g. ${p?.issuer_template}`} error={errors.issuer}><Input value={String(cfg.issuer ?? "")} placeholder={p?.issuer_template} onChange={(e) => s("issuer", e.target.value)} /></Field>
          <Field label="Client ID" error={errors.client_id}><Input value={String(cfg.client_id ?? "")} onChange={(e) => s("client_id", e.target.value)} /></Field>
          <SecretField label="Client secret" has={!!provider?.has_secret} value={secret} onChange={setSecret} />
          {provider?.config.redirect_uri_effective ? <Field label="Redirect URI to register with the provider" className="sm:col-span-2"><Input readOnly value={String(provider.config.redirect_uri_effective)} className="font-mono text-[12px]" /></Field>
            : <p className="text-[12.5px] text-muted sm:col-span-2">The redirect URI to register with your provider is shown after saving (and checked by the test).</p>}
        </div>);
      case "claims": return (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Scopes" className="sm:col-span-2" error={errors.scopes}><Input value={String(cfg.scopes ?? "")} onChange={(e) => s("scopes", e.target.value)} /></Field>
          <Field label="Username claim"><Input value={String(cfg.username_claim ?? "")} onChange={(e) => s("username_claim", e.target.value)} /></Field>
          <Field label="Email claim"><Input value={String(cfg.email_claim ?? "")} onChange={(e) => s("email_claim", e.target.value)} /></Field>
          <Field label="Groups claim" hint="Dotted paths work, e.g. realm_access.roles"><Input value={String(cfg.groups_claim ?? "")} onChange={(e) => s("groups_claim", e.target.value)} /></Field>
          <OidcMfaFields cfg={cfg} onChange={(p) => setCfg((c) => ({ ...c, ...p }))} />
        </div>);
      case "test": return (
        <div className="space-y-3">
          <Button variant="primary" disabled={test.isPending} onClick={() => test.mutate({ kind: "oidc", config: strip(cfg), secret: secret || undefined, provider_id: provider?.id })}><Play /> {test.isPending ? "Testing…" : stages.length ? "Test again" : "Test"}</Button>
          {stages.length > 0 && <StageList stages={stages} labels={OIDC_STAGE_LABELS} />}
          {test.data && <p role="status" className={test.data.ok ? "text-[13px] text-ok" : "text-[13px] text-crit"}>{test.data.ok ? "The provider configuration looks correct." : "A check failed."}</p>}
        </div>);
      case "save": return (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Display name" hint="Shown on the sign-in button" error={errors.name}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <label className="flex items-center gap-2 pt-6 text-[13px]"><Switch checked={enabled} onCheckedChange={setEnabled} label="Enabled" /> Offer on the sign-in page</label>
        </div>);
      case "done": return (
        <SuccessPanel title={`${saved?.name ?? name} saved`} actions={<Button variant="primary" onClick={() => saved && onDone?.(saved)}>Map groups</Button>}>
          {saved?.config.redirect_uri_effective ? <>Register this redirect URI with your provider: <code className="break-all font-mono text-[12px]">{String(saved.config.redirect_uri_effective)}</code></> : "Saved."}
        </SuccessPanel>);
      default: return null;
    }
  };
  return (
    <Wizard title="OIDC" steps={OIDC_STEPS} current={step} completed={done} onStepChange={step === "done" ? undefined : setStep} hideNav={step === "done"}
      onBack={() => setStep(ids[Math.max(0, ids.indexOf(step) - 1)])} onNext={next} busy={save.isPending}
      nextLabel={step === "save" ? "Save provider" : "Continue"} extraActions={onCancel && <Button variant="ghost" onClick={onCancel}>Cancel</Button>}>
      {body()}
    </Wizard>
  );
}

/** Dialog wrapper used from the identity providers page. */
export function IdentityWizardDialog({ kind, provider, onClose }: { kind: "ldap" | "oidc"; provider?: IdentityProvider | null; onClose: () => void }) {
  const W = kind === "ldap" ? LdapWizard : OidcWizard;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-4xl" title={provider ? `Edit ${provider.name}` : kind === "ldap" ? "Connect LDAP / Active Directory" : "Connect an OpenID Connect provider"}>
      <W provider={provider} onDone={onClose} onCancel={onClose} />
    </Dialog>
  );
}

/** Route: /platform/identity/wizard?kind=ldap|oidc[&id=<provider id>] */
export function IdentityWizardPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { can } = useAuth();
  const kind = params.get("kind") === "oidc" ? "oidc" : "ldap";
  const choose = (k: "ldap" | "oidc") => setParams((p) => { const n = new URLSearchParams(p); n.set("kind", k); return n; }, { replace: true });
  const id = params.get("id");
  const q = useQuery({ queryKey: ["idps"], queryFn: () => get<IdentityProvider[]>("/identity-providers"), enabled: !!id && can("idp.manage") });
  const provider = id ? q.data?.find((x) => x.id === id) ?? null : null;
  const back = () => navigate(kind === "ldap" ? "/platform/ldap" : "/platform/identity");
  return (
    <div>
      <PageHeader title={kind === "ldap" ? "Connect LDAP / Active Directory" : "Connect single sign-on (OIDC)"}
        crumbs={[{ label: "Platform" }, { label: "Identity providers", to: "/platform/identity" }, { label: "Wizard" }]}
        meta={<Badge tone="outline">{kind.toUpperCase()}</Badge>} />
      {!can("idp.manage") ? <Card><EmptyState title="You can't manage identity providers">This needs the idp.manage permission.</EmptyState></Card> : (
        <Card><CardBody>
          {!id && (
            <div role="radiogroup" aria-label="Identity provider type" className="mb-4 inline-flex rounded-md border border-border p-0.5 text-[13px]">
              {([["ldap", "LDAP / Active Directory"], ["oidc", "OpenID Connect"]] as const).map(([k, label]) => (
                <button key={k} type="button" role="radio" aria-checked={kind === k} onClick={() => choose(k)}
                  className={`rounded px-3 py-1.5 transition-colors duration-150 ${kind === k ? "bg-surface-2 font-medium text-text" : "text-muted hover:text-text"}`}>{label}</button>
              ))}
            </div>
          )}
          {id && q.isLoading ? null : kind === "ldap" ? <LdapWizard provider={provider} onDone={back} onCancel={back} /> : <OidcWizard provider={provider} onDone={back} onCancel={back} />}
        </CardBody></Card>
      )}
    </div>
  );
}
