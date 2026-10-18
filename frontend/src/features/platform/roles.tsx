import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError, get, patch, post } from "@/api/client";
import type { Permission, Role } from "@/api/types";
import { PageHeader } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { Tooltip } from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

export function RolesPage() {
  const { canPlatform } = useAuth();
  const qc = useQueryClient();
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => get<Role[]>("/roles") });
  const perms = useQuery({ queryKey: ["permissions"], queryFn: () => get<Permission[]>("/permissions") });
  const [editing, setEditing] = useState<Role | "new" | null>(null);
  const groups = useMemo(() => {
    const m = new Map<string, Permission[]>();
    for (const p of perms.data ?? []) m.set(p.group, [...(m.get(p.group) ?? []), p]);
    return [...m.entries()];
  }, [perms.data]);
  const list = roles.data ?? [];
  return (
    <div>
      <PageHeader title="Roles" description="Roles are named sets of permissions. Permissions are always checked by the backend, and assignments decide where they apply."
        crumbs={[{ label: "Platform" }, { label: "Roles" }]}
        actions={canPlatform("roles.manage") && <Button variant="primary" onClick={() => setEditing("new")}><Plus /> New role</Button>} />
      <Card className="overflow-hidden">
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full border-collapse text-[12.5px]">
            <thead className="sticky top-0 z-10 bg-surface-2">
              <tr className="border-b border-border">
                <th className="sticky left-0 z-10 min-w-[260px] bg-surface-2 px-3 py-2 text-left font-medium text-muted">Permission</th>
                {list.map((r) => (
                  <th key={r.id} className="min-w-[96px] px-2 py-2 text-center align-bottom font-medium">
                    <button className={cn("leading-tight", !r.is_system && canPlatform("roles.manage") && "text-accent hover:underline")} onClick={() => !r.is_system && canPlatform("roles.manage") && setEditing(r)}>
                      {r.name}
                    </button>
                    {r.is_system && <Tooltip content="Built-in role; create a custom role to change permissions"><Lock className="mx-auto mt-1 size-3 text-muted" /></Tooltip>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map(([g, ps]) => (
                <Fragment key={g}>
                  <tr className="bg-surface-2/60"><td colSpan={list.length + 1} className="sticky left-0 px-3 py-1.5 text-2xs font-semibold text-muted">{g}</td></tr>
                  {ps.map((p) => (
                    <tr key={p.key} className="border-b border-border/60 hover:bg-surface-2/50">
                      <td className="sticky left-0 bg-surface px-3 py-1.5">
                        <div className="font-mono text-[12px]">{p.key}</div><div className="text-[11.5px] text-muted">{p.description}{p.platform_only ? ", platform scope only" : ""}</div>
                      </td>
                      {list.map((r) => <td key={r.id} className="text-center">{r.permissions.includes(p.key) ? <span className="inline-block size-2.5 rounded-full bg-accent" aria-label="granted" /> : <span className="inline-block size-2.5 rounded-full bg-surface-3" aria-label="not granted" />}</td>)}
                    </tr>
                  ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {editing && <RoleEditor role={editing === "new" ? null : editing} perms={perms.data ?? []} onClose={() => { setEditing(null); qc.invalidateQueries({ queryKey: ["roles"] }); }} />}
    </div>
  );
}

function RoleEditor({ role, perms, onClose }: { role: Role | null; perms: Permission[]; onClose: () => void }) {
  const [name, setName] = useState(role?.name ?? "");
  const [desc, setDesc] = useState(role?.description ?? "");
  const [sel, setSel] = useState<Set<string>>(new Set(role?.permissions ?? []));
  const m = useMutation({
    mutationFn: () => role ? patch(`/roles/${role.id}`, { name, description: desc, permissions: [...sel] }) : post("/roles", { name, description: desc, permissions: [...sel] }),
    onSuccess: () => { toast.success(role ? "Role updated" : "Role created"); onClose(); },
    onError: (e: ApiError) => toast.error(e.message),
  });
  const toggle = (k: string) => setSel((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const grouped = perms.reduce<Record<string, Permission[]>>((a, p) => ({ ...a, [p.group]: [...(a[p.group] ?? []), p] }), {});
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} width="max-w-3xl" title={role ? `Edit ${role.name}` : "New role"}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!name || m.isPending} onClick={() => m.mutate()}>{role ? "Save role" : "Create role"}</Button></>}>
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Description"><Input value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {Object.entries(grouped).map(([g, ps]) => (
            <fieldset key={g}><legend className="mb-1.5 text-[12.5px] font-semibold">{g}</legend>
              {ps.map((p) => (
                <label key={p.key} className="flex cursor-pointer items-start gap-2 py-0.5 text-[12.5px]">
                  <input type="checkbox" className="mt-0.5" checked={sel.has(p.key)} onChange={() => toggle(p.key)} />
                  <span><span className="font-mono">{p.key}</span>{p.platform_only && <Badge className="ml-1">platform</Badge>}<span className="block text-[11.5px] text-muted">{p.description}</span></span>
                </label>
              ))}
            </fieldset>
          ))}
        </div>
      </div>
    </Dialog>
  );
}
