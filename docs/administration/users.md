# Users and groups

**Platform → Configuration → Users** manages platform accounts — the people who sign in to
DBManager, not database accounts. The page is shown with `users.manage` or `rbac.manage`; creating,
changing and deleting users and groups needs `users.manage`, assigning roles needs `rbac.manage`.
It has the tabs **Users** and **Groups**; select a user to open the user dialog.

* **Local users** — **Create user** asks for username, display name, email and a temporary
  password (changed by the user at first sign-in); the dialog shows the active flag. Passwords use Argon2id; the policy
  requires 12+ characters with three character classes. **Reset password** sets a temporary password
  that must be changed at next sign-in; deactivating a user or resetting a password revokes all
  their sessions.
* **Directory users** — created automatically at first LDAP/OIDC sign-in when the provider has
  `auto_provision`; they cannot have a local password.
* **Lockout** — after `DBM_LOGIN_MAX_FAILURES` (5) failures an account is locked for
  `DBM_LOGIN_LOCKOUT_MINUTES` (15); **Unlock account** clears it.
* **Multi-factor authentication** — users enroll themselves under *user menu → Security*.
  **Reset MFA** removes a local user's authenticator and recovery codes when the device is lost;
  see [Multi-factor authentication](mfa.md).
* **Delete user** — removes the account, its role assignments and API tokens (type the username to
  confirm). You cannot delete your own account. Audit records keep the username.
* **Groups** — collections of users that carry role assignments; directory groups are mapped to them.
  **Edit members** sets the members (you cannot add yourself to a group, which would let you grant
  yourself its roles); **Delete group** removes the group and its role assignments.
* **Role assignments** — in the user dialog (and for a group on the Groups tab), **Assign role**
  picks a **Role** and a **Scope** (*Entire platform*, *Environment*, *Site*, *Cluster*, *Server*,
  *Database* or *Schema*). You cannot change your own assignments.
* **API tokens** — the user dialog lists the user's tokens and can revoke them (`users.manage`).
* **Effective permissions** — in the user dialog, **Show** lists every role the user holds, directly
  or through a group, with its scope and the resulting permissions
  (`GET /api/v1/users/{id}/effective-permissions`).

**Platform → Configuration → Roles** shows the roles against the permission catalog and lets
`roles.manage` create, edit and delete custom roles (**New role**, then **Create role**; **Save role**;
**Delete role**); system roles cannot be edited or deleted. The role list
(`GET /api/v1/roles`) is administration data: it needs `roles.manage`, `users.manage` or
`rbac.manage` (at any scope).

## Placement

**Overview → Fleet → Placement** (`inventory.manage`) creates, edits and deletes the environments, sites,
clusters and applications servers are assigned to. An item can only be deleted while no server
references it (the API answers `409`); move those servers first. Environments and clusters can also
be edited from their own pages.

Role assignments themselves are explained in [RBAC](rbac.md). The CLI can reset any password:
`docker compose exec backend python -m app.cli reset-password <username>`.
