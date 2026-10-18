import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError, del, get, patch, post } from "@/api/client";
import type { Group, IdentityProvider } from "@/api/types";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { IdentityWizardDialog } from "./idp-wizards";

const OIDC_FIELDS: [string, string, string?][] = [
  ["issuer", "Issuer URL", "https://login.microsoftonline.com/<tenant>/v2.0"], ["client_id", "Client ID"],
  ["scopes", "Scopes", "openid profile email"], ["username_claim", "Username claim", "preferred_username"], ["groups_claim", "Groups claim", "groups"],
];
const LDAP_FIELDS: [string, string, string?][] = [
  ["server_uri", "Server URI", "ldaps://dc01.corp.example:636"], ["bind_dn", "Service account DN", "CN=svc-dbm,OU=Service,DC=corp,DC=example"],
  ["user_base_dn", "User search base", "OU=Users,DC=corp,DC=example"], ["user_filter", "User filter", "(&(objectClass=user)(sAMAccountName={username}))"],
  ["attr_username", "Username attribute", "sAMAccountName"], ["attr_email", "Email attribute", "mail"], ["attr_display_name", "Display name attribute", "displayName"],
  ["group_attribute", "Group attribute", "memberOf"],
];

function ProviderDialog({ kind, provider, onClose }: { kind: "ldap" | "oidc"; provider: IdentityProvider | null; onClose: () => void }) {
  const fields = kind === "oidc" ? OIDC_FIELDS : LDAP_FIELDS;
  const [name, setName] = useState(provider?.name ?? (kind === "oidc" ? "Single sign-on" : "Active Directory"));
  const [enabled, setEnabled] = useState(provider?.enabled ?? false);
  const [cfg, setCfg] = useState<Record<string, unknown>>(provider?.config ?? (kind === "ldap" ? { user_filter: "(&(objectClass=user)(sAMAccountName={username}))", attr_username: "sAMAccountName", group_attribute: "memberOf", verify_cert: true } : { scopes: "openid profile email", groups_claim: "groups", username_claim: "preferred_username" }));
  const [secret, setSecret] = useState("");
  const m = useMutation({
    mutationFn: () => {
      const config = Object.fromEntries(Object.entries(cfg).filter(([k]) => k !== "redirect_uri_effective"));
      return provider ? patch(`/identity-providers/${provider.id}`, { name, enabled, config, secret: secret || undefined }) : post("/identity-providers", { kind, name, enabled, config, secret: secret || undefined });
    },
    onSuccess: () => { toast.success("Identity provider saved"); onClose(); },
    onError: (e: ApiError) => toast.error(e.message, { description: (e.details?.missing as string[] | undefined)?.join(", ") }),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-2xl" title={provider ? `Edit ${provider.name}` : kind === "oidc" ? "Add OpenID Connect provider" : "Add LDAP / Active Directory"}
      description={kind === "oidc" ? "Works with Keycloak, Microsoft Entra ID, Okta, Auth0 and any compliant provider. Uses the authorization code flow with PKCE." : "LDAPS or StartTLS is required. Users bind with their own credentials; the service account only searches."}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" onClick={() => m.mutate()} disabled={m.isPending}>Save provider</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Display name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <label className="flex items-center gap-2 pt-6 text-[13px]"><Switch checked={enabled} onCheckedChange={setEnabled} label="Enabled" /> Enabled</label>
        {fields.map(([k, l, ph]) => <Field key={k} label={l} className={k === "user_filter" || k === "issuer" ? "sm:col-span-2" : undefined}><Input value={String(cfg[k] ?? "")} placeholder={ph} onChange={(e) => setCfg({ ...cfg, [k]: e.target.value })} /></Field>)}
        {kind === "ldap" && <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={Boolean(cfg.start_tls)} onChange={(e) => setCfg({ ...cfg, start_tls: e.target.checked })} /> Use StartTLS (for ldap:// URIs)</label>}
        <Field label={kind === "oidc" ? "Client secret" : "Service account password"} hint={provider?.has_secret ? "Stored encrypted. Leave empty to keep it." : "Stored encrypted, never shown again"}>
          <Input type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} />
        </Field>
        {kind === "oidc" && provider && <Field label="Redirect URI to register with the provider" className="sm:col-span-2"><Input readOnly value={String(provider.config.redirect_uri_effective ?? "")} className="font-mono text-[12px]" /></Field>}
      </div>
    </Dialog>
  );
}

function Mappings({ p }: { p: IdentityProvider }) {
  const qc = useQueryClient();
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => get<Group[]>("/groups") });
  const [ext, setExt] = useState("");
  const [gid, setGid] = useState("");
  const add = useMutation({ mutationFn: () => post(`/identity-providers/${p.id}/mappings`, { external_group: ext, group_id: gid }), onSuccess: () => { setExt(""); qc.invalidateQueries({ queryKey: ["idps"] }); }, onError: (e: ApiError) => toast.error(e.message) });
  const rm = useMutation({ mutationFn: (id: string) => del(`/identity-providers/${p.id}/mappings/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["idps"] }) });
  const gname = (id: string) => groups.data?.find((g) => g.id === id)?.name ?? id;
  return (
    <div className="space-y-2">
      <div className="text-[12.5px] font-medium">Group mappings</div>
      <ul className="space-y-1 text-[13px]">
        {p.mappings.map((m) => <li key={m.id} className="flex items-center gap-2"><code className="font-mono text-[12px]">{m.external_group}</code><span className="text-muted">maps to</span><Badge tone="accent">{gname(m.group_id)}</Badge><Button size="icon" variant="ghost" aria-label="Remove mapping" onClick={() => rm.mutate(m.id)}><Trash2 /></Button></li>)}
        {!p.mappings.length && <li className="text-muted">No mappings. Users from this provider get no roles until a mapping or a direct assignment exists.</li>}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Input aria-label="External group" className="w-72" placeholder={p.kind === "ldap" ? "CN=DBA-Prod,OU=Groups,DC=corp,DC=example" : "group name or id from the token"} value={ext} onChange={(e) => setExt(e.target.value)} />
        <Select aria-label="Group" className="w-48" value={gid} onChange={(e) => setGid(e.target.value)}><option value="">Platform group…</option>{groups.data?.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}</Select>
        <Button disabled={!ext || !gid} onClick={() => add.mutate()}>Add mapping</Button>
      </div>
    </div>
  );
}

export function IdentityProvidersPage({ only }: { only?: "ldap" | "oidc" }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["idps"], queryFn: () => get<IdentityProvider[]>("/identity-providers") });
  const [edit, setEdit] = useState<{ kind: "ldap" | "oidc"; p: IdentityProvider | null; raw?: boolean } | null>(null);
  const test = useMutation({ mutationFn: (id: string) => post<{ ok: boolean; message: string }>(`/identity-providers/${id}/test`), onSuccess: (r) => toast[r.ok ? "success" : "error"](r.message) });
  const remove = useMutation({ mutationFn: (id: string) => del(`/identity-providers/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["idps"] }) });
  const list = (q.data ?? []).filter((p) => !only || p.kind === only);
  return (
    <div>
      <PageHeader title={only === "ldap" ? "LDAP / Active Directory" : "Identity providers"}
        description={only === "ldap" ? "Directory sign-in with group-to-role mapping." : "Single sign-on with OpenID Connect, and LDAP directories. SAML is planned; OIDC is the recommended federation path."}
        crumbs={[{ label: "Platform" }, { label: only === "ldap" ? "LDAP" : "Identity providers" }]}
        actions={<div className="flex gap-2">{only !== "ldap" && <Button variant="primary" onClick={() => setEdit({ kind: "oidc", p: null })}><Plus /> OIDC provider</Button>}<Button variant={only === "ldap" ? "primary" : "secondary"} onClick={() => setEdit({ kind: "ldap", p: null })}><Plus /> LDAP directory</Button></div>} />
      {q.isLoading ? <SkeletonRows /> : !list.length ? <Card><EmptyState title="No providers configured">Local accounts keep working. Add a provider to let people sign in with their corporate identity.</EmptyState></Card> : (
        <div className="space-y-4">
          {list.map((p) => (
            <Card key={p.id}>
              <CardHeader title={<span className="flex items-center gap-2">{p.name}<Badge tone="outline">{p.kind.toUpperCase()}</Badge>{p.enabled ? <Badge tone="ok">enabled</Badge> : <Badge>disabled</Badge>}</span>}
                description={String(p.config.issuer ?? p.config.server_uri ?? "")}
                actions={<><Button size="sm" onClick={() => test.mutate(p.id)}>Test</Button><Button size="sm" onClick={() => setEdit({ kind: p.kind as "ldap" | "oidc", p })}>Edit</Button><Button size="sm" variant="ghost" onClick={() => setEdit({ kind: p.kind as "ldap" | "oidc", p, raw: true })}>All fields</Button><Button size="sm" variant="danger-outline" onClick={() => remove.mutate(p.id)}>Delete</Button></>} />
              <CardBody><Mappings p={p} /></CardBody>
            </Card>
          ))}
        </div>
      )}
      {edit && (edit.raw
        ? <ProviderDialog kind={edit.kind} provider={edit.p} onClose={() => { setEdit(null); qc.invalidateQueries({ queryKey: ["idps"] }); }} />
        : <IdentityWizardDialog kind={edit.kind} provider={edit.p} onClose={() => { setEdit(null); qc.invalidateQueries({ queryKey: ["idps"] }); }} />)}
    </div>
  );
}
