import { ServerScopedPage } from "../shared/server-scoped";
import { UsersTab, PrivilegesTab } from "../server/tabs/access";
import { DatabasesTab } from "../server/tabs/databases";
import { ObjectBrowser, SchemasTable } from "../server/tabs/objects";
import { useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useAllServers } from "@/api/hooks";
import { PageHeader } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Field, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { AdministrationHint, adminTabPath } from "../shared/admin-link";

const crumbs = (l: string) => [{ label: "Administration" }, { label: l }];

export function AdminDatabasesPage() {
  const navigate = useNavigate();
  return <ServerScopedPage title="Databases" description="Databases on the selected server with size, sessions and cache efficiency. Creating and dropping objects is done with the guarded actions on the server's Administration tab (preview, confirmation, audit)." crumbs={crumbs("Databases")} cap="databases">
    {(s) => <div className="space-y-3">
      <p className="text-[13px]"><AdministrationHint serverId={s.id} what="Creating and dropping schemas, users, roles, grants and indexes" /></p>
      <DatabasesTab server={s} onOpen={(db) => navigate(`/admin/objects?server=${s.id}&db=${encodeURIComponent(db)}`)} />
    </div>}
  </ServerScopedPage>;
}
export function AdminSchemasPage() {
  return <ServerScopedPage title="Schemas" description="Schemas with object counts and size." crumbs={crumbs("Schemas")} cap="schemas">{(s) => <SchemasTable server={s} />}</ServerScopedPage>;
}
export function AdminObjectsPage() {
  const db = new URLSearchParams(window.location.search).get("db") ?? undefined;
  return <ServerScopedPage title="Objects" description="Tables, views, indexes, sequences, routines, triggers and constraints." crumbs={crumbs("Objects")} cap="objects">{(s) => <ObjectBrowser server={s} initialDatabase={db} />}</ServerScopedPage>;
}
export function AdminUsersPage() {
  const [params] = useSearchParams();
  // "+ Add → Database user": continue on the chosen server's Administration tab with create_user preselected
  if (params.get("create") === "1") return <CreateDatabaseUser />;
  return <ServerScopedPage title="Database users" description="Login accounts and roles, with administrative attributes highlighted." crumbs={crumbs("Database users")} perm="user.view" cap="users">{(s) => <UsersTab server={s} />}</ServerScopedPage>;
}
export function AdminPrivilegesPage() {
  return <ServerScopedPage title="Roles & privileges" description="Who has access to what, and whether it comes from a direct grant or a role." crumbs={crumbs("Roles & privileges")} perm="privilege.view" cap="privileges">{(s) => <PrivilegesTab server={s} />}</ServerScopedPage>;
}

/** Pick the server for a new database account, then hand over to its Administration tab. */
function CreateDatabaseUser() {
  const [params] = useSearchParams();
  const servers = useAllServers();
  const list = (servers.data?.items ?? []).filter((s) => s.implementation !== "planned");
  // servers registered with administration enabled first
  const sorted = [...list].sort((a, b) => Number(b.management_mode === "administration") - Number(a.management_mode === "administration"));
  const [picked, setPicked] = useState("");
  const direct = params.get("server");
  if (direct) return <Navigate to={adminTabPath(direct, "create_user")} replace />;
  const id = picked || sorted[0]?.id || "";
  return (
    <div>
      <PageHeader title="Create a database user" crumbs={crumbs("Database users")}
        description="Database accounts are created with the guarded create_user action on the server's Administration tab: the statements are previewed, confirmed and audited." />
      <Card>
        {servers.isLoading ? <SkeletonRows rows={3} /> : servers.error ? <ErrorState error={servers.error} onRetry={() => servers.refetch()} /> : !sorted.length ? (
          <EmptyState title="No servers available">Register a server with administration enabled first.</EmptyState>
        ) : (
          <CardBody className="flex flex-wrap items-end gap-3">
            <Field label="Server" className="w-80">
              <Select value={id} onChange={(e) => setPicked(e.target.value)}>
                {sorted.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.engine_label}{s.management_mode === "administration" ? ", administration enabled" : ", monitoring only"})</option>)}
              </Select>
            </Field>
            <Button variant="primary" asChild><Link to={adminTabPath(id, "create_user")}>Continue to Administration</Link></Button>
          </CardBody>
        )}
      </Card>
    </div>
  );
}
