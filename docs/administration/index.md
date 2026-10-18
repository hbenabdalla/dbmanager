# Administration

Platform administration lives under **Platform** in the sidebar (Setup and Configuration groups).
Most pages need a platform-scope permission such as `settings.manage`, `users.manage`, `idp.manage`
or `collectors.view`.

| Task | Page |
|---|---|
| Configuration (environment variables and editable settings) | this page, below |
| Platform users and groups | [Users and groups](users.md) |
| Roles, scopes and permissions | [RBAC](rbac.md) |
| Directory sign-in | [Identity providers](identity-providers.md), [LDAP / AD](ldap.md), [OIDC](oidc.md) |
| Metric storage and retention | [Monitoring storage](monitoring-storage.md), [Retention](retention.md) |
| Database drivers and engine support | [Database drivers](drivers.md), [Engine support](engine-support.md) |
| Demo data | [Simulation](simulation.md) |
| Platform security settings | [Security settings](security.md) |
| Audit log | [Audit](audit.md) |
| Backup, restore, upgrade | [Backup and restore](../operations/backup-restore.md), [Upgrade](../operations/upgrade.md) |
| Health and diagnostics | [Diagnostics](diagnostics.md) |

## Configuration

DB Manager is configured in two places:

* **Environment variables** (`DBM_*`, read at start-up; in Docker Compose from `.env`). The full
  list is in [Deployment → environment variables](../deployment/index.md#environment-variables-backend).
  Secrets (`DBM_MASTER_KEY`, `POSTGRES_PASSWORD`, `DBM_TIMESERIES_PASSWORD`,
  `DBM_BOOTSTRAP_ADMIN_PASSWORD`, SMTP or OIDC secrets) belong in `.env` or a secret manager.
* **Editable settings** under **Platform → Configuration → Settings** (`GET/PUT /api/v1/settings`,
  stored in the control plane and audited), for example the UI banner text, production safeguards
  (disable writes / EXPLAIN ANALYZE on production) and the monitoring retention policy. The same
  page lists the read-only runtime values (environment, process role, session lifetimes, collector
  concurrency, time-series backend, key id …) without secrets.
