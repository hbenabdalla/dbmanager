# Users and privileges

**Database users** and **Roles & privileges** (Operations → Administration, or the server tabs)
show the engine's accounts and roles with their administrative reasons, role memberships and grants
(system, database, schema, object and column level where the engine has them).

## Guarded administration

On servers registered as **Administration enabled**, the **Administration** tab offers actions the
adapter supports for that engine: create/drop database, schema, user/login, role; grant and
revoke; create index.

1. Fill in the parameters (validated identifiers, privilege allow-lists per engine).
2. **Preview** shows the exact vendor statements (passwords masked).
3. **Execute** requires the action's permission on the server, the administration credential, a
   typed confirmation for the risk class (the server name for destructive or production changes)
   and a reason for destructive production changes.

Monitoring-only servers refuse every change (`409 monitoring_only`). Engine limits: Db2 never
creates users (authentication is external), Oracle offers no PDB lifecycle, Db2 for z/OS offers no
actions. Passwords never appear in SQL text where the engine allows it and are never audited.
