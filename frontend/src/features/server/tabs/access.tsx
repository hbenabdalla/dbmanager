import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { get } from "@/api/client";
import type { DatabaseInfo, DbUser, Privilege, ServerDetail } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardHeader } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Input, Select } from "@/components/ui/input";
import { Tooltip } from "@/components/ui/tooltip";
import { dateTime, relTime } from "@/lib/format";
import { AdministrationHint } from "../../shared/admin-link";

function AdminCell({ u }: { u: DbUser }) {
  if (!u.admin_reasons.length) return <span className="text-muted">—</span>;
  return <Tooltip content={u.admin_reasons.join(", ")}><span><Badge tone="crit"><ShieldAlert className="size-3" /> {u.admin_reasons[0]}{u.admin_reasons.length > 1 ? ` +${u.admin_reasons.length - 1}` : ""}</Badge></span></Tooltip>;
}

export function UsersTab({ server }: { server: ServerDetail }) {
  const users = useQuery({ queryKey: ["db-users", server.id], queryFn: () => get<DbUser[]>(`/servers/${server.id}/database-users`) });
  const roles = useQuery({ queryKey: ["db-roles", server.id], queryFn: () => get<DbUser[]>(`/servers/${server.id}/roles`) });
  const [onlyAdmin, setOnlyAdmin] = useState(false);
  const [hideSystem, setHideSystem] = useState(true);
  const filter = (list: DbUser[] | undefined) => list?.filter((u) => (!onlyAdmin || u.admin_reasons.length) && (!hideSystem || !u.is_system));
  const dormantDays = 90;
  const columns: ColumnDef<DbUser, unknown>[] = [
    { header: "Name", accessorKey: "name", cell: ({ row: { original: u } }) => <span className="font-medium">{u.name}{u.is_system && <Badge className="ml-2">system</Badge>}</span> },
    { header: "Administrative", id: "admin", accessorFn: (u) => u.admin_reasons.length, cell: ({ row: { original: u } }) => <AdminCell u={u} /> },
    { header: "Member of", accessorFn: (u) => u.member_of.join(", "), cell: ({ row: { original: u } }) => <span className="flex flex-wrap gap-1">{u.member_of.slice(0, 4).map((m) => <Badge key={m} tone="outline">{m}</Badge>)}{u.member_of.length > 4 && <span className="text-2xs text-muted">+{u.member_of.length - 4}</span>}</span> },
    { header: "Last login", accessorKey: "last_login_at", cell: ({ row: { original: u } }) => {
      if (!u.last_login_at) return <Tooltip content="This engine doesn't record last login time"><span className="text-muted">not tracked</span></Tooltip>;
      const days = (Date.now() - new Date(u.last_login_at).getTime()) / 86400000;
      return <span className={days > dormantDays ? "font-medium text-warn" : ""}>{relTime(u.last_login_at)}</span>;
    } },
    { header: "Password expires", accessorKey: "valid_until", cell: ({ getValue }) => getValue() ? dateTime(getValue() as string) : <span className="text-muted">never</span> },
    { header: "Connection limit", accessorKey: "connection_limit", meta: { align: "right" }, cell: ({ getValue }) => (getValue() as number) ?? "—" },
  ];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-4 text-[13px]">
        <label className="flex items-center gap-2"><input type="checkbox" checked={onlyAdmin} onChange={(e) => setOnlyAdmin(e.target.checked)} /> Only administrative accounts</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={hideSystem} onChange={(e) => setHideSystem(e.target.checked)} /> Hide built-in roles</label>
        <AdministrationHint serverId={server.id} what="Changes to accounts (create, drop, grant, revoke)" action="create_user" />
      </div>
      <Card><CardHeader title="Login accounts" description="Who can connect" /><DataTable columns={columns} data={filter(users.data)} loading={users.isLoading} error={users.error} dense /></Card>
      <Card><CardHeader title="Roles" description="Non-login roles used to group privileges" /><DataTable columns={columns.filter((c) => c.header !== "Last login" && c.header !== "Password expires")} data={filter(roles.data)} loading={roles.isLoading} error={roles.error} dense /></Card>
    </div>
  );
}

export function PrivilegesTab({ server }: { server: ServerDetail }) {
  const dbs = useQuery({ queryKey: ["dbs", server.id], queryFn: () => get<DatabaseInfo[]>(`/servers/${server.id}/databases`) });
  const [database, setDatabase] = useState("");
  const [grantee, setGrantee] = useState("");
  const [type, setType] = useState("");
  const db = database || server.default_database || dbs.data?.find((d) => !d.is_system && d.accessible)?.name || "";
  const privs = useQuery({ queryKey: ["privs", server.id, db], enabled: !!db, queryFn: () => get<Privilege[]>(`/servers/${server.id}/privileges`, { database: db }) });
  const rows = useMemo(() => (privs.data ?? []).filter((p) => (!grantee || p.grantee.toLowerCase().includes(grantee.toLowerCase())) && (!type || p.object_type === type)), [privs.data, grantee, type]);
  const types = [...new Set((privs.data ?? []).map((p) => p.object_type))];
  const publicCount = (privs.data ?? []).filter((p) => p.grantee === "PUBLIC").length;
  const columns: ColumnDef<Privilege, unknown>[] = [
    { header: "Grantee", accessorKey: "grantee", cell: ({ getValue }) => { const g = getValue() as string; return g === "PUBLIC" ? <Badge tone="warn">PUBLIC</Badge> : <span className="font-medium">{g}</span>; } },
    { header: "Privilege", accessorKey: "privilege", cell: ({ getValue }) => <span className="font-mono text-[12px]">{getValue() as string}</span> },
    { header: "Object type", accessorKey: "object_type" },
    { header: "Object", accessorKey: "object_name", cell: ({ getValue }) => <span className="font-mono text-[12px]">{getValue() as string}</span> },
    { header: "How", id: "via", accessorFn: (p) => p.via_role ? "role membership" : "direct grant", cell: ({ row: { original: p } }) => p.via_role ? <Badge tone="accent">role</Badge> : <Badge tone="outline">direct</Badge> },
    { header: "Grantor", accessorKey: "grantor" },
    { header: "Grantable", accessorKey: "grantable", cell: ({ getValue }) => getValue() ? <Badge tone="warn">with grant option</Badge> : "" },
  ];
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <Select aria-label="Database" className="w-52" value={db} onChange={(e) => setDatabase(e.target.value)}>
          {(dbs.data ?? []).filter((d) => d.accessible && !d.is_template).map((d) => <option key={d.name}>{d.name}</option>)}
        </Select>
        <Input aria-label="Grantee" className="w-56" placeholder="Who has access? Filter by grantee" value={grantee} onChange={(e) => setGrantee(e.target.value)} />
        <Select aria-label="Object type" className="w-44" value={type} onChange={(e) => setType(e.target.value)}><option value="">All object types</option>{types.map((t) => <option key={t}>{t}</option>)}</Select>
        <span className="ml-auto text-[12.5px] text-muted tabular">{rows.length} grants{publicCount ? `, ${publicCount} to PUBLIC` : ""}</span>
      </div>
      <DataTable columns={columns} data={rows} loading={privs.isLoading} error={privs.error} dense pageSize={100} />
    </Card>
  );
}
