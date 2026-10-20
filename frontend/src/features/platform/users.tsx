import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus, ShieldOff, Trash2, UserPlus, Users as UsersIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError, del, get, patch, post, put } from "@/api/client";
import { useAllServers, useClusters, useEnvironments, useSites } from "@/api/hooks";
import type { Assignment, Group, Page, Role, User } from "@/api/types";
import { ConfirmDialog } from "@/components/confirm";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { EmptyState } from "@/components/ui/states";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { UserTokens } from "@/features/account/api-tokens";
import { useAuth } from "@/lib/auth";
import { dateTime, relTime } from "@/lib/format";

const err = (e: ApiError) => {
  const pw = (e.details?.password as string[] | undefined)?.join("; ");
  const missing = (e.details?.missing_permissions as string[] | undefined)?.join(", ");
  toast.error(e.message, { description: pw ?? (missing ? `Missing: ${missing}` : undefined) });
};

export function ScopePicker({ value, onChange }: { value: { scope_type: string; scope_id: string }; onChange: (v: { scope_type: string; scope_id: string }) => void }) {
  const envs = useEnvironments();
  const sites = useSites();
  const clusters = useClusters();
  const servers = useAllServers();
  const [server, setServer] = useState("");
  const [db, setDb] = useState("");
  const [schema, setSchema] = useState("");
  const t = value.scope_type;
  const setType = (scope_type: string) => onChange({ scope_type, scope_id: scope_type === "platform" ? "*" : "" });
  const compose = (s: string, d: string, sc: string) => onChange({ scope_type: t, scope_id: t === "server" ? s : t === "database" ? (s && d ? `${s}/${d}` : "") : s && d && sc ? `${s}/${d}/${sc}` : "" });
  const opts = t === "environment" ? envs.data : t === "site" ? sites.data : t === "cluster" ? clusters.data : undefined;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label="Scope">
        <Select value={t} onChange={(e) => setType(e.target.value)}>
          {[["platform", "Entire platform"], ["environment", "Environment"], ["site", "Site"], ["cluster", "Cluster"], ["server", "Server"], ["database", "Database"], ["schema", "Schema"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
      </Field>
      {opts && (
        <Field label={t[0].toUpperCase() + t.slice(1)}>
          <Select value={value.scope_id} onChange={(e) => onChange({ scope_type: t, scope_id: e.target.value })}><option value="">Choose…</option>{opts.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</Select>
        </Field>
      )}
      {["server", "database", "schema"].includes(t) && (
        <Field label="Server">
          <Select value={server} onChange={(e) => { setServer(e.target.value); compose(e.target.value, db, schema); }}><option value="">Choose…</option>{servers.data?.items.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</Select>
        </Field>
      )}
      {["database", "schema"].includes(t) && <Field label="Database"><Input value={db} onChange={(e) => { setDb(e.target.value); compose(server, e.target.value, schema); }} /></Field>}
      {t === "schema" && <Field label="Schema"><Input value={schema} onChange={(e) => { setSchema(e.target.value); compose(server, db, e.target.value); }} /></Field>}
    </div>
  );
}

function AssignDialog({ principal, open, onOpenChange }: { principal: { type: "user" | "group"; id: string; name: string }; open: boolean; onOpenChange: (o: boolean) => void }) {
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => get<Role[]>("/roles") });
  const [role, setRole] = useState("");
  const [scope, setScope] = useState({ scope_type: "environment", scope_id: "" });
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => post("/role-assignments", { principal_type: principal.type, principal_id: principal.id, role_id: role, ...scope }),
    onSuccess: () => { toast.success("Role assigned"); qc.invalidateQueries({ queryKey: ["assignments"] }); onOpenChange(false); },
    onError: err,
  });
  const r = roles.data?.find((x) => x.id === role);
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={`Assign a role to ${principal.name}`} width="max-w-xl"
      description="You can only hand out permissions you hold yourself, at a scope you manage. You can't change your own access."
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!role || !scope.scope_id || m.isPending} onClick={() => m.mutate()}>Assign role</Button></>}>
      <div className="space-y-4">
        <Field label="Role"><Select value={role} onChange={(e) => setRole(e.target.value)}><option value="">Choose a role…</option>{roles.data?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
        {r && <div className="flex flex-wrap gap-1">{r.permissions.map((p) => <Badge key={p} tone="outline" className="font-mono">{p}</Badge>)}</div>}
        <ScopePicker value={scope} onChange={setScope} />
      </div>
    </Dialog>
  );
}

export function AssignmentList({ principalId, principalName, type }: { principalId: string; principalName: string; type: "user" | "group" }) {
  const { me, can } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["assignments", principalId], queryFn: () => get<Assignment[]>("/role-assignments", { principal_id: principalId }) });
  const remove = useMutation({ mutationFn: (id: string) => del(`/role-assignments/${id}`), onSuccess: () => { toast.success("Assignment removed"); qc.invalidateQueries({ queryKey: ["assignments"] }); }, onError: err });
  const self = me?.id === principalId;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-[13px] font-semibold">Role assignments</h4>
        {can("rbac.manage") && !self && <Button size="sm" onClick={() => setOpen(true)}><Plus /> Assign role</Button>}
      </div>
      {self && <p className="mb-2 text-[12px] text-muted">You can't change your own role assignments.</p>}
      <ul className="divide-y divide-border rounded-md border border-border">
        {q.data?.map((a) => (
          <li key={a.id} className="flex items-center gap-3 px-3 py-2 text-[13px]">
            <span className="font-medium">{a.role_name}</span>
            <span className="text-muted">on</span>
            <Badge tone="accent">{a.scope_type}</Badge><span>{a.scope_label}</span>
            {can("rbac.manage") && !self && <Button size="icon" variant="ghost" className="ml-auto" aria-label="Remove assignment" onClick={() => remove.mutate(a.id)}><Trash2 /></Button>}
          </li>
        ))}
        {q.data?.length === 0 && <li className="px-3 py-3 text-[13px] text-muted">No roles assigned. This {type} can sign in but sees nothing.</li>}
      </ul>
      {open && <AssignDialog principal={{ type, id: principalId, name: principalName }} open={open} onOpenChange={setOpen} />}
    </div>
  );
}

interface EffectiveGrant { role: string; scope_type: string; scope_id: string; scope_label: string | null; via_group: string | null; permissions: string[] }

/** What a user can actually do: every grant (direct or via a group) with its permissions. */
export function EffectivePermissions({ userId }: { userId: string }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["effective-permissions", userId],
    queryFn: () => get<{ user: string; grants: EffectiveGrant[] }>(`/users/${userId}/effective-permissions`),
    enabled: open,
  });
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-[13px] font-semibold">Effective permissions</h4>
        <Button size="sm" variant="ghost" onClick={() => setOpen((o) => !o)}>{open ? "Hide" : "Show"}</Button>
      </div>
      {open && (q.isLoading ? <p className="text-[13px] text-muted">Loading…</p> : (
        <ul className="divide-y divide-border rounded-md border border-border" data-testid="effective-permissions">
          {q.data?.grants.map((g, i) => (
            <li key={i} className="space-y-1 px-3 py-2 text-[13px]">
              <div><span className="font-medium">{g.role}</span> <span className="text-muted">on</span> <Badge tone="accent">{g.scope_type}</Badge> {g.scope_label ?? g.scope_id}
                {g.via_group && <span className="text-muted"> (via group {g.via_group})</span>}</div>
              <div className="flex flex-wrap gap-1">{g.permissions.map((p) => <Badge key={p} tone="outline" className="font-mono">{p}</Badge>)}</div>
            </li>
          ))}
          {q.data?.grants.length === 0 && <li className="px-3 py-3 text-[13px] text-muted">No permissions anywhere.</li>}
        </ul>
      ))}
    </div>
  );
}

function UserDialog({ user, onClose }: { user: User; onClose: () => void }) {
  const qc = useQueryClient();
  const { canPlatform, me } = useAuth();
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState<"mfa" | "delete" | null>(null);
  const mfaReset = useMutation({
    mutationFn: () => post<{ message: string }>(`/users/${user.id}/mfa/reset`),
    onSuccess: (r) => { toast.success(r.message); setConfirm(null); },
    onError: err,
  });
  const remove = useMutation({
    mutationFn: () => del(`/users/${user.id}`),
    onSuccess: () => { toast.success(`${user.username} deleted`); qc.invalidateQueries({ queryKey: ["users"] }); setConfirm(null); onClose(); },
    onError: err,
  });
  const toggle = useMutation({ mutationFn: (v: Partial<{ is_active: boolean; unlock: boolean }>) => patch<User>(`/users/${user.id}`, v), onSuccess: () => { toast.success("User updated"); qc.invalidateQueries({ queryKey: ["users"] }); }, onError: err });
  const reset = useMutation({ mutationFn: () => post(`/users/${user.id}/reset-password`, { new_password: pw, must_change_password: true }), onSuccess: () => { toast.success("Password reset; the user must change it at next sign-in"); setPw(""); }, onError: err });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-2xl" title={user.display_name ?? user.username} description={`${user.username}, ${user.auth_source} account`}>
      <div className="space-y-5">
        <div className="grid gap-2 text-[13px] sm:grid-cols-3">
          <div><div className="text-[12px] text-muted">Last sign-in</div>{relTime(user.last_login_at)}</div>
          <div><div className="text-[12px] text-muted">Created</div>{dateTime(user.created_at)}</div>
          <div><div className="text-[12px] text-muted">Groups</div>{user.groups.map((g) => g.name).join(", ") || "None"}</div>
          {user.auth_source === "local" && <div><div className="text-[12px] text-muted">Multi-factor</div>{user.mfa_enabled ? "Authenticator enrolled" : "Not enrolled"}</div>}
        </div>
        {canPlatform("users.manage") && (
          <div className="flex flex-wrap items-center gap-4 rounded-md border border-border p-3 text-[13px]">
            <label className="flex items-center gap-2"><Switch checked={user.is_active} disabled={user.id === me?.id} onCheckedChange={(v) => toggle.mutate({ is_active: v })} label="Active" /> Account active</label>
            {user.locked_until && new Date(user.locked_until) > new Date() && <Button size="sm" onClick={() => toggle.mutate({ unlock: true })}>Unlock account</Button>}
            {user.auth_source === "local" && (
              <div className="ml-auto flex items-center gap-2">
                <Input type="password" aria-label="New password" autoComplete="new-password" placeholder="New temporary password" className="w-56" value={pw} onChange={(e) => setPw(e.target.value)} />
                <Button size="sm" disabled={!pw || reset.isPending} onClick={() => reset.mutate()}>Reset password</Button>
              </div>
            )}
          </div>
        )}
        {canPlatform("users.manage") && (
          <div className="flex flex-wrap gap-2">
            {user.auth_source === "local" && user.mfa_enabled && <Button size="sm" onClick={() => setConfirm("mfa")}><ShieldOff /> Reset MFA</Button>}
            {user.id !== me?.id && <Button size="sm" variant="danger" onClick={() => setConfirm("delete")}><Trash2 /> Delete user</Button>}
          </div>
        )}
        <AssignmentList principalId={user.id} principalName={user.username} type="user" />
        <EffectivePermissions userId={user.id} />
        {canPlatform("users.manage") && <UserTokens userId={user.id} />}
      </div>
      <ConfirmDialog open={confirm === "mfa"} onOpenChange={(o) => !o && setConfirm(null)} title={`Reset MFA of ${user.username}?`}
        confirmLabel="Remove authenticator" busy={mfaReset.isPending} onConfirm={() => mfaReset.mutate()}>
        Use this when the user lost their authenticator. Their authenticator and recovery codes are removed; if the MFA policy
        applies to them they must set up a new authenticator at their next sign-in. The reset is audited.
      </ConfirmDialog>
      <ConfirmDialog open={confirm === "delete"} onOpenChange={(o) => !o && setConfirm(null)} title={`Delete ${user.username}?`} danger
        expected={user.username} expectedLabel="Type the username to confirm" confirmLabel="Delete user" busy={remove.isPending}
        onConfirm={() => remove.mutate()}>
        The account, its role assignments and its API tokens are removed. Audit records keep the username. This cannot be undone.
      </ConfirmDialog>
    </Dialog>
  );
}

function CreateUser({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [f, setF] = useState({ username: "", display_name: "", email: "", password: "" });
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: () => post<User>("/users", { ...f, email: f.email || null, display_name: f.display_name || null, must_change_password: true }),
    onSuccess: (u) => { toast.success(`${u.username} created`, { description: "Assign a role so the user can see servers." }); qc.invalidateQueries({ queryKey: ["users"] }); onOpenChange(false); setF({ username: "", display_name: "", email: "", password: "" }); },
    onError: err,
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Create local user" description="Directory users (LDAP/OIDC) are created automatically on their first sign-in."
      footer={<><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="primary" disabled={!f.username || !f.password || m.isPending} onClick={() => m.mutate()}>Create user</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Username"><Input autoComplete="off" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} /></Field>
        <Field label="Display name"><Input value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} /></Field>
        <Field label="Email"><Input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label="Temporary password" hint="Changed by the user at first sign-in"><Input type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
      </div>
    </Dialog>
  );
}

function MembersDialog({ group, onClose }: { group: Group; onClose: () => void }) {
  const qc = useQueryClient();
  const { me } = useAuth();
  const users = useQuery({ queryKey: ["users", "all"], queryFn: () => get<Page<User>>("/users", { page_size: 500 }) });
  const [ids, setIds] = useState<Set<string>>(() => new Set(group.members.map((m) => m.id)));
  const save = useMutation({
    mutationFn: () => put<Group>(`/groups/${group.id}/members`, { user_ids: [...ids] }),
    onSuccess: () => { toast.success("Members updated"); qc.invalidateQueries({ queryKey: ["groups"] }); qc.invalidateQueries({ queryKey: ["users"] }); onClose(); },
    onError: err,
  });
  const toggle = (id: string) => setIds((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Members of ${group.name}`}
      description="Members receive the group's role assignments. Memberships synchronised from a directory are kept as they are."
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={save.isPending} onClick={() => save.mutate()}>Save members</Button></>}>
      <ul className="max-h-80 divide-y divide-border overflow-y-auto rounded-md border border-border">
        {users.data?.items.map((u) => {
          const self = u.id === me?.id && !group.members.some((m) => m.id === u.id);
          return (
            <li key={u.id} className="px-3 py-1.5 text-[13px]">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={ids.has(u.id)} disabled={self} onChange={() => toggle(u.id)} aria-label={`Member ${u.username}`} />
                <span className="font-medium">{u.display_name ?? u.username}</span><span className="text-muted">{u.username}</span>
                {self && <span className="text-[12px] text-muted">(you cannot add yourself)</span>}
              </label>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}

function Groups() {
  const qc = useQueryClient();
  const { canPlatform } = useAuth();
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => get<Group[]>("/groups") });
  const [name, setName] = useState("");
  const [selId, setSelId] = useState<string | null>(null);
  const sel = groups.data?.find((g) => g.id === selId) ?? null;
  const setSel = (g: Group | null) => setSelId(g?.id ?? null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const create = useMutation({ mutationFn: () => post("/groups", { name }), onSuccess: () => { setName(""); qc.invalidateQueries({ queryKey: ["groups"] }); }, onError: err });
  const remove = useMutation({
    mutationFn: (g: Group) => del(`/groups/${g.id}`),
    onSuccess: () => { toast.success("Group deleted"); setDeleting(false); setSel(null); qc.invalidateQueries({ queryKey: ["groups"] }); },
    onError: err,
  });
  return (
    <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
      <Card>
        <CardHeader title="Groups" description="Directory groups map to these through identity providers" />
        <ul className="divide-y divide-border">
          {groups.data?.map((g) => <li key={g.id}><button className="flex w-full items-center justify-between px-4 py-2 text-left text-[13px] hover:bg-surface-2" onClick={() => setSel(g)}><span className="font-medium">{g.name}</span><span className="text-muted tabular">{g.members.length}</span></button></li>)}
          {groups.data?.length === 0 && <li className="px-4 py-3 text-[13px] text-muted">No groups yet</li>}
        </ul>
        {canPlatform("users.manage") && <div className="flex gap-2 border-t border-border p-3"><Input aria-label="New group" placeholder="New group name" value={name} onChange={(e) => setName(e.target.value)} /><Button disabled={!name} onClick={() => create.mutate()}>Add</Button></div>}
      </Card>
      <Card>
        {sel ? (
          <CardBody className="space-y-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div><h3 className="text-[15px] font-semibold">{sel.name}</h3><p className="text-[12.5px] text-muted">Members: {sel.members.map((m) => m.username).join(", ") || "none"}</p></div>
              {canPlatform("users.manage") && (
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => setEditing(true)}><UsersIcon /> Edit members</Button>
                  <Button size="sm" variant="danger" onClick={() => setDeleting(true)}><Trash2 /> Delete group</Button>
                </div>
              )}
            </div>
            <AssignmentList principalId={sel.id} principalName={sel.name} type="group" />
          </CardBody>
        ) : <EmptyState title="Select a group" />}
      </Card>
      {editing && sel && <MembersDialog group={sel} onClose={() => setEditing(false)} />}
      <ConfirmDialog open={deleting} onOpenChange={setDeleting} title={`Delete group ${sel?.name ?? ""}?`} danger
        expected={sel?.name} expectedLabel="Type the group name to confirm" confirmLabel="Delete group" busy={remove.isPending}
        onConfirm={() => sel && remove.mutate(sel)}>
        Members lose the roles assigned to this group. Directory group mappings pointing to it stop working.
      </ConfirmDialog>
    </div>
  );
}

export function UsersPage() {
  const { canPlatform } = useAuth();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<User | null>(null);
  const [creating, setCreating] = useState(false);
  // "?create=1" (from the "+ Add" menu) opens the create dialog for users.manage holders
  const [params, setParams] = useSearchParams();
  const createFlag = params.get("create") === "1";
  const mayCreate = canPlatform("users.manage");
  useEffect(() => {
    if (createFlag && mayCreate) setCreating(true);
  }, [createFlag, mayCreate]);
  const changeCreating = (o: boolean) => {
    setCreating(o);
    if (!o && createFlag) {
      const next = new URLSearchParams(params);
      next.delete("create");
      setParams(next, { replace: true });
    }
  };
  const users = useQuery({ queryKey: ["users", q, page], queryFn: () => get<Page<User>>("/users", { q, page, page_size: 50 }), placeholderData: (p) => p });
  const columns: ColumnDef<User, unknown>[] = [
    { header: "User", accessorKey: "username", cell: ({ row: { original: u } }) => <div><div className="font-medium">{u.display_name ?? u.username}</div><div className="text-[12px] text-muted">{u.username}{u.email ? `, ${u.email}` : ""}</div></div> },
    { header: "Source", accessorKey: "auth_source", cell: ({ getValue }) => <Badge tone="outline">{getValue() as string}</Badge> },
    { header: "Status", id: "status", cell: ({ row: { original: u } }) => !u.is_active ? <Badge tone="down">disabled</Badge> : u.locked_until && new Date(u.locked_until) > new Date() ? <Badge tone="warn">locked</Badge> : <Badge tone="ok">active</Badge> },
    { header: "Groups", accessorFn: (u) => u.groups.map((g) => g.name).join(", ") },
    { header: "Last sign-in", accessorKey: "last_login_at", cell: ({ getValue }) => relTime(getValue() as string) },
  ];
  return (
    <div>
      <PageHeader title="Users" description="Platform accounts, groups and their scoped role assignments." crumbs={[{ label: "Platform" }, { label: "Users" }]}
        actions={canPlatform("users.manage") && <Button variant="primary" onClick={() => setCreating(true)}><UserPlus /> Create user</Button>} />
      <Tabs defaultValue="users">
        <TabsList className="mb-4"><TabsTrigger value="users">Users</TabsTrigger><TabsTrigger value="groups">Groups</TabsTrigger></TabsList>
        <TabsContent value="users">
          <Card>
            <div className="border-b border-border p-3"><Input aria-label="Search users" className="w-72" placeholder="Search name or email" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></div>
            <DataTable columns={columns} data={users.data?.items} loading={users.isLoading} error={users.error} onRowClick={setSel}
              manual={users.data ? { page, pageSize: 50, total: users.data.total, onPage: setPage } : undefined} />
          </Card>
        </TabsContent>
        <TabsContent value="groups"><Groups /></TabsContent>
      </Tabs>
      {sel && <UserDialog user={sel} onClose={() => setSel(null)} />}
      <CreateUser open={creating} onOpenChange={changeCreating} />
    </div>
  );
}
