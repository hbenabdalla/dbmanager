# Administration

Platform administration lives under **Platform** in the sidebar, in the sections **Setup**,
**Backup & Restore** and **Configuration**. Most pages need a platform-scope permission such as
`settings.manage`, `users.manage`, `idp.manage` or `collectors.view`.

| Task | Page in the application | Documentation |
|---|---|---|
| First-run setup | Platform → Setup → Setup wizard, Setup checklist | [Setup wizards](../user-guide/onboarding/index.md) |
| Configuration (environment variables and editable settings) | Platform → Configuration → Settings | this page, below |
| Platform users and groups | Platform → Configuration → Users | [Users and groups](users.md) |
| Roles, scopes and permissions | Platform → Configuration → Roles | [RBAC](rbac.md) |
| Directory sign-in | Platform → Configuration → Identity providers, Identity wizard, OIDC, LDAP | [Identity providers](identity-providers.md), [LDAP / AD](ldap.md), [OIDC](oidc.md) |
| Multi-factor authentication, API tokens | user menu → Security, API tokens | [MFA](mfa.md), [API tokens](api-tokens.md) |
| Database credentials and secret sources | Platform → Configuration → Credentials | [Secret providers](secret-providers.md) |
| Collection and diagnostics | Platform → Configuration → Collectors | [Diagnostics](diagnostics.md) |
| Metric storage and retention | Platform → Configuration → Monitoring storage | [Monitoring storage](monitoring-storage.md), [Retention](retention.md) |
| Database drivers and engine support | Platform → Configuration → Database drivers, Engine support | [Database drivers](drivers.md), [Engine support](engine-support.md) |
| Alert notifications | Platform → Configuration → Notification channels | [Notifications](notifications.md) |
| Audit log and SIEM | Security → Audit logs; Platform → Configuration → Audit forwarding | [Audit](audit.md), [Audit forwarding](siem.md) |
| Demo data | Platform → Configuration → Simulation | [Simulation](simulation.md) |
| Version and build | Platform → Configuration → About | [About](about.md) |
| Backup and restore of KBM DBManager and of databases | Platform → Backup & Restore | [Backup & Restore](../backup-restore/index.md) |
| Upgrade | — | [Upgrade](../operations/upgrade.md) |
| Platform security settings | — | [Security settings](security.md), [Rate limiting](rate-limiting.md) |

## Configuration

KBM DBManager is configured in two places:

* **Environment variables** (`DBM_*`, read at start-up; in Docker Compose from `.env`). The full
  list is in [Deployment → environment variables](../deployment/index.md#environment-variables).
  Secrets (`DBM_MASTER_KEY`, `POSTGRES_PASSWORD`, `DBM_TIMESERIES_PASSWORD`,
  `DBM_BOOTSTRAP_ADMIN_PASSWORD`, SMTP or OIDC secrets) belong in `.env` or a secret manager.
* **Editable settings** under **Platform → Configuration → Settings** (`GET/PUT /api/v1/settings`,
  stored in the control plane and audited as `settings.update`). The page needs `settings.manage`
  at platform scope; without it the page says so and shows nothing. **Save settings** stores the
  changes.

| Setting | Default | Meaning |
|---|---|---|
| `ui.banner_text` | empty | banner shown to all users (for example a maintenance notice) |
| `workspace.allow_writes_in_production` | `true` | allow data-modifying workspace statements on production servers |
| `workspace.allow_explain_analyze_in_production` | `true` | allow EXPLAIN ANALYZE on production servers |
| `collection.default_interval_s` | `60` | default collection interval for servers registered afterwards |
| `security.dormant_account_days` | `90` | days without login before a database account counts as dormant |
| `monitoring.retention_days` | `90` | retention policy of metric history (enforced by the time-series database, see [Retention](retention.md)) |
| `performance.regression_threshold_pct`, `performance.regression_min_calls`, `performance.regression_min_delta_ms`, `performance.regression_recent_hours`, `performance.baseline_days` | `50`, `20`, `5`, `1`, `7` | query regression detection — see [Regressions](../performance/regressions-and-comparison.md) |
| `performance.forecast_warning_days` | `30` | capacity forecast: raise a finding when storage is expected to fill within this many days |
| `performance.validation_window_hours` | `24` | hours measured before and after a recommendation is resolved |
| `backup.allow_restore_without_safety_backup` | `false` | allow a platform restore without first taking a safety backup of the current configuration |
| `backup.notification_channels` | empty | notification channel names (comma separated) for events of backups no policy owns: manual backups, their verification and restores |

The page also shows, read-only, the **Multi-factor authentication policy** (`mfa_required_for`,
`mfa_issuer` from `DBM_MFA_REQUIRED_FOR` and `DBM_MFA_ISSUER`) and the **Deployment (environment
variables)** values: environment, process role, public base URL, session lifetimes, lockout,
collector concurrency, time-series backend and retention, workspace limits, master key id and
cookie settings — never secrets.
