# Users and groups

**Platform → Configuration → Users** (`users.manage`) manages platform accounts — the people who
sign in to DB Manager, not database accounts.

* **Local users** — username, display name, email, active flag. Passwords use Argon2id; the policy
  requires 12+ characters with three character classes. *Reset password* sets a temporary password
  that must be changed at next sign-in; deactivating a user or resetting a password revokes all
  their sessions.
* **Directory users** — created automatically at first LDAP/OIDC sign-in when the provider has
  `auto_provision`; they cannot have a local password.
* **Lockout** — after `DBM_LOGIN_MAX_FAILURES` (5) failures an account is locked for
  `DBM_LOGIN_LOCKOUT_MINUTES` (15); **Unlock** clears it.
* **Groups** — collections of users that carry role assignments; directory groups are mapped to them.
* **Effective permissions** — shows the union of a user's direct and group role assignments per scope.

Role assignments themselves are explained in [RBAC](rbac.md). The CLI can reset any password:
`docker compose exec backend python -m app.cli reset-password <username>`.
