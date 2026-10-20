# Give a colleague scoped access

**Goal:** a colleague can work on exactly the servers they are responsible for — for example
*DBA Administrator* on the *Development* environment, or *Backup Operator* on two servers — and
sees nothing else.

## Before you start

* `users.manage` (platform) to create the account or a group; `rbac.manage` at a scope that
  **contains** the target scope to assign roles. You must yourself hold every permission of the
  role at that scope ("you cannot give what you don't have"), and you cannot assign roles to
  yourself or to a group you belong to.
* Decide the **role** and the **scope**. Scopes: *Entire platform*, *Environment*, *Site*,
  *Cluster*, *Server*, *Database*, *Schema*. Platform-only permissions (users, roles, settings,
  audit, alert rules, report schedules, backup policies and targets, platform restore …) only
  work in an assignment at *Entire platform*. See the [role table](../../administration/rbac.md#system-roles).

## Steps

1. Open **Platform → Configuration → Users**.
2. If the colleague signs in with LDAP or OIDC, they appear after their first sign-in. For a local
   account select **Create user** (or **+ Add → Platform user**): **Username**, **Display name**,
   **Email** and a **Temporary password**, which they change at first sign-in. Select
   **Create user**.
3. Select the user in the list. The user dialog opens.
4. Under **Role assignments**, select **Assign role**.
5. Choose the **Role**, then the **Scope**:
    * *Environment*, *Site* or *Cluster* — pick it in the list;
    * *Server* — pick the **Server**;
    * *Database* / *Schema* — pick the **Server** and type the **Database** (and **Schema**).
6. Select **Assign role**. Repeat for further assignments (for example *Log Viewer* on another
   server).
7. Check the result: **Effective permissions → Show** lists every role the user holds, directly or
   through groups, with its scope and the resulting permissions.

For a team, do this once on a group instead: **Groups** tab → enter a **New group name** → **Add**,
select the group, **Edit members**, then **Assign role** on the group. With LDAP or OIDC, map the
directory group to this platform group on the identity provider, so membership follows the
directory ([Identity providers](../../administration/identity-providers.md#group-mapping)).

## What you should see

* The colleague's user menu (top right) lists the roles and scopes under **Access**.
* They see only the servers in their scope: fleet lists, dashboards, reports, alerts, findings,
  backups and backup jobs are filtered; pages and buttons whose permission they lack are hidden.
* The assignment is in the audit log, including denied attempts and the missing permissions.

## If it does not work

* **Assign role** fails with a missing-permission error: you do not hold `rbac.manage`, or one of
  the role's permissions, at that scope. Ask a Platform Administrator.
* The colleague sees nothing after an LDAP/OIDC sign-in: no group mapping and no direct
  assignment yet ([Identity problems](../../troubleshooting/identity.md)).
* A permission seems to have no effect: it is platform-only and the assignment is narrower.

Reference: [RBAC](../../administration/rbac.md), [Users and groups](../../administration/users.md).
