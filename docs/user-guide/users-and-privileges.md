# Users and privileges

**Operations → Administration → Database users** and **Roles & privileges** (or the server's
**Users** and **Privileges** tabs) show the engine's accounts and roles with their administrative
reasons, role memberships and grants (system, database, schema, object and column level where the
engine has them). Pick the server in the **Server** selector. These pages are read-only; changes
are made on the server's Administration tab, which they link to.

**+ Add → Database user** (top bar, permission `user.manage`) opens *Create a database user*: pick
the **Server** (servers registered with administration enabled are listed first) and select
**Continue to Administration**. The server's Administration tab opens with the *create user* action
selected.

## Guarded administration

The server's **Administration** tab offers the actions the adapter supports for that engine:
create/drop database, schema, user/login, role; grant and revoke; create index. Actions you have no
permission for are listed as *(no permission)*.

1. Under **Guarded change**, choose the **Action** and fill in its fields (validated identifiers,
   privilege allow-lists per engine; list fields are comma separated).
2. **Preview statements** shows the exact vendor statements under **Review**, with the risk class
   (passwords masked).
3. **Execute** opens a confirmation dialog. It requires the action's permission on the server, the
   administration credential, a typed confirmation where the risk class asks for it (*Type the
   server name to confirm* for destructive or production changes) and a reason, which is required
   for destructive changes on production servers.

On a *Monitoring only* server the tab shows *Changes can be previewed but not executed on this
server* with the reasons, and the API refuses execution (`409 monitoring_only`). Switch the
server's management mode under **Settings** to allow changes. Engine limits: Db2 never creates
users (authentication is external), Oracle offers no PDB lifecycle, Db2 for z/OS offers no
actions. Passwords never appear in SQL text where the engine allows it and are never audited.

Every executed action is audited with its statements (passwords masked).
