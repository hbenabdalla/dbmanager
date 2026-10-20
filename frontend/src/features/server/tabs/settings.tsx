import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, KeyRound, PlugZap, ShieldCheck, Trash2 } from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, del, download, patch, post, put } from "@/api/client";
import { serverCan, useApplications, useClusters, useEngines, useEnvironments, useSites } from "@/api/hooks";
import type { CredentialInfo, ServerDetail } from "@/api/types";
import { ConfirmDialog } from "@/components/confirm";
import { StageList, type Stage, type StageStatus } from "@/components/wizard";
import { Dialog } from "@/components/ui/dialog";
import { STAGE_LABELS } from "@/features/onboarding/onboarding-wizard";
import { SecretSourceFields, secretRefError, type SecretProvider } from "@/features/shared/secret-source";
import { postNdjson } from "@/features/onboarding/stream";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { relTime } from "@/lib/format";

function CredentialEditor({ server, purpose, info }: { server: ServerDetail; purpose: "monitoring" | "admin" | "readonly"; info: CredentialInfo | null }) {
  const [username, setUsername] = useState(info?.username ?? "");
  const [password, setPassword] = useState("");
  const [provider, setProvider] = useState<SecretProvider>((info?.provider as SecretProvider | undefined) ?? "local");
  const [ref, setRef] = useState(info?.secret_ref ?? "");
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: () => put(`/servers/${server.id}/credentials/${purpose}`, { username, password: password || null, provider, secret_ref: provider === "local" ? null : ref.trim() }),
    onSuccess: () => { toast.success("Credential saved"); setPassword(""); qc.invalidateQueries({ queryKey: ["server", server.id] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const remove = useMutation({
    mutationFn: () => del(`/servers/${server.id}/credentials/${purpose}`),
    onSuccess: () => { toast.success("Credential removed"); qc.invalidateQueries({ queryKey: ["server", server.id] }); },
  });
  return (
    <div className="space-y-3 rounded-md border border-border p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 font-medium"><KeyRound className="size-4 text-muted" />{purpose === "monitoring" ? "Monitoring credential" : purpose === "readonly" ? "Read-only credential (optional, SQL workspace)" : "Administration credential"}</div>
        <span className="text-[12px] text-muted">{info ? `${info.has_secret ? "Secret stored" : "No secret"}, rotated ${relTime(info.rotated_at)}${info.updated_by ? ` by ${info.updated_by}` : ""}` : "Not configured"}</span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Username"><Input autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} /></Field>
        <SecretSourceFields provider={provider} onProvider={setProvider} secretRef={ref} onSecretRef={setRef} password={password} onPassword={setPassword}
          passwordHint={info?.has_secret ? "Leave empty to keep the current password" : undefined} />
      </div>
      <div className="flex justify-end gap-2">
        {info && <Button size="sm" variant="danger-outline" onClick={() => remove.mutate()}>Remove</Button>}
        <Button size="sm" variant="primary" disabled={!username || !!secretRefError(provider, ref) || save.isPending} onClick={() => save.mutate()}>Save credential</Button>
      </div>
    </div>
  );
}

type StreamEvent = { stage: string; status: string; message?: string; duration_ms?: number; detail?: Record<string, unknown>; result?: { ok: boolean; message?: string } };

/** Staged connection test of the registered server, using its stored settings and credential. */
function ConnectionTestDialog({ server, onClose }: { server: ServerDetail; onClose: () => void }) {
  const [stages, setStages] = useState<Stage[]>([]);
  const [result, setResult] = useState<{ ok: boolean; message?: string } | null>(null);
  const [running, setRunning] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const run = async () => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setRunning(true);
    setResult(null);
    setStages(Object.keys(STAGE_LABELS).map((s) => ({ stage: s, status: "pending" as StageStatus })));
    try {
      await postNdjson<StreamEvent>(`/servers/${server.id}/test-connection/stream`, {}, (ev) => {
        if (ev.stage === "result") { setResult(ev.result ?? null); return; }
        setStages((cur) => {
          const upd: Stage = { stage: ev.stage, status: ev.status as StageStatus, message: ev.message, duration_ms: ev.duration_ms, detail: ev.detail };
          return cur.some((s) => s.stage === ev.stage) ? cur.map((s) => (s.stage === ev.stage ? upd : s)) : [...cur, upd];
        });
      }, ctl.signal);
    } catch (e) {
      if ((e as Error).name !== "AbortError") setResult({ ok: false, message: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setStages((cur) => cur.map((s) => (s.status === "pending" || s.status === "running" ? { ...s, status: "skipped" } : s)));
      setRunning(false);
    }
  };
  return (
    <Dialog open onOpenChange={(o) => { if (!o) { abort.current?.abort(); onClose(); } }} title={`Test connection to ${server.name}`}
      description="DNS, TCP, TLS, authentication, version, privileges and capabilities with the stored settings and monitoring credential."
      footer={<><Button variant="ghost" onClick={() => { abort.current?.abort(); onClose(); }}>Close</Button><Button variant="primary" disabled={running} onClick={run}>{running ? "Testing…" : stages.length ? "Run again" : "Start test"}</Button></>}>
      {stages.length > 0 ? <StageList stages={stages} labels={STAGE_LABELS} /> : <p className="text-[13px] text-text-2">Nothing is changed on the server.</p>}
      {result && (
        <p role="status" className={`mt-3 rounded-md border px-3 py-2 text-[13px] ${result.ok ? "border-ok/30 bg-ok-soft text-ok" : "border-crit/30 bg-crit-soft text-crit"}`}>
          {result.ok ? "Connection succeeded." : "Connection failed."}{result.message ? ` ${result.message}` : ""}
        </p>
      )}
    </Dialog>
  );
}

export function SettingsTab({ server }: { server: ServerDetail }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const envs = useEnvironments();
  const sites = useSites();
  const clusters = useClusters();
  const apps = useApplications();
  const [f, setF] = useState({
    description: server.description ?? "", host: server.host, port: String(server.port), default_database: server.default_database ?? "",
    environment_id: server.environment?.id ?? "", site_id: server.site?.id ?? "", cluster_id: server.cluster?.id ?? "", cluster_role: server.cluster_role ?? "",
    application_id: server.application?.id ?? "", owner: server.owner ?? "", tls_mode: server.tls_mode, monitoring_enabled: server.monitoring_enabled,
    collection_interval_s: String(server.collection_interval_s), management_mode: server.management_mode ?? "monitoring",
  });
  const engines = useEngines();
  const optionDefs = engines.data?.find((e) => e.engine === server.engine)?.connection_options ?? [];
  const [opts, setOpts] = useState<Record<string, string | boolean>>(
    Object.fromEntries(Object.entries(server.connection_options ?? {}).map(([k, v]) => [k, typeof v === "boolean" ? v : String(v)])),
  );
  const [licensed, setLicensed] = useState<string[]>(server.licensed_features ?? []);
  const [deleting, setDeleting] = useState(false);
  const set = (k: keyof typeof f, v: string | boolean) => setF((p) => ({ ...p, [k]: v }));
  const save = useMutation({
    mutationFn: () => patch(`/servers/${server.id}`, {
      ...f, port: Number(f.port), collection_interval_s: Number(f.collection_interval_s), description: f.description || null,
      default_database: f.default_database || null, environment_id: f.environment_id || null, site_id: f.site_id || null, cluster_id: f.cluster_id || null,
      cluster_role: f.cluster_role || null, application_id: f.application_id || null, owner: f.owner || null,
      connection_options: Object.fromEntries(Object.entries(opts).filter(([, v]) => v !== "" && v !== undefined)
        .map(([k, v]) => [k, optionDefs.find((d) => d.key === k)?.type === "integer" ? Number(v) : v])),
      ...(server.engine === "oracle" ? { licensed_features: licensed } : {}),
    }),
    onSuccess: () => { toast.success("Server updated"); qc.invalidateQueries({ queryKey: ["server", server.id] }); qc.invalidateQueries({ queryKey: ["servers"] }); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const remove = useMutation({
    mutationFn: (confirm: string) => del(`/servers/${server.id}`, { confirm }),
    onSuccess: () => { toast.success(`${server.name} removed`); qc.invalidateQueries({ queryKey: ["servers"] }); qc.invalidateQueries({ queryKey: ["fleet-sources"] }); navigate("/fleet/servers"); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const collect = useMutation({ mutationFn: () => post(`/servers/${server.id}/collect`), onSuccess: () => toast.success("Collection queued") });
  const scan = useMutation({
    mutationFn: () => post<{ message: string }>(`/servers/${server.id}/security-scan`),
    onSuccess: (r) => toast.success(r.message),
    onError: (e: ApiError) => toast.error(e.message),
  });
  const [testing, setTesting] = useState(false);
  const diagnostics = useMutation({
    mutationFn: () => download(`/servers/${server.id}/diagnostics`, `dbm-diagnostics-${server.name}.json`),
    onSuccess: () => toast.success("Diagnostic bundle downloaded"),
    onError: (e: ApiError) => toast.error(e.message),
  });
  const canModify = serverCan(server, "server.modify");
  return (
    <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
      <Card>
        <CardHeader title="Connection and placement" actions={canModify && <Button variant="primary" size="sm" disabled={save.isPending} onClick={() => save.mutate()}>Save changes</Button>} />
        <CardBody>
          <fieldset disabled={!canModify} className="grid gap-3 sm:grid-cols-2">
            <Field label="Host"><Input value={f.host} onChange={(e) => set("host", e.target.value)} /></Field>
            <div className="grid grid-cols-[110px_1fr] gap-3">
              <Field label="Port"><Input value={f.port} onChange={(e) => set("port", e.target.value)} /></Field>
              <Field label="Default database"><Input value={f.default_database} onChange={(e) => set("default_database", e.target.value)} /></Field>
            </div>
            <Field label="TLS mode"><Select value={f.tls_mode} onChange={(e) => set("tls_mode", e.target.value)}>{["verify-full", "verify-ca", "require", "prefer", "disable"].map((m) => <option key={m}>{m}</option>)}</Select></Field>
            <Field label="Environment"><Select value={f.environment_id} onChange={(e) => set("environment_id", e.target.value)}><option value="">Unassigned</option>{envs.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
            <Field label="Site"><Select value={f.site_id} onChange={(e) => set("site_id", e.target.value)}><option value="">None</option>{sites.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
            <Field label="Cluster"><Select value={f.cluster_id} onChange={(e) => set("cluster_id", e.target.value)}><option value="">None</option>{clusters.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
            <Field label="Role in cluster"><Select value={f.cluster_role} onChange={(e) => set("cluster_role", e.target.value)}><option value="">None</option>{["primary", "replica", "standby", "node"].map((r) => <option key={r}>{r}</option>)}</Select></Field>
            <Field label="Application"><Select value={f.application_id} onChange={(e) => set("application_id", e.target.value)}><option value="">None</option>{apps.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select></Field>
            <Field label="Owner"><Input value={f.owner} onChange={(e) => set("owner", e.target.value)} /></Field>
            <Field label="Description"><Input value={f.description} onChange={(e) => set("description", e.target.value)} /></Field>
            <div className="flex items-center gap-3 text-[13px]"><Switch checked={f.monitoring_enabled} onCheckedChange={(v) => set("monitoring_enabled", v)} label="Monitoring" /> Collect metrics in the background</div>
            <Field label="Management mode" hint="Monitoring only: no change actions are offered, whatever the credentials allow">
              <Select value={f.management_mode} onChange={(e) => set("management_mode", e.target.value)}>
                <option value="monitoring">Monitoring only</option><option value="administration">Administration enabled</option>
              </Select>
            </Field>
            {optionDefs.map((o) => o.type === "boolean" ? (
              <label key={o.key} className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={opts[o.key] === true} onChange={(e) => setOpts({ ...opts, [o.key]: e.target.checked })} /> {o.label}</label>
            ) : o.type === "choice" ? (
              <Field key={o.key} label={o.label} hint={o.help}><Select value={String(opts[o.key] ?? "")} onChange={(e) => setOpts({ ...opts, [o.key]: e.target.value })}><option value="">Default</option>{o.choices?.map((c) => <option key={c}>{c}</option>)}</Select></Field>
            ) : (
              <Field key={o.key} label={o.label} hint={o.help}><Input value={String(opts[o.key] ?? "")} onChange={(e) => setOpts({ ...opts, [o.key]: e.target.value })} /></Field>
            ))}
            {server.engine === "oracle" && (
              <fieldset className="sm:col-span-2 space-y-1 text-[13px]">
                <legend className="mb-1 font-medium">Oracle management packs licensed for this database</legend>
                {([["diagnostics_pack", "Diagnostics Pack"], ["tuning_pack", "Tuning Pack"]] as const).map(([k, l]) => (
                  <label key={k} className="flex items-center gap-2"><input type="checkbox" checked={licensed.includes(k)} onChange={(e) => setLicensed((p) => e.target.checked ? [...p, k] : p.filter((x) => x !== k))} /> {l}</label>
                ))}
                <p className="text-[12px] text-muted">Only tick a pack your organisation licenses for this database. It lets the SQL workspace query pack objects (AWR, ASH, SQL tuning advisors); DBManager's own collectors never use them.</p>
              </fieldset>
            )}
            <Field label="Collection interval"><Select value={f.collection_interval_s} onChange={(e) => set("collection_interval_s", e.target.value)}>{[["15", "15 seconds"], ["30", "30 seconds"], ["60", "1 minute"], ["300", "5 minutes"], ["900", "15 minutes"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
          </fieldset>
        </CardBody>
      </Card>
      <div className="space-y-4">
        {serverCan(server, "credentials.manage") && !server.is_demo && (
          <Card>
            <CardHeader title="Credentials" description="Encrypted with AES-256-GCM. Secrets are write-only." />
            <CardBody className="space-y-3">
              <CredentialEditor server={server} purpose="monitoring" info={server.credentials.monitoring} />
              <CredentialEditor server={server} purpose="admin" info={server.credentials.admin} />
              <CredentialEditor server={server} purpose="readonly" info={server.credentials.readonly ?? null} />
            </CardBody>
          </Card>
        )}
        <Card>
          <CardHeader title="Operations" />
          <CardBody className="space-y-3 text-[13px]">
            {serverCan(server, "collectors.manage") && <div className="flex items-center justify-between gap-3"><span className="text-text-2">Run discovery, metrics, query statistics and advisors now.</span><Button size="sm" onClick={() => collect.mutate()}>Collect now</Button></div>}
            {(serverCan(server, "server.modify") || serverCan(server, "collectors.manage")) && (
              <div className="flex items-center justify-between gap-3"><span className="text-text-2">Check that DBManager can still reach and sign in to this server.</span><Button size="sm" onClick={() => setTesting(true)}><PlugZap /> Test connection</Button></div>
            )}
            {serverCan(server, "security.manage") && (
              <div className="flex items-center justify-between gap-3"><span className="text-text-2">Re-evaluate the security rules now instead of at the next scheduled scan.</span><Button size="sm" disabled={scan.isPending} onClick={() => scan.mutate()}><ShieldCheck /> Scan security now</Button></div>
            )}
            {serverCan(server, "collectors.view") && (
              <div className="flex items-center justify-between gap-3"><span className="text-text-2">Collector diagnostics for support: settings, health, recent jobs and driver versions. Secrets are redacted.</span><Button size="sm" disabled={diagnostics.isPending} onClick={() => diagnostics.mutate()}><Download /> Download diagnostics</Button></div>
            )}
            {serverCan(server, "server.delete") && (
              <div className="flex items-center justify-between gap-3 border-t border-border pt-3">
                <span className="text-text-2">Remove from DBManager. The database server itself isn't touched.</span>
                <Button size="sm" variant="danger-outline" onClick={() => setDeleting(true)}><Trash2 /> Remove server</Button>
              </div>
            )}
          </CardBody>
        </Card>
      </div>
      {testing && <ConnectionTestDialog server={server} onClose={() => setTesting(false)} />}
      <ConfirmDialog open={deleting} onOpenChange={setDeleting} title={`Remove ${server.name}?`} danger expected={server.name}
        confirmLabel="Remove server" busy={remove.isPending} onConfirm={(typed) => remove.mutate(typed)}>
        <p className="text-[13px] text-text-2">Collected metrics, query statistics, findings and stored credentials for this server are deleted. Audit records are kept.</p>
      </ConfirmDialog>
    </div>
  );
}
