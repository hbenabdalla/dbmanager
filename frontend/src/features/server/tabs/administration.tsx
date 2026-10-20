import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, Play, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, get, post } from "@/api/client";
import type { ServerDetail } from "@/api/types";
import { ConfirmDialog } from "@/components/confirm";
import { CodeBlock } from "@/components/page";
import { RiskBadge } from "@/components/status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { ErrorState, SkeletonRows } from "@/components/ui/states";

interface ActionDef { action: string; label: string; risk: string; permission: string; params: string[]; allowed: boolean }
interface ActionsResponse { management_mode: string; available: boolean; blockers: string[]; actions: ActionDef[] }
interface Preview {
  action: string; risk: string; statements: string[]; database: string | null; notes: string[]; target_object: string | null;
  confirmation: string | null; executable: boolean;
}

/** Parameters each action may take, beyond the required ones the backend lists. Engine
 * builders ignore what they don't use and reject what they don't allow. */
const OPTIONAL: Record<string, string[]> = {
  create_schema: ["owner"],
  create_user: ["database", "default_tablespace"],
  drop_user: ["cascade"],
  create_role: ["database"],
  grant: ["database", "schema", "object_name", "role", "grantee_type"],
  revoke: ["database", "schema", "object_name", "role", "grantee_type"],
  create_index: ["schema", "include", "unique", "online"],
};
const LABEL: Record<string, string> = {
  name: "Name", password: "Password", database: "Database", schema: "Schema / owner", table: "Table", columns: "Columns",
  include: "Included columns", privileges: "Privileges", object_type: "Object type", object_name: "Object name",
  grantee: "Grantee", grantee_type: "Grantee type", role: "Role", owner: "Owner", default_tablespace: "Default tablespace",
  unique: "Unique", online: "Online build", cascade: "Cascade (drops owned objects)",
};
const BOOLEAN = new Set(["unique", "online", "cascade"]);
const LISTS = new Set(["columns", "include", "privileges"]);
const OBJECT_TYPES = ["database", "schema", "table", "view", "sequence", "procedure", "function", "package", "role", "system", "server"];

function payload(def: ActionDef, values: Record<string, string | boolean>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of [...def.params, ...(OPTIONAL[def.action] ?? [])]) {
    const v = values[k];
    if (v === undefined || v === "" || v === false) continue;
    out[k] = LISTS.has(k) && typeof v === "string" ? v.split(",").map((x) => x.trim()).filter(Boolean) : v;
  }
  return out;
}

export function AdministrationTab({ server }: { server: ServerDetail }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-actions", server.id], queryFn: () => get<ActionsResponse>(`/servers/${server.id}/admin/actions`) });
  const [params] = useSearchParams();
  // ?action=create_user preselects an action (used by "+ Add → Database user")
  const requested = params.get("action") ?? "";
  const [action, setAction] = useState<string>("");
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [confirming, setConfirming] = useState(false);
  const def = q.data?.actions.find((a) => a.action === action);
  useEffect(() => {
    if (!requested || action || !q.data) return;
    const want = q.data.actions.find((a) => a.action === requested);
    if (want?.allowed) setAction(want.action);
  }, [requested, q.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const doPreview = useMutation({
    mutationFn: () => post<Preview>(`/servers/${server.id}/admin/preview`, { action, params: payload(def!, values) }),
    onSuccess: setPreview,
    onError: (e: ApiError) => { setPreview(null); toast.error(e.message); },
  });
  const execute = useMutation({
    mutationFn: ({ confirm, reason }: { confirm: string; reason: string }) =>
      post<{ ok: boolean; statements_executed: number }>(`/servers/${server.id}/admin/execute`, {
        action, params: payload(def!, values), confirm: confirm || preview?.confirmation || undefined, reason: reason || undefined,
      }),
    onSuccess: (r) => {
      toast.success(`${def?.label} completed`, { description: `${r.statements_executed} statement(s) executed and audited` });
      setConfirming(false); setPreview(null); setValues({});
      qc.invalidateQueries({ queryKey: ["server", server.id] });
    },
    onError: (e: ApiError) => toast.error(e.message),
  });

  if (q.isLoading) return <SkeletonRows rows={6} />;
  if (q.error || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  const fields = def ? [...def.params, ...(OPTIONAL[def.action] ?? [])] : [];
  const needsTyped = preview?.confirmation && preview.confirmation !== "CONFIRM";
  const expected = preview?.confirmation === "server name" ? server.name : preview?.confirmation ?? undefined;

  return (
    <div className="space-y-4">
      {!d.available && (
        <Card className="border-warn/30 bg-warn-soft/40">
          <CardBody className="flex gap-2.5 text-[13px] text-text-2">
            <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warn" />
            <div>
              <div className="font-medium">Changes can be previewed but not executed on this server</div>
              <ul className="mt-1 list-disc pl-4 text-muted">{d.blockers.map((b) => <li key={b}>{b}</li>)}</ul>
            </div>
          </CardBody>
        </Card>
      )}
      <div className="grid gap-4 lg:grid-cols-[1fr_1.1fr]">
        <Card>
          <CardHeader title="Guarded change" description={`Statements are generated by the ${server.engine_label} adapter, shown for review, then executed with the administration credential and audited.`} />
          <CardBody className="space-y-3">
            <Field label="Action">
              <Select aria-label="Action" value={action} onChange={(e) => { setAction(e.target.value); setValues({}); setPreview(null); }}>
                <option value="">Choose an action…</option>
                {d.actions.map((a) => <option key={a.action} value={a.action} disabled={!a.allowed}>{a.label}{a.allowed ? "" : " (no permission)"}</option>)}
              </Select>
            </Field>
            {def && (
              <div className="grid gap-3 sm:grid-cols-2">
                {fields.map((k) => BOOLEAN.has(k) ? (
                  <label key={k} className="flex items-center gap-2 text-[13px]">
                    <input type="checkbox" checked={values[k] === true} onChange={(e) => setValues({ ...values, [k]: e.target.checked })} /> {LABEL[k] ?? k}
                  </label>
                ) : k === "object_type" ? (
                  <Field key={k} label={LABEL[k]}>
                    <Select value={String(values[k] ?? "")} onChange={(e) => setValues({ ...values, [k]: e.target.value })}>
                      <option value="">Choose…</option>{OBJECT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                    </Select>
                  </Field>
                ) : k === "grantee_type" ? (
                  <Field key={k} label={LABEL[k]} hint="Db2 only">
                    <Select value={String(values[k] ?? "")} onChange={(e) => setValues({ ...values, [k]: e.target.value })}>
                      <option value="">user</option><option value="group">group</option><option value="role">role</option>
                    </Select>
                  </Field>
                ) : (
                  <Field key={k} label={`${LABEL[k] ?? k}${def.params.includes(k) ? "" : " (optional)"}`} hint={LISTS.has(k) ? "Comma separated" : undefined}>
                    <Input type={k === "password" ? "password" : "text"} autoComplete={k === "password" ? "new-password" : "off"}
                      value={String(values[k] ?? "")} onChange={(e) => setValues({ ...values, [k]: e.target.value })} />
                  </Field>
                ))}
              </div>
            )}
            <div className="flex items-center gap-2">
              <Button onClick={() => doPreview.mutate()} disabled={!def || doPreview.isPending}><Eye /> Preview statements</Button>
              {def && <RiskBadge risk={def.risk} />}
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Review" description="Passwords are masked here, never stored and never written to the audit log." />
          <CardBody className="space-y-3">
            {!preview ? <p className="text-[13px] text-muted">Preview an action to see the exact statements.</p> : (
              <>
                <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
                  <RiskBadge risk={preview.risk} />
                  {preview.database && <Badge tone="outline">database {preview.database}</Badge>}
                  {preview.target_object && <Badge tone="outline">{preview.target_object}</Badge>}
                </div>
                <CodeBlock code={preview.statements.join(";\n") + ";"} maxHeight="16rem" />
                {preview.notes.length > 0 && <ul className="list-disc pl-4 text-[12.5px] text-muted">{preview.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
                <Button variant={preview.risk === "DESTRUCTIVE" ? "danger" : "primary"} disabled={!preview.executable || !d.available || execute.isPending}
                  onClick={() => (preview.confirmation ? setConfirming(true) : execute.mutate({ confirm: "", reason: "" }))}>
                  <Play /> Execute
                </Button>
              </>
            )}
          </CardBody>
        </Card>
      </div>
      <ConfirmDialog open={confirming} onOpenChange={setConfirming} title={`${def?.label ?? "Change"} on ${server.name}`}
        expected={needsTyped ? expected : undefined} expectedLabel="Type the server name to confirm"
        danger={preview?.risk === "DESTRUCTIVE"} askReason reasonRequired={preview?.risk === "DESTRUCTIVE" && server.environment?.tier === "production"}
        busy={execute.isPending} confirmLabel="Execute"
        onConfirm={(typed, reason) => execute.mutate({ confirm: needsTyped ? typed : "CONFIRM", reason })}>
        <CodeBlock code={(preview?.statements ?? []).join(";\n") + ";"} maxHeight="12rem" />
      </ConfirmDialog>
    </div>
  );
}
