# Users and groups

**Platform → Configuration → Users** (`users.manage`) manages platform accounts — the people who
sign in to DBManager, not database accounts.

* **Local users** — username, display name, email, active flag. Passwords use Argon2id; the policy
  requires 12+ characters with three character classes. *Reset password* sets a temporary password
  that must be changed at next sign-in; deactivating a user or resetting a password revokes all
  their sessions.
* **Directory users** — created automatically at first LDAP/OIDC sign-in when the provider has
  `auto_provision`; they cannot have a local password.
* **Lockout** — after `DBM_LOGIN_MAX_FAILURES` (5) failures an account is locked for
  `DBM_LOGIN_LOCKOUT_MINUTES` (15); **Unlock** clears it.
* **Multi-factor authentication** — users enroll themselves under *user menu → Security*.
  **Reset MFA** removes a local user's authenticator and recovery codes when the device is lost;
  see [Multi-factor authentication](mfa.md).
* **Delete user** — removes the account, its role assignments and API tokens (type the username to
  confirm). You cannot delete your own account. Audit records keep the username.
* **Groups** — collections of users that carry role assignments; directory groups are mapped to them.
  **Edit members** sets the members (you cannot add yourself to a group, which would let you grant
  yourself its roles); **Delete group** removes the group and its role assignments.
* **Effective permissions** — in the user dialog, **Show** lists every role the user holds, directly
  or through a group, with its scope and the resulting permissions
  (`GET /api/v1/users/{id}/effective-permissions`).

Custom roles are deleted from the role editor (**Delete role**); system roles cannot be deleted.

## Placement

**Fleet → Placement** (`inventory.manage`) creates, edits and deletes the environments, sites,
clusters and applications servers are assigned to. An item can only be deleted while no server
references it (the API answers `409`); move those servers first. Environments and clusters can also
be edited from their own pages.

Role assignments themselves are explained in [RBAC](rbac.md). The CLI can reset any password:
`docker compose exec backend python -m app.cli reset-password <username>`.
