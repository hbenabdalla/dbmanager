import { ServerScopedPage } from "../shared/server-scoped";
import { UsersTab, PrivilegesTab } from "../server/tabs/access";
import { DatabasesTab } from "../server/tabs/databases";
import { ObjectBrowser, SchemasTable } from "../server/tabs/objects";
import { useNavigate } from "react-router-dom";

const crumbs = (l: string) => [{ label: "Administration" }, { label: l }];

export function AdminDatabasesPage() {
  const navigate = useNavigate();
  return <ServerScopedPage title="Databases" description="Databases on the selected server with size, sessions and cache efficiency. Create and drop through the SQL workspace, which requires confirmation." crumbs={crumbs("Databases")} cap="databases">
    {(s) => <DatabasesTab server={s} onOpen={(db) => navigate(`/admin/objects?server=${s.id}&db=${encodeURIComponent(db)}`)} />}
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
  return <ServerScopedPage title="Database users" description="Login accounts and roles, with administrative attributes highlighted." crumbs={crumbs("Database users")} perm="user.view" cap="users">{(s) => <UsersTab server={s} />}</ServerScopedPage>;
}
export function AdminPrivilegesPage() {
  return <ServerScopedPage title="Roles & privileges" description="Who has access to what, and whether it comes from a direct grant or a role." crumbs={crumbs("Roles & privileges")} perm="privilege.view" cap="privileges">{(s) => <PrivilegesTab server={s} />}</ServerScopedPage>;
}
