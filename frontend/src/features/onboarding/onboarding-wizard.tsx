/** Database onboarding wizard (9 steps) with a live staged connection test.
 * Monitoring Only is the default management mode; administration is never enabled automatically. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Eye, Lock, Play, ShieldAlert, Square, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, post } from "@/api/client";
import { useApplications, useClusters, useEngines, useEnvironments, useSites } from "@/api/hooks";
import type { CapabilityStatus, EngineInfo, Server, TestConnectionResult } from "@/api/types";
import { DatabaseEngineIcon } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { ChoiceCard, StageList, SuccessPanel, Wizard, type Stage, type StageStatus, type WizardStep } from "@/components/wizard";
import { DEPLOYMENTS, MON_HINT, optionPayload } from "@/features/fleet/register-server";
import { PROVIDER_LABEL, SecretSourceFields, secretRefError, type SecretProvider } from "@/features/shared/secret-source";
import { DATABASE_FIELD, ERROR_LABEL, LEVEL_LABEL, LEVEL_TONE, SUPPORT_LABEL } from "@/lib/capabilities";
import { cn } from "@/lib/utils";
import { postNdjson } from "./stream";

export const ONBOARDING_STEPS: WizardStep[] = [
  { id: "engine", title: "Engine", description: "Which database engine is this server running?" },
  { id: "connection", title: "Connection", description: "Where DBManager reaches the server." },
  { id: "tls", title: "TLS", description: "How the connection is encrypted and verified." },
  { id: "credentials", title: "Credentials", description: "A least-privilege monitoring account. Passwords are encrypted and never shown again." },
  { id: "management", title: "Management mode", description: "What DBManager is allowed to offer on this server." },
  { id: "placement", title: "Placement", description: "Where the server belongs in your estate." },
  { id: "test", title: "Test connection", description: "A live, staged test with the details above." },
  { id: "review", title: "Review", description: "Check everything before the server is registered." },
  { id: "done", title: "Done" },
];

export const STAGE_LABELS: Record<string, string> = {
  validate: "Validate settings", driver: "Database driver", dns: "Resolve host name", tcp: "Open TCP connection", tls: "TLS handshake",
  authenticate: "Authenticate", version: "Read server version", privileges: "Check privileges", capabilities: "Discover capabilities",
};
const STAGE_ORDER = Object.keys(STAGE_LABELS);

export interface OnboardingForm {
  engine: string; name: string; host: string; port: string; default_database: string; description: string; deployment: string;
  options: Record<string, string | boolean>; licensed_features: string[];
  tls_mode: string; tls_ca_pem: string;
  mon_user: string; mon_password: string; admin_user: string; admin_password: string;
  /** where the monitoring password comes from; non-local sources only store a reference */
  mon_provider: SecretProvider; mon_ref: string;
  management_mode: "monitoring" | "administration";
  environment_id: string; site_id: string; cluster_id: string; cluster_role: string; application_id: string; owner: string; tags: string;
  monitoring_enabled: boolean; collection_interval_s: string;
}

/** Monitoring credential payload: a password, or only a reference for env/file/vault. */
export function monitoringCredential(f: Pick<OnboardingForm, "mon_user" | "mon_password" | "mon_provider" | "mon_ref">) {
  return f.mon_provider === "local"
    ? { username: f.mon_user, password: f.mon_password || null }
    : { username: f.mon_user, password: null, provider: f.mon_provider, secret_ref: f.mon_ref.trim() };
}

const EMPTY: OnboardingForm = {
  engine: "", name: "", host: "", port: "", default_database: "", description: "", deployment: "self-managed", options: {}, licensed_features: [],
  tls_mode: "verify-full", tls_ca_pem: "", mon_user: "", mon_password: "", admin_user: "", admin_password: "", mon_provider: "local", mon_ref: "", management_mode: "monitoring",
  environment_id: "", site_id: "", cluster_id: "", cluster_role: "", application_id: "", owner: "", tags: "", monitoring_enabled: true,
  collection_interval_s: "60",
};

const TLS_MODES: [string, string, string][] = [
  ["verify-full", "Verify certificate and host name", "Recommended. Protects against interception."],
  ["verify-ca", "Verify certificate", "Checks the CA chain, not the host name."],
  ["require", "Encrypt, don't verify", "Encrypted but open to man-in-the-middle."],
  ["prefer", "Encrypt when available", "Falls back to plain text."],
  ["disable", "No TLS", "Credentials and data travel in clear text."],
];

export function parseTags(s: string): { key: string; value: string }[] {
  return s.split(/[,;]/).map((t) => t.trim()).filter(Boolean).map((t) => { const [key, ...v] = t.split("="); return { key: key.trim(), value: v.join("=").trim() }; });
}

/** Capability results grouped by level (FULL first). */
export function CapabilityGroups({ caps }: { caps: Record<string, CapabilityStatus> }) {
  const groups = useMemo(() => {
    const g: Record<string, [string, CapabilityStatus][]> = {};
    for (const [k, v] of Object.entries(caps)) (g[v.level] ??= []).push([k, v]);
    const order = ["FULL", "PARTIAL", "REQUIRES_CONFIGURATION", "REQUIRES_EXTENSION", "REQUIRES_PRIVILEGE", "REQUIRES_LICENSE", "REQUIRES_EXTERNAL_MONITORING", "NOT_SUPPORTED", "NOT_IMPLEMENTED", "PLANNED"];
    return Object.entries(g).sort(([a], [b]) => order.indexOf(a) - order.indexOf(b));
  }, [caps]);
  return (
    <div className="space-y-3" aria-label="Capabilities">
      {groups.map(([level, items]) => (
        <div key={level}>
          <div className="mb-1 flex items-center gap-2 text-[12.5px] font-medium"><Badge tone={LEVEL_TONE[level] ?? "neutral"}>{LEVEL_LABEL[level] ?? level}</Badge><span className="text-muted">{items.length}</span></div>
          <ul className="flex flex-wrap gap-1.5">
            {items.map(([k, v]) => <li key={k} title={v.note ?? undefined} className="rounded border border-border px-1.5 py-0.5 text-[12px] text-text-2">{k.replaceAll("_", " ")}{v.note && level !== "FULL" ? <span className="text-muted"> — {v.note}</span> : null}</li>)}
          </ul>
        </div>
      ))}
    </div>
  );
}

function EngineBanner({ engine }: { engine: EngineInfo }) {
  if (engine.support_level === "supported" && engine.driver_status === "installed") return null;
  return (
    <div className="rounded-md border border-info/30 bg-info-soft px-3 py-2 text-[12.5px] text-info">
      {engine.implemented ? <><Badge tone={SUPPORT_LABEL[engine.support_level ?? "supported"]?.tone ?? "info"}>{SUPPORT_LABEL[engine.support_level ?? "supported"]?.label}</Badge> {engine.support_note}</> : `No ${engine.display_name} adapter is available; the server is kept for inventory only.`}
      {engine.driver_status === "missing" && <span role="alert" className="mt-1 block font-medium text-crit">{engine.driver_detail?.remediation ?? `The ${engine.driver} driver is missing in this backend.`} The connection test will fail until it is installed (Platform → Database drivers).</span>}
      {engine.driver_status === "degraded" && <span className="mt-1 block">Driver: {engine.driver_detail?.error ?? "degraded"}.</span>}
    </div>
  );
}

export function OnboardingWizard({ open, onOpenChange, defaultEnvironmentId }: { open: boolean; onOpenChange: (o: boolean) => void; defaultEnvironmentId?: string }) {
  const [step, setStep] = useState("engine");
  const [done, setDone] = useState<string[]>([]);
  const [f, setF] = useState<OnboardingForm>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [stages, setStages] = useState<Stage[]>([]);
  const [result, setResult] = useState<TestConnectionResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [created, setCreated] = useState<Server | null>(null);
  const abort = useRef<AbortController | null>(null);
  const engines = useEngines();
  const envs = useEnvironments();
  const sites = useSites();
  const clusters = useClusters();
  const apps = useApplications();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const set = <K extends keyof OnboardingForm>(k: K, v: OnboardingForm[K]) => { setF((p) => ({ ...p, [k]: v })); setErrors((e) => ({ ...e, [k]: "" })); };
  const engine = engines.data?.find((e) => e.engine === f.engine);
  const dbField = DATABASE_FIELD[f.engine] ?? DATABASE_FIELD.postgresql;
  const optionDefs = (engine?.connection_options ?? []).filter((o) => !(f.engine === "oracle" && o.key === "service_name"));
  const options = optionPayload(optionDefs, f.options);

  const reset = () => {
    setF({ ...EMPTY, environment_id: defaultEnvironmentId ?? envs.data?.find((e) => e.tier === "development")?.id ?? "" });
    setStep("engine"); setDone([]); setErrors({}); setStages([]); setResult(null); setCreated(null);
  };
  useEffect(() => { if (open) reset(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => abort.current?.abort(), []);

  const pickEngine = (e: EngineInfo) => {
    const dbf = DATABASE_FIELD[e.engine] ?? DATABASE_FIELD.postgresql;
    setF((p) => ({ ...p, engine: e.engine, port: String(e.default_port), default_database: dbf.value, options: {}, licensed_features: [] }));
    setStages([]); setResult(null);
  };

  const validate = (id: string): Record<string, string> => {
    const e: Record<string, string> = {};
    if (id === "engine" && !f.engine) e.engine = "Choose an engine";
    if (id === "connection") {
      if (!/^[A-Za-z0-9._-]{1,128}$/.test(f.name)) e.name = "Use letters, digits, dot, dash or underscore";
      if (!f.host.trim()) e.host = "Enter a host name or IP address";
      const port = Number(f.port);
      if (!Number.isInteger(port) || port < 1 || port > 65535) e.port = "Port must be 1–65535";
    }
    if (id === "tls" && (f.tls_mode === "verify-ca") && !f.tls_ca_pem.trim()) e.tls_ca_pem = "verify-ca needs the CA certificate";
    if (id === "tls" && f.tls_ca_pem.trim() && !f.tls_ca_pem.includes("BEGIN CERTIFICATE")) e.tls_ca_pem = "Paste a PEM certificate";
    if (id === "credentials" && f.monitoring_enabled && !f.mon_user.trim()) e.mon_user = "A monitoring account is needed to collect metrics";
    if (id === "credentials" && f.mon_user.trim()) { const r = secretRefError(f.mon_provider, f.mon_ref); if (r) e.mon_ref = r; }
    if (id === "management" && f.management_mode === "administration" && !f.admin_user.trim()) e.admin_user = "Administration needs a separate administration account";
    return e;
  };

  const ids = ONBOARDING_STEPS.map((s) => s.id);
  const go = (id: string) => setStep(id);
  const next = () => {
    const e = validate(step);
    setErrors(e);
    if (Object.values(e).some(Boolean)) return;
    setDone((d) => (d.includes(step) ? d : [...d, step]));
    if (step === "review") { create.mutate(); return; }
    const i = ids.indexOf(step);
    setStep(ids[i + 1]);
  };
  const back = () => setStep(ids[Math.max(0, ids.indexOf(step) - 1)]);

  const body = () => ({
    engine: f.engine, host: f.host.trim(), port: Number(f.port), default_database: f.default_database || null, tls_mode: f.tls_mode,
    tls_ca_pem: f.tls_ca_pem || null, username: f.mon_user, connection_options: options,
    ...(f.mon_provider === "local" ? { password: f.mon_password || null } : { provider: f.mon_provider, secret_ref: f.mon_ref.trim() }),
    licensed_features: f.licensed_features,
  });

  const runTest = async () => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setTesting(true); setResult(null);
    setStages(STAGE_ORDER.map((s) => ({ stage: s, status: "pending" as StageStatus })));
    try {
      await postNdjson<{ stage: string; status: string; message?: string; duration_ms?: number; detail?: Record<string, unknown>; result?: TestConnectionResult }>(
        "/servers/test-connection/stream", body(), (ev) => {
          if (ev.stage === "result") { setResult(ev.result ?? null); return; }
          setStages((cur) => {
            const exists = cur.some((s) => s.stage === ev.stage);
            const upd: Stage = { stage: ev.stage, status: ev.status as StageStatus, message: ev.message, duration_ms: ev.duration_ms, detail: ev.detail };
            return exists ? cur.map((s) => (s.stage === ev.stage ? upd : s)) : [...cur, upd];
          });
        }, ctl.signal);
    } catch (e) {
      if ((e as Error).name !== "AbortError") setResult({ ok: false, message: e instanceof ApiError ? e.message : String(e) });
    } finally {
      // anything the server never reported is shown as skipped, not as still running
      setStages((cur) => cur.map((s) => (s.status === "pending" || s.status === "running" ? { ...s, status: "skipped" } : s)));
      setTesting(false);
    }
  };
  const stopTest = () => { abort.current?.abort(); };

  const create = useMutation({
    mutationFn: () => post<Server>("/servers", {
      name: f.name, engine: f.engine, host: f.host.trim(), port: Number(f.port), default_database: f.default_database || null,
      description: f.description || null, environment_id: f.environment_id || null, site_id: f.site_id || null,
      cluster_id: f.cluster_id || null, cluster_role: f.cluster_role || null, application_id: f.application_id || null,
      owner: f.owner || null, deployment: f.deployment, tls_mode: f.tls_mode, tls_ca_pem: f.tls_ca_pem || null,
      monitoring_enabled: f.monitoring_enabled, collection_interval_s: Number(f.collection_interval_s), licensed_features: f.licensed_features,
      management_mode: f.management_mode, connection_options: options, tags: parseTags(f.tags),
      monitoring_credential: f.mon_user ? monitoringCredential(f) : null,
      admin_credential: f.management_mode === "administration" && f.admin_user ? { username: f.admin_user, password: f.admin_password || null } : null,
    }),
    onSuccess: (s) => {
      setCreated(s);
      // never keep secrets around once the server is registered
      setF((p) => ({ ...p, mon_password: "", admin_password: "" }));
      setDone((d) => [...new Set([...d, "review"])]);
      setStep("done");
      for (const k of ["servers", "fleet-sources", "dashboard", "setup-checklist"]) qc.invalidateQueries({ queryKey: [k] });
    },
    onError: (e: ApiError) => {
      const errs: Record<string, string> = {};
      for (const it of (e.details?.errors as { loc: string[]; msg: string }[] | undefined) ?? []) errs[it.loc[it.loc.length - 1]] = it.msg;
      setErrors(errs);
      toast.error(e.message);
    },
  });

  const envName = envs.data?.find((e) => e.id === f.environment_id)?.name;
  const review: [string, React.ReactNode][] = [
    ["Engine", engine ? <DatabaseEngineIcon engine={engine.engine} withLabel /> : "—"],
    ["Name", f.name], ["Address", `${f.host}:${f.port}${f.default_database ? ` / ${f.default_database}` : ""}`],
    ["TLS", f.tls_mode + (f.tls_ca_pem ? " (private CA)" : "")],
    ["Monitoring account", f.mon_user ? <span>{f.mon_user} <span className="text-muted">{f.mon_provider !== "local" ? `(${PROVIDER_LABEL[f.mon_provider]}: ${f.mon_ref.trim()})` : f.mon_password ? "(password set, encrypted on save)" : "(no password)"}</span></span> : "none"],
    ["Management mode", f.management_mode === "monitoring" ? "Monitoring Only" : <span className="text-warn">Administration Enabled ({f.admin_user})</span>],
    ["Environment", envName ?? "Unassigned"],
    ["Collection", f.monitoring_enabled ? `every ${Number(f.collection_interval_s) >= 60 ? `${Number(f.collection_interval_s) / 60} min` : `${f.collection_interval_s} s`}` : "off"],
    ["Connection test", result ? (result.ok ? <Badge tone="ok">passed</Badge> : <Badge tone="crit">failed</Badge>) : <Badge tone="warn">not run</Badge>],
  ];

  const content = () => {
    switch (step) {
      case "engine":
        return (
          <div className="space-y-3">
            {errors.engine && <p className="text-[12.5px] text-crit" role="alert">{errors.engine}</p>}
            <div role="radiogroup" aria-label="Engine" className="grid gap-2 sm:grid-cols-2">
              {engines.data?.filter((e) => e.engine !== "demo").map((e) => (
                <button key={e.engine} type="button" role="radio" aria-checked={f.engine === e.engine} onClick={() => pickEngine(e)}
                  className={cn("flex items-center justify-between gap-2 rounded-md border px-3 py-2.5 text-left text-[13px] transition-colors duration-150 motion-reduce:transition-none",
                    f.engine === e.engine ? "border-accent bg-accent-soft/50 ring-1 ring-accent" : "border-border hover:bg-surface-2")}>
                  <span className="flex items-center gap-2"><DatabaseEngineIcon engine={e.engine} size="md" decorative /> <span className="font-medium">{e.display_name}</span></span>
                  <span className="flex gap-1">
                    {e.support_level === "partial" && <Badge tone="info">partial</Badge>}
                    {!e.implemented && <Badge>inventory only</Badge>}
                    {e.driver_status === "missing" && <Badge tone="crit">driver missing</Badge>}
                  </span>
                </button>
              ))}
            </div>
            {engine && <EngineBanner engine={engine} />}
          </div>
        );
      case "connection":
        return (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Name" hint="Unique; letters, digits, dot, dash, underscore" error={errors.name}><Input value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="pg-orders-prod-01" /></Field>
            <Field label="Deployment"><Select value={f.deployment} onChange={(e) => set("deployment", e.target.value)}>{DEPLOYMENTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
            <Field label="Host" error={errors.host}><Input value={f.host} onChange={(e) => set("host", e.target.value)} placeholder="db01.internal or 10.0.0.12" /></Field>
            <div className="grid grid-cols-[110px_1fr] gap-3">
              <Field label="Port" error={errors.port}><Input inputMode="numeric" value={f.port} onChange={(e) => set("port", e.target.value)} /></Field>
              <Field label={dbField.label} hint={dbField.hint}><Input value={f.default_database} placeholder={dbField.placeholder} onChange={(e) => set("default_database", e.target.value)} /></Field>
            </div>
            {optionDefs.map((o) => o.type === "boolean" ? (
              <label key={o.key} className="flex items-start gap-2 text-[13px]"><input type="checkbox" className="mt-0.5" checked={f.options[o.key] === true} onChange={(e) => set("options", { ...f.options, [o.key]: e.target.checked })} />
                <span>{o.label}{o.help && <span className="block text-[12px] text-muted">{o.help}</span>}</span></label>
            ) : o.type === "choice" ? (
              <Field key={o.key} label={o.label} hint={o.help}><Select value={String(f.options[o.key] ?? "")} onChange={(e) => set("options", { ...f.options, [o.key]: e.target.value })}><option value="">Default</option>{o.choices?.map((c) => <option key={c} value={c}>{c}</option>)}</Select></Field>
            ) : (
              <Field key={o.key} label={o.label} hint={o.help}><Input inputMode={o.type === "integer" ? "numeric" : undefined} value={String(f.options[o.key] ?? "")} onChange={(e) => set("options", { ...f.options, [o.key]: e.target.value })} /></Field>
            ))}
            {(engine?.licensable_features ?? []).map((lf) => (
              <label key={lf.key} className="flex items-start gap-2 text-[13px] sm:col-span-2">
                <input type="checkbox" className="mt-0.5" checked={f.licensed_features.includes(lf.key)} onChange={(e) => set("licensed_features", e.target.checked ? [...f.licensed_features, lf.key] : f.licensed_features.filter((x) => x !== lf.key))} />
                <span>This server is licensed for {lf.label} <span className="text-muted">(off by default)</span><span className="block text-[12px] text-muted">Covers {lf.covers}. {lf.used_by_db_manager}.</span></span>
              </label>
            ))}
            <Field label="Description" className="sm:col-span-2"><Input value={f.description} onChange={(e) => set("description", e.target.value)} /></Field>
          </div>
        );
      case "tls":
        return (
          <div className="space-y-3">
            <div role="radiogroup" aria-label="TLS mode" className="grid gap-2">
              {TLS_MODES.map(([v, l, h]) => (
                <ChoiceCard key={v} name="tls_mode" selected={f.tls_mode === v} onSelect={() => set("tls_mode", v)} title={l}
                  badge={v === "verify-full" ? <Badge tone="ok">recommended</Badge> : v === "disable" ? <Badge tone="crit">insecure</Badge> : undefined}>
                  <code className="font-mono text-2xs">{v}</code> — {h}
                </ChoiceCard>
              ))}
            </div>
            <Field label="CA certificate (PEM)" hint="Optional. Needed when the server certificate isn't signed by a public CA." error={errors.tls_ca_pem}>
              <Textarea rows={4} value={f.tls_ca_pem} onChange={(e) => set("tls_ca_pem", e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" />
            </Field>
          </div>
        );
      case "credentials":
        return (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Monitoring user" hint={MON_HINT[f.engine] ?? "Read-only monitoring account"} error={errors.mon_user}><Input autoComplete="off" value={f.mon_user} onChange={(e) => set("mon_user", e.target.value)} /></Field>
            <SecretSourceFields provider={f.mon_provider} onProvider={(v) => set("mon_provider", v)} secretRef={f.mon_ref} onSecretRef={(v) => set("mon_ref", v)}
              password={f.mon_password} onPassword={(v) => set("mon_password", v)} passwordLabel="Monitoring password" passwordHint="Encrypted before it is stored; never displayed again." />
            {errors.mon_ref && <p role="alert" className="text-[12px] text-crit sm:col-span-2">{errors.mon_ref}</p>}
            <div className="flex items-center gap-3 text-[13px]"><Switch checked={f.monitoring_enabled} onCheckedChange={(v) => set("monitoring_enabled", v)} label="Collect metrics" /> Collect metrics in the background</div>
            <Field label="Collection interval">
              <Select value={f.collection_interval_s} onChange={(e) => set("collection_interval_s", e.target.value)}>
                {[["15", "15 seconds"], ["30", "30 seconds"], ["60", "1 minute"], ["300", "5 minutes"], ["900", "15 minutes"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
            </Field>
            <p className="flex items-start gap-2 text-[12.5px] text-muted sm:col-span-2"><Lock className="mt-0.5 size-4 shrink-0" aria-hidden /> The permissions this account needs are in the database permission guide for {engine?.display_name ?? "the engine"}. Grant only those.</p>
          </div>
        );
      case "management":
        return (
          <div className="space-y-3">
            <div role="radiogroup" aria-label="Management mode" className="grid gap-2">
              <ChoiceCard name="mode" selected={f.management_mode === "monitoring"} onSelect={() => set("management_mode", "monitoring")} icon={<Eye />} title="Monitoring Only" badge={<Badge tone="ok">default</Badge>}>
                DBManager reads metrics, sessions, queries and configuration. It never offers changes on this server, whatever the credentials would allow. Recommended for most servers.
              </ChoiceCard>
              <ChoiceCard name="mode" selected={f.management_mode === "administration"} onSelect={() => set("management_mode", "administration")} icon={<Wrench />} title="Administration Enabled">
                Guarded changes (create users, grant privileges, create indexes, cancel sessions) become available. Every change is previewed, needs confirmation and permission, and is audited. Uses a separate administration account.
              </ChoiceCard>
            </div>
            {f.management_mode === "administration" && (
              <div className="grid gap-3 rounded-md border border-warn/40 bg-warn-soft/40 p-3 sm:grid-cols-2">
                <p className="flex items-start gap-2 text-[12.5px] text-warn sm:col-span-2"><ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden /> Use a dedicated account with only the privileges you want DBManager to exercise. Destructive actions are never run automatically.</p>
                <Field label="Administration user" error={errors.admin_user}><Input autoComplete="off" value={f.admin_user} onChange={(e) => set("admin_user", e.target.value)} /></Field>
                <Field label="Administration password" hint="Encrypted; never displayed again."><Input type="password" autoComplete="new-password" value={f.admin_password} onChange={(e) => set("admin_password", e.target.value)} /></Field>
              </div>
            )}
          </div>
        );
      case "placement":
        return (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Environment"><Select value={f.environment_id} onChange={(e) => set("environment_id", e.target.value)}><option value="">Unassigned</option>{envs.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
            <Field label="Site"><Select value={f.site_id} onChange={(e) => set("site_id", e.target.value)}><option value="">None</option>{sites.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
            <Field label="Cluster"><Select value={f.cluster_id} onChange={(e) => set("cluster_id", e.target.value)}><option value="">None</option>{clusters.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
            <Field label="Role in cluster"><Select value={f.cluster_role} onChange={(e) => set("cluster_role", e.target.value)}><option value="">None</option>{["primary", "replica", "standby", "node"].map((r) => <option key={r} value={r}>{r}</option>)}</Select></Field>
            <Field label="Application"><Select value={f.application_id} onChange={(e) => set("application_id", e.target.value)}><option value="">None</option>{apps.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
            <Field label="Owner"><Input value={f.owner} onChange={(e) => set("owner", e.target.value)} placeholder="team or person" /></Field>
            <Field label="Tags" hint="key=value pairs separated by commas" className="sm:col-span-2"><Input value={f.tags} onChange={(e) => set("tags", e.target.value)} placeholder="team=payments, pci=true" /></Field>
          </div>
        );
      case "test":
        return (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              {testing ? <Button onClick={stopTest}><Square /> Stop</Button> : <Button variant="primary" disabled={f.mon_provider !== "local" && !f.mon_ref.trim()} onClick={runTest}><Play /> {stages.length ? "Run again" : "Run test"}</Button>}
              <span className="text-[12.5px] text-muted">As {f.mon_user || "no user"} → {f.host}:{f.port}</span>
            </div>
            {stages.length > 0 && <StageList stages={stages} labels={STAGE_LABELS} />}
            {result && (
              <div role="status" className={cn("rounded-md border px-3 py-2 text-[13px]", result.ok ? "border-ok/30 bg-ok-soft text-ok" : "border-crit/30 bg-crit-soft text-crit")}>
                {result.ok ? <>Connected{result.version ? `, version ${result.version}` : ""}{result.latency_ms ? ` in ${Math.round(result.latency_ms)} ms` : ""}.</>
                  : <>{result.error_code && <span className="font-medium">{ERROR_LABEL[result.error_code] ?? result.error_code}: </span>}{result.message}</>}
              </div>
            )}
            {result?.ok && result.capabilities && <CapabilityGroups caps={result.capabilities} />}
            {!stages.length && f.mon_provider === "local" && <p className="text-[12.5px] text-muted">The test is optional, but it shows exactly what DBManager can see with this account before you register the server.</p>}
          </div>
        );
      case "review":
        return (
          <dl className="divide-y divide-border rounded-md border border-border text-[13px]">
            {review.map(([k, v]) => <div key={k} className="grid grid-cols-[170px_1fr] gap-3 px-3 py-2"><dt className="text-muted">{k}</dt><dd className="min-w-0 break-words">{v}</dd></div>)}
          </dl>
        );
      case "done":
        return (
          <SuccessPanel title={`${created?.name ?? f.name} registered`}
            actions={<>
              <Button variant="primary" onClick={() => { onOpenChange(false); if (created) navigate(`/servers/${created.id}`); }}>Open server</Button>
              <Button onClick={reset}>Add another</Button>
            </>}>
            {created?.implementation === "planned" ? "This engine has no adapter yet; the server is kept for inventory." : "Discovery and the first collection are queued. Credentials were encrypted and are not shown again."}
          </SuccessPanel>
        );
      default:
        return null;
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange} width="max-w-4xl" title="Add a database server" description="A guided setup: connection, security, credentials, placement and a live test.">
      <Wizard title="Onboarding" steps={ONBOARDING_STEPS} current={step} completed={done} onStepChange={step === "done" ? undefined : go}
        onBack={step === "done" ? undefined : back} onNext={step === "done" ? undefined : next} busy={create.isPending}
        hideNav={step === "done"}
        nextLabel={step === "review" ? "Register server" : step === "test" && !result ? "Skip test" : "Continue"}>
        {content()}
      </Wizard>
    </Dialog>
  );
}
