import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plug, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, post } from "@/api/client";
import { useApplications, useClusters, useEngines, useEnvironments, useSites } from "@/api/hooks";
import type { ConnectionOption, Server, TestConnectionResult } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { SecretSourceFields, secretRefError, type SecretProvider } from "@/features/shared/secret-source";
import { DATABASE_FIELD, ERROR_LABEL, SUPPORT_LABEL } from "@/lib/capabilities";
import { DatabaseEngineIcon } from "@/components/engine-icon";

interface Form {
  name: string; engine: string; host: string; port: string; default_database: string; description: string;
  environment_id: string; site_id: string; cluster_id: string; cluster_role: string; application_id: string; owner: string;
  deployment: string; tls_mode: string; tls_ca_pem: string; mon_user: string; mon_password: string; mon_provider: SecretProvider; mon_ref: string; admin_user: string;
  admin_password: string; monitoring_enabled: boolean; collection_interval_s: string; tags: string; licensed_features: string[];
  management_mode: "monitoring" | "administration"; options: Record<string, string | boolean>;
}

const EMPTY: Form = {
  name: "", engine: "postgresql", host: "", port: "5432", default_database: "postgres", description: "", environment_id: "",
  site_id: "", cluster_id: "", cluster_role: "", application_id: "", owner: "", deployment: "self-managed",
  tls_mode: "verify-full", tls_ca_pem: "", mon_user: "", mon_password: "", mon_provider: "local", mon_ref: "", admin_user: "", admin_password: "",
  monitoring_enabled: true, collection_interval_s: "60", tags: "", licensed_features: [], management_mode: "monitoring", options: {},
};

export const MON_HINT: Record<string, string> = {
  postgresql: "Read-only account, e.g. a role with pg_monitor",
  mysql: "Account with PROCESS and SELECT on performance_schema",
  mariadb: "Account with PROCESS and SELECT on performance_schema",
  mssql: "SQL login with VIEW SERVER STATE and VIEW ANY DEFINITION",
  oracle: "User with CREATE SESSION and SELECT_CATALOG_ROLE",
  db2: "Authorization ID with CONNECT and EXECUTE on MON_GET_* (or SQLADM)",
  db2zos: "Authorization ID with SELECT on the SYSIBM catalog",
};

/** Connection options are sent as typed JSON; empty values are left out. */
export function optionPayload(defs: ConnectionOption[] | undefined, values: Record<string, string | boolean>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const d of defs ?? []) {
    const v = values[d.key];
    if (v === undefined || v === "") continue;
    out[d.key] = d.type === "integer" ? Number(v) : v;
  }
  return out;
}

export const DEPLOYMENTS = [
  ["self-managed", "Self-managed"], ["aws-rds", "Amazon RDS"], ["aws-aurora", "Amazon Aurora"], ["azure-flexible", "Azure Database (Flexible Server)"],
  ["azure-sql", "Azure SQL Database"], ["gcp-cloudsql", "Google Cloud SQL"], ["oci-autonomous", "Oracle Autonomous Database"], ["ibm-db2-cloud", "IBM Db2 on Cloud"],
];

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className="border-t border-border pt-4 first:border-0 first:pt-0">
      <legend className="mb-3 text-[13px] font-semibold">{title}</legend>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

export function RegisterServerDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [f, setF] = useState<Form>(EMPTY);
  const [test, setTest] = useState<TestConnectionResult | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const engines = useEngines();
  const envs = useEnvironments();
  const sites = useSites();
  const clusters = useClusters();
  const apps = useApplications();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((p) => ({ ...p, [k]: v }));
  const engine = engines.data?.find((e) => e.engine === f.engine);
  const dbField = DATABASE_FIELD[f.engine] ?? DATABASE_FIELD.postgresql;
  // the Oracle service name is the "database" field; the option list only adds the SID alternative
  const optionDefs = (engine?.connection_options ?? []).filter((o) => !(f.engine === "oracle" && o.key === "service_name"));
  const options = optionPayload(optionDefs, f.options);

  useEffect(() => {
    if (open) {
      setF({ ...EMPTY, environment_id: envs.data?.find((e) => e.tier === "development")?.id ?? "" });
      setTest(null);
      setErrors({});
    }
  }, [open, envs.data]);

  const testConn = useMutation({
    mutationFn: () => post<TestConnectionResult>("/servers/test-connection", {
      engine: f.engine, host: f.host, port: Number(f.port), default_database: f.default_database || null, tls_mode: f.tls_mode,
      tls_ca_pem: f.tls_ca_pem || null, username: f.mon_user,
      ...(f.mon_provider === "local" ? { password: f.mon_password || null } : { provider: f.mon_provider, secret_ref: f.mon_ref.trim() }),
      connection_options: options, licensed_features: f.licensed_features,
    }),
    onSuccess: setTest,
    onError: (e: ApiError) => setTest({ ok: false, message: e.message }),
  });

  const create = useMutation({
    mutationFn: () => post<Server>("/servers", {
      name: f.name, engine: f.engine, host: f.host, port: Number(f.port), default_database: f.default_database || null,
      description: f.description || null, environment_id: f.environment_id || null, site_id: f.site_id || null,
      cluster_id: f.cluster_id || null, cluster_role: f.cluster_role || null, application_id: f.application_id || null,
      owner: f.owner || null, deployment: f.deployment, tls_mode: f.tls_mode, tls_ca_pem: f.tls_ca_pem || null,
      monitoring_enabled: f.monitoring_enabled, collection_interval_s: Number(f.collection_interval_s), licensed_features: f.licensed_features,
      management_mode: f.management_mode, connection_options: options,
      tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean).map((t) => { const [key, ...v] = t.split("="); return { key: key.trim(), value: v.join("=").trim() }; }),
      monitoring_credential: !f.mon_user ? null : f.mon_provider === "local"
        ? { username: f.mon_user, password: f.mon_password || null }
        : { username: f.mon_user, password: null, provider: f.mon_provider, secret_ref: f.mon_ref.trim() },
      admin_credential: f.admin_user ? { username: f.admin_user, password: f.admin_password || null } : null,
    }),
    onSuccess: (s) => {
      toast.success(`${s.name} registered`, { description: s.implementation === "planned" ? "This engine has no adapter yet; the server is kept for inventory." : "Discovery and the first collection are queued." });
      qc.invalidateQueries({ queryKey: ["servers"] });
      qc.invalidateQueries({ queryKey: ["fleet-sources"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      onOpenChange(false);
      navigate(`/servers/${s.id}`);
    },
    onError: (e: ApiError) => {
      const errs: Record<string, string> = {};
      for (const it of (e.details?.errors as { loc: string[]; msg: string }[] | undefined) ?? []) errs[it.loc[it.loc.length - 1]] = it.msg;
      setErrors(errs);
      toast.error(e.message);
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange} width="max-w-3xl" title="Register a server"
      description="Credentials are encrypted before they're stored and are never shown again."
      footer={
        <>
          <div className="mr-auto flex items-center gap-2 text-[12.5px]">
            {test && (test.ok ? <span className="flex items-center gap-1.5 text-ok"><CheckCircle2 className="size-4" /> Connected{test.version ? `, version ${test.version}` : ""}{test.latency_ms ? ` in ${Math.round(test.latency_ms)} ms` : ""}</span>
              : <span className="flex items-center gap-1.5 text-crit"><XCircle className="size-4 shrink-0" /> <span>{test.error_code && <span className="font-medium">{ERROR_LABEL[test.error_code] ?? test.error_code}: </span>}{test.message}</span></span>)}
          </div>
          <Button onClick={() => testConn.mutate()} disabled={!f.host || !f.mon_user || (f.mon_provider !== "local" && !f.mon_ref.trim()) || testConn.isPending}><Plug /> {testConn.isPending ? "Testing…" : "Test connection"}</Button>
          <Button variant="primary" onClick={() => create.mutate()} disabled={!f.name || !f.host || (!!f.mon_user && !!secretRefError(f.mon_provider, f.mon_ref)) || create.isPending}>Register server</Button>
        </>
      }>
      <div className="space-y-5">
        <Section title="Server">
          <Field label="Name" hint="Unique; letters, digits, dot, dash, underscore" error={errors.name}>
            <Input value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="pg-orders-prod-01" />
          </Field>
          <div className="flex items-end gap-2">
          <span className="mb-1"><DatabaseEngineIcon engine={f.engine} size="md" /></span>
          <Field label="Engine" className="min-w-0 flex-1">
            <Select value={f.engine} onChange={(e) => {
              const en = engines.data?.find((x) => x.engine === e.target.value);
              const dbf = DATABASE_FIELD[e.target.value] ?? DATABASE_FIELD.postgresql;
              setF((p) => ({ ...p, engine: e.target.value, port: String(en?.default_port ?? p.port), default_database: dbf.value, options: {}, licensed_features: [] }));
              setTest(null);
            }}>
              {engines.data?.map((e) => <option key={e.engine} value={e.engine}>{e.display_name}{e.support_level === "partial" ? " (partial support)" : e.implemented ? "" : " (no adapter yet)"}</option>)}
            </Select>
          </Field>
          </div>
          {engine && (engine.support_level !== "supported" || engine.driver_status !== "installed") && (
            <p className="sm:col-span-2 rounded-md border border-info/30 bg-info-soft px-3 py-2 text-[12.5px] text-info">
              {engine.implemented ? <><Badge tone={SUPPORT_LABEL[engine.support_level ?? "supported"]?.tone ?? "info"}>{SUPPORT_LABEL[engine.support_level ?? "supported"]?.label}</Badge> {engine.support_note}</> : `No ${engine.display_name} adapter is available; the server is kept for inventory only.`}
              {engine.driver_status === "missing" && <span role="alert" className="mt-1 block font-medium text-crit">{engine.driver_detail?.remediation ?? `The ${engine.driver} driver is missing in this backend.`} Test connection will fail until it is installed (Platform → Database drivers).</span>}
              {engine.driver_status === "degraded" && <span className="mt-1 block">Driver: {engine.driver_detail?.error ?? "degraded"} (Platform → Database drivers).</span>}
            </p>
          )}
          <Field label="Host" error={errors.host}><Input value={f.host} onChange={(e) => set("host", e.target.value)} placeholder="db01.internal or 10.0.0.12" /></Field>
          <div className="grid grid-cols-[110px_1fr] gap-3">
            <Field label="Port" error={errors.port}><Input inputMode="numeric" value={f.port} onChange={(e) => set("port", e.target.value)} /></Field>
            <Field label={dbField.label} hint={dbField.hint}><Input value={f.default_database} placeholder={dbField.placeholder} onChange={(e) => set("default_database", e.target.value)} /></Field>
          </div>
          <Field label="Description" className="sm:col-span-2"><Input value={f.description} onChange={(e) => set("description", e.target.value)} /></Field>
        </Section>

        <Section title="Placement and ownership">
          <Field label="Environment">
            <Select value={f.environment_id} onChange={(e) => set("environment_id", e.target.value)}>
              <option value="">Unassigned</option>{envs.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </Select>
          </Field>
          <Field label="Site">
            <Select value={f.site_id} onChange={(e) => set("site_id", e.target.value)}>
              <option value="">None</option>{sites.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </Select>
          </Field>
          <Field label="Cluster">
            <Select value={f.cluster_id} onChange={(e) => set("cluster_id", e.target.value)}>
              <option value="">None</option>{clusters.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </Select>
          </Field>
          <Field label="Role in cluster">
            <Select value={f.cluster_role} onChange={(e) => set("cluster_role", e.target.value)}>
              <option value="">None</option>{["primary", "replica", "standby", "node"].map((r) => <option key={r} value={r}>{r}</option>)}
            </Select>
          </Field>
          <Field label="Application">
            <Select value={f.application_id} onChange={(e) => set("application_id", e.target.value)}>
              <option value="">None</option>{apps.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
            </Select>
          </Field>
          <Field label="Owner"><Input value={f.owner} onChange={(e) => set("owner", e.target.value)} placeholder="team or person" /></Field>
          <Field label="Deployment">
            <Select value={f.deployment} onChange={(e) => set("deployment", e.target.value)}>{DEPLOYMENTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          </Field>
          <Field label="Tags" hint="Comma separated key=value pairs"><Input value={f.tags} onChange={(e) => set("tags", e.target.value)} placeholder="team=payments, pci=true" /></Field>
        </Section>

        <Section title="Connection security">
          <Field label="TLS mode" hint="verify-full checks the certificate and host name">
            <Select value={f.tls_mode} onChange={(e) => set("tls_mode", e.target.value)}>
              {["verify-full", "verify-ca", "require", "prefer", "disable"].map((m) => <option key={m} value={m}>{m}</option>)}
            </Select>
          </Field>
          <div />
          <Field label="CA certificate (PEM)" className="sm:col-span-2" hint="Optional. Needed when the server certificate isn't signed by a public CA.">
            <Textarea rows={3} value={f.tls_ca_pem} onChange={(e) => set("tls_ca_pem", e.target.value)} placeholder="-----BEGIN CERTIFICATE-----" />
          </Field>
        </Section>

        {(optionDefs.length > 0 || (engine?.licensable_features ?? []).length > 0) && (
          <Section title={`${engine?.display_name ?? "Engine"} settings`}>
            {optionDefs.map((o) => (
              o.type === "boolean" ? (
                <label key={o.key} className="flex items-start gap-2 text-[13px]">
                  <input type="checkbox" className="mt-0.5" checked={f.options[o.key] === true}
                    onChange={(e) => set("options", { ...f.options, [o.key]: e.target.checked })} />
                  <span>{o.label}{o.help && <span className="block text-[12px] text-muted">{o.help}</span>}</span>
                </label>
              ) : o.type === "choice" ? (
                <Field key={o.key} label={o.label} hint={o.help}>
                  <Select value={String(f.options[o.key] ?? "")} onChange={(e) => set("options", { ...f.options, [o.key]: e.target.value })}>
                    <option value="">Default</option>{o.choices?.map((c) => <option key={c} value={c}>{c}</option>)}
                  </Select>
                </Field>
              ) : (
                <Field key={o.key} label={o.label} hint={o.help}>
                  <Input inputMode={o.type === "integer" ? "numeric" : undefined} value={String(f.options[o.key] ?? "")}
                    onChange={(e) => set("options", { ...f.options, [o.key]: e.target.value })} />
                </Field>
              )
            ))}
            {(engine?.licensable_features ?? []).map((lf) => (
              <label key={lf.key} className="flex items-start gap-2 text-[13px] sm:col-span-2">
                <input type="checkbox" className="mt-0.5" checked={f.licensed_features.includes(lf.key)}
                  onChange={(e) => set("licensed_features", e.target.checked ? [...f.licensed_features, lf.key] : f.licensed_features.filter((x) => x !== lf.key))} />
                <span>
                  This server is licensed for {lf.label} <span className="text-muted">(off by default)</span>
                  <span className="block text-[12px] text-muted">Covers {lf.covers}. {lf.used_by_db_manager}.</span>
                </span>
              </label>
            ))}
          </Section>
        )}

        <Section title="Credentials">
          <Field label="Monitoring user" hint={MON_HINT[f.engine] ?? "Read-only monitoring account"}><Input autoComplete="off" value={f.mon_user} onChange={(e) => set("mon_user", e.target.value)} /></Field>
          <SecretSourceFields provider={f.mon_provider} onProvider={(v) => set("mon_provider", v)} secretRef={f.mon_ref} onSecretRef={(v) => set("mon_ref", v)}
            password={f.mon_password} onPassword={(v) => set("mon_password", v)} passwordLabel="Monitoring password" />
          <Field label="Administration user" hint="Optional. Only used for confirmed changes."><Input autoComplete="off" value={f.admin_user} onChange={(e) => set("admin_user", e.target.value)} /></Field>
          <Field label="Administration password"><Input type="password" autoComplete="new-password" value={f.admin_password} onChange={(e) => set("admin_password", e.target.value)} /></Field>
        </Section>

        <Section title="Management">
          <Field label="Mode" hint={f.management_mode === "monitoring" ? "DBManager never offers changes on this server, whatever the credentials allow." : "Guarded changes (preview, confirmation, audit) use the administration credential."}>
            <Select value={f.management_mode} onChange={(e) => set("management_mode", e.target.value as Form["management_mode"])}>
              <option value="monitoring">Monitoring only</option>
              <option value="administration">Administration enabled</option>
            </Select>
          </Field>
        </Section>

        <Section title="Monitoring">
          <div className="flex items-center gap-3 text-[13px]"><Switch checked={f.monitoring_enabled} onCheckedChange={(v) => set("monitoring_enabled", v)} label="Collect metrics" /> Collect metrics in the background</div>
          <Field label="Collection interval">
            <Select value={f.collection_interval_s} onChange={(e) => set("collection_interval_s", e.target.value)}>
              {[["15", "15 seconds"], ["30", "30 seconds"], ["60", "1 minute"], ["300", "5 minutes"], ["900", "15 minutes"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </Select>
          </Field>
        </Section>
        {Object.keys(errors).length > 0 && <Badge tone="crit">Check the highlighted fields</Badge>}
      </div>
    </Dialog>
  );
}
