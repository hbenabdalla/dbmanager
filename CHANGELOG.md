# Changelog

All notable changes to DB Manager are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Pre-1.0 minor versions may contain breaking changes;
they are listed under **Changed** with migration notes.

## [0.12.0] — 2026-10-19

The first public release. Its number follows the roadmap: it contains milestones 0.9 (release
foundation), 0.10 (operations & alerting), 0.11 (enterprise integration) and 0.12 (asset
protection: backup & restore). Versions 0.9, 0.10 and 0.11 were development states and were never
released. What was verified for each roadmap task is listed in `docs/product/roadmap-audit.md`.

### Added

**Backup & restore** (milestone 0.12)
- Platform → Backup & Restore: overview dashboard, backup history, policies, storage targets, an
  8-step create wizard and a restore wizard.
- Platform backups of DB Manager itself by component (organization, inventory with encrypted
  credential references, identity, findings state, alerting, settings, backup configuration,
  optional audit log and VictoriaMetrics telemetry export) in a versioned archive with a manifest
  (versions, row counts, SHA-256 per file, master-key ids needed).
- Encryption with age (`pyrage`): to an X25519 recipient, or with a passphrase protecting a
  per-backup key (`key.age`). Both are recoverable with the standard `age` CLI. The master key is
  never written into a backup.
- Storage targets: local disk, mounted volume, NFS mount (only under `DBM_BACKUP_ALLOWED_ROOTS`)
  and S3-compatible buckets.
- A backup is only `SUCCESS` after the stored copy was read back and verified. Integrity
  statuses: CREATING / VERIFYING / SUCCESS / FAILED / CORRUPTED / DELETED.
- Restore steps: manifest, integrity, compatibility (compatible / migration required /
  unsupported), component selection, impact analysis from a trial run that is rolled back
  (including cascade effects), typed confirmation, automatic pre-restore safety backup,
  post-restore validation. The audit log is merge-only.
- Managed-database backups through per-engine providers with capability discovery:
  - PostgreSQL `pg_dump` / `pg_restore`;
  - MySQL `mysqldump` and MariaDB `mariadb-dump` (`--single-transaction`, routines, triggers, events, utf8mb4);
  - SQL Server native `BACKUP DATABASE … COPY_ONLY, CHECKSUM`, `RESTORE VERIFYONLY` and `RESTORE … WITH MOVE`;
  - Oracle Data Pump schema export and import with `REMAP_SCHEMA`;
  - Db2 LUW online `BACKUP` (restore is external; the command is shown);
  - Db2 for z/OS is reported as external tool required.

  Restores only ever create a new database or schema.
- Policies (daily / weekly / monthly at a local time, DST-aware) and retention (keep last N, N
  days, daily / weekly / monthly generations; only backups the policy created). Bulk backups by
  servers, environments, sites or tags with per-asset progress, `PARTIAL_SUCCESS` and cancel.
- Notifications to policy channels: backup failed, storage unavailable, verification failed,
  restore failed, retention cleanup failed, no successful backup within policy.
- Asset protection status on server pages (Protected / At risk / Not protected).
- Disaster recovery: every backup has a catalog entry next to it, and *Scan* registers the
  backups of a target in a fresh installation.
- Prometheus metrics: `dbm_backup_jobs_total`, `dbm_backup_failures_total`,
  `dbm_backup_duration_seconds`, `dbm_backup_bytes_total`, `dbm_restore_jobs_total`,
  `dbm_restore_failures_total`, `dbm_backup_last_successful_backup_timestamp_seconds`.
- Permissions `backup.view`, `backup.create`, `backup.verify`, `backup.delete`,
  `backup.policy.manage`, `backup.target.manage`, `restore.execute`, `restore.platform`,
  `restore.database`, and a *Backup Operator* system role. Migration `0010`.
- The backend image ships `pg_dump` / `pg_restore` 17 and the MariaDB client. Docker Compose
  mounts backup and scratch volumes.

**Alerting (completed)**
- Tag scope (`key` or `key=value`) for rules and maintenance windows; metric rules on the latest
  value or avg / min / max over a time-series window.
- Alert detail with comments; alert inbox with acknowledge, resolve and comment; alert panels on
  server pages; re-notification every N minutes until acknowledged.
- Webhook presets for Slack (blocks) and Microsoft Teams (Adaptive Card); SMTP with STARTTLS or
  implicit TLS and a CA file (`DBM_SMTP_SECURITY`, `DBM_SMTP_CA_FILE`).
- Delivery retries with backoff (`DBM_NOTIFY_MAX_ATTEMPTS`, `DBM_NOTIFY_RETRY_BACKOFF_SECONDS`);
  channel routing by environment, tag and engine.
- Recurring (daily / weekly) maintenance windows in a time zone. A suppressed alert notifies
  after its window ends if it is still firing. Migration `0007`.

**Enterprise integration**
- Multi-factor authentication for local accounts: TOTP with QR enrollment, 10 single-use
  recovery codes, a two-step sign-in, an enrollment policy (`DBM_MFA_REQUIRED_FOR` = none /
  admins / all) and administrator reset. OIDC providers can require MFA through `amr` / `acr`
  claims. Migration `0008`.
- Audit forwarding to a SIEM (Platform → Audit forwarding):
  - syslog RFC 5424 over UDP, TCP (octet counting) or TLS;
  - signed JSON webhook batches;
  - category filters and an at-least-once cursor with backlog.

  Migration `0009`.
- HashiCorp Vault credential provider: KV v2, token / AppRole / Kubernetes auth, namespaces and
  a cache (`DBM_VAULT_*`). Credential forms offer environment, file and Vault sources, and the
  connection test before registration resolves these references.
- Real identity-provider tests: OpenLDAP, Keycloak and Vault targets in
  `docker-compose.test.yml` (`scripts/integration-test identity` / `vault`).

**Operations**
- Redacted per-server diagnostic bundle (Server → Settings → Download diagnostics).
- Placement page to create, edit and delete environments, sites, clusters and applications. User,
  group and role deletion, group membership and effective-permissions views. Streamed "Test
  connection" and "Scan security now" on the server settings.
- `scripts/restore-drill.sh` (backup → wipe → restore → verify) runs in the Docker CI job.
- Migration tests upgrade an existing installation with data from the first and the previous
  schema.

**Database engines**
- Adapter framework with a capability model (FULL, PARTIAL, REQUIRES_PRIVILEGE,
  REQUIRES_CONFIGURATION, REQUIRES_LICENSE, REQUIRES_EXTENSION, REQUIRES_EXTERNAL_MONITORING,
  NOT_SUPPORTED, NOT_IMPLEMENTED) and runtime discovery per server.
- Adapters for PostgreSQL (asyncpg), MySQL and MariaDB (PyMySQL), Microsoft SQL Server (pyodbc +
  ODBC Driver 18), Oracle Database (python-oracledb thin mode; Diagnostics/Tuning Pack features
  never used unless marked licensed), IBM Db2 LUW (ibm_db) and partial IBM Db2 for z/OS.
- Real-engine validation suite (`docker-compose.test.yml`, `scripts/integration-test.*`) with
  fixtures, workloads, blocking chains and least-privilege accounts; results shipped in
  `backend/app/data/real_engine_validation.json`. Validated: PostgreSQL 16.15, MySQL 8.4.11,
  MariaDB 11.4.13, SQL Server 2022 (16.0.4305.5), Oracle 21c XE (21.3), Db2 11.5.9.0 — all
  thirteen matrix capabilities.
- Database driver health (Platform → Database drivers), including "SQL Server Driver MISSING"
  with remediation when Microsoft ODBC Driver 18 is absent.

**Monitoring and performance**
- Collectors with per-component state, circuit breaker and cumulative-counter normalization.
- Time-series storage in VictoriaMetrics (in-memory backend for tests), retention and downsampling
  settings, metric catalog.
- Query statistics from each engine's own source (pg_stat_statements, performance_schema,
  sys.dm_exec_query_stats, V$SQLSTATS, MON_GET_PKG_CACHE_STMT), fingerprints and history.
- Execution plans: estimated and actual (rolled back), graph / tree / raw vendor plan views.
- Sessions, locks and blocking chains; replication status; configuration; storage.

**Alerting**
- Alert rules on normalized metrics (operator, threshold, sustained-for duration), server health
  and collector failures, scoped to all servers, an environment or a server.
- Alert lifecycle pending → firing → acknowledged → resolved with auto-resolve and
  de-duplication; maintenance windows that suppress notifications; Operations → Alerts, alert
  rules, maintenance windows, alert count in the top bar and on the dashboard.
- Notification channels: webhook (JSON, optional HMAC-SHA256 signature, SSRF guard) and SMTP
  e-mail; write-only encrypted secrets, test send and delivery log.

**Advice and security**
- Rule engine with common and engine-specific performance and security rules based only on
  evidence returned by the adapters.
- Security posture scoring, privileged-account review, findings lifecycle.

**Administration**
- Guarded administrative actions (create/drop user, role, grant/revoke, create index, …) with
  statement preview, typed confirmation, production safeguards and audit. Servers are Monitoring
  Only until administration is explicitly enabled.
- Guarded SQL workspace with statement classification and read-only execution.

**Platform**
- Local, LDAP/Active Directory and OIDC authentication; group-to-role mapping.
- Scoped RBAC (platform, environment, server, …) and a complete audit trail with CSV export.
- Simulation: on-request, clearly labelled simulated fleets for demonstration.
- Resumable Platform Setup Wizard, post-setup checklist, 9-step database onboarding wizard with a
  streamed staged connection test, bulk onboarding (grid / CSV / JSON, no passwords in files),
  LDAP and OIDC wizards with staged tests and presets.
- UI: grouped navigation, command palette and global "Add" menu, command-center dashboard with
  filters, fleet and replication topology, server metric cards, security posture dashboard,
  dark mode, reduced-motion support.
- Docker Compose deployment (frontend, backend, PostgreSQL, VictoriaMetrics) and Kubernetes /
  OpenShift manifests.

**Operations**
- `GET /api/v1/version`, `GET /api/v1/about` and Platform → About: application, backend,
  frontend, build, commit and schema versions, engine validation status, links (configurable
  `DBM_DOCUMENTATION_URL`, `DBM_GITHUB_URL`, `DBM_ROADMAP_URL`), license.
- `scripts/backup.sh` / `scripts/restore.sh` for Docker Compose installations.
- Automated migration tests (step-by-step upgrade, downgrade, model parity) and a backup/restore
  round-trip test of encrypted credentials.

**API**
- Personal API tokens: hashed and expiring (`DBM_API_TOKEN_MAX_DAYS`), optionally narrowed to a
  subset of the owner's permissions and to client CIDRs, sent as `Authorization: Bearer`;
  interactive-only endpoints (token, password, user/role/IdP management) refuse them; failures
  rate-limited and audited.

**Rate limiting**
- Per-principal API limits — every request (`DBM_API_RATE_PER_USER_PER_MINUTE`, 600), live
  queries against managed databases (`DBM_API_RATE_TARGET_PER_MINUTE`, 120) and expensive
  operations such as SQL execution, plans, administration, exports and tests
  (`DBM_API_RATE_EXPENSIVE_PER_MINUTE`, 30). A session counts against its user, each API token
  against itself.
- Sliding-window counters stored in PostgreSQL (`DBM_RATE_LIMIT_BACKEND=database`, migration
  `0006`) so the limits hold across backend replicas; login and failed-token limits use them too.
- `429 rate_limited` with an exact `Retry-After`, `RateLimit-*` headers and the policy in the error
  details; the UI retries a refused data load once after `Retry-After`; `dbm_rate_limited_total`
  metric.

**Project**
- Licensed under the Apache License 2.0 (`LICENSE`); SPDX identifier in `backend/pyproject.toml`,
  `frontend/package.json` and on the About page.
- Documentation published on Read the Docs (https://db-manager.readthedocs.io/en/latest/); the Help menu and About page link to it
  by default (`DBM_DOCUMENTATION_URL` overrides it, `off` hides it).
- Documentation site for Read the Docs (MkDocs Material), product capability matrix, gap
  analysis and roadmap, contribution guide, security policy, GitHub issue forms and CI workflows.

### Security
- Credentials encrypted at rest with an envelope key (`DBM_MASTER_KEY`); never returned by the
  API, logged or audited.
- Credential references to environment variables cannot point at the platform's own
  configuration (`DBM_*`, `PG*`, cloud credential variables).
- Platform backups keep secrets in their envelope-encrypted form. Backup passphrases and age
  identities are only sealed in the job for the duration of a run, then removed; they are never
  logged or audited. Downloads need the same permission as a restore.
- `BACKUP` / `RESTORE` statements in the SQL workspace are classified as privileged / destructive.

### Changed
- Required CI checks run on every pull request (no path filters), with stable job names for branch
  protection.
- The roadmap is renumbered: performance intelligence is now 0.13, reporting 0.14 and cloud-native
  0.15. Milestone 0.12 is asset protection.
- A wrong password on the MFA endpoints answers `422` instead of `401`, so a typo no longer signs
  the user out of the web UI.
- `DBM_SMTP_STARTTLS` is superseded by `DBM_SMTP_SECURITY`.
- Supported versions in the documentation now match the adapters: PostgreSQL 12–17, MySQL 8.0 /
  8.4 / 9.x, MariaDB 10.5+.

### Fixed
- LDAP group lookups failed against real directories when the group attribute wasn't in the
  schema cache (`memberOf`). Found by the first OpenLDAP run.
- A MySQL / MariaDB dump whose definitions are qualified with the source schema name is refused
  for a cross-name restore. Restoring it would otherwise have changed the source database.
- Syslog over TLS closes the connection with `close_notify`.
- The About page no longer says "license not yet selected"; the reports page no longer offers
  formats the API doesn't serve.
- Engine matrix corrections: MariaDB has 17 engine-specific rules (not 19), and the read-only
  transaction support per engine is corrected.

### Deprecated
- `DBM_SMTP_STARTTLS` (use `DBM_SMTP_SECURITY`). It is still honoured when `DBM_SMTP_SECURITY` is
  not set.

### Known limitations
- Real-engine runs exist for:
  - backups on PostgreSQL 16 and MariaDB 10.11;
  - LDAP on OpenLDAP 2.6.

  The SQL Server, Oracle, Db2 LUW and MySQL backup providers and the Keycloak / Vault tests are
  implemented, but their integration tests have not run yet (they need the Docker integration
  environment).
- No point-in-time recovery. Db2 LUW restores and Oracle RMAN are external tools. Db2 for z/OS
  backups are not run by DB Manager.
- Azure Blob Storage and Google Cloud Storage targets are planned; S3-compatible endpoints work.
- Cloud secret managers (AWS, Azure, Google) are planned.
- Reports are CSV only and not schedulable. The Kubernetes manifests aren't validated in CI and
  there's no Helm chart.
- Db2 for z/OS is partial. Oracle TCPS has not been exercised against a real listener.

