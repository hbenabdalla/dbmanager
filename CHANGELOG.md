# Changelog

All notable changes to DBManager are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Pre-1.0 minor versions may contain breaking changes;
they are listed under **Changed** with migration notes.

## [0.14.0] — 2026-10-19

Milestone 0.14 — Reporting — and the DBManager branding and database-engine identity workstream.
Upgrading from 0.13.0 applies migration `0012` automatically; no configuration change is needed.

### Added

**Reporting** (milestone 0.14)
- **New reports** (0.14.1): *Security findings* (statuses, minimum severity, affected object,
  first/last seen, resolution, remediation), *Query statistics* (window 1–744 h, calls, total /
  mean / max time, share of the server's time, rows, CPU, reads, masked text), *Query regressions*
  (with plan changes on Oracle and SQL Server) and *Capacity forecast* (per server, database or
  tablespace).
- **Report parameters** for every report — environment, server, engine, plus `window_hours`,
  `statuses`, `min_severity` and `scope` where they apply — and `GET /api/v1/reports/{key}/preview`
  (first 50 rows and the total). The catalog lists each report's `params`.
- **Server-side PDF** (0.14.3) for every report (`GET /api/v1/reports/{key}.pdf`), rendered with
  ReportLab: logo, title, generated-for, time, scope and row count, header repeated on every page,
  landscape for wide tables, at most 5,000 rows, data-classification footer.
- **Scheduled reports** (0.14.2): **Reports → Schedules** with a wizard (report and parameters,
  PDF/CSV, daily / weekly / monthly in a time zone, e-mail channels, owner), *Send now*, run
  history per channel. API `/api/v1/report-schedules` (CRUD, `/{id}/run`, `/{id}/runs`), permission
  `reports.schedule` (platform), job `report_scheduler` (every minute on the scheduler leader).
  Each run uses the owner's current roles; a disabled or deleted owner, or one without the report's
  permission, fails the run without sending. Audit events `report.delivered`,
  `report_schedule.create` / `.update` / `.delete`. Setting `DBM_REPORT_MAX_ATTACHMENT_MB`
  (default 10).
- Migration `0012`: `report_schedules` (included in platform backups, component *alerting*) and
  `report_runs` (delivery history, not backed up).
- Reports page: reports grouped by area as cards, generate dialog with parameters, preview and
  downloads, *Schedule…* shortcut.

**Branding** (sprint 0.14.4)
- The DBManager logo on the sign-in page, setup wizard, sidebar (expanded and collapsed), About
  page, loading and first-run screens, PDF reports, README and documentation site; favicon
  (`.ico`, 16/32 px), Apple touch icon, 192/512 px app icons and `site.webmanifest`.
- `DatabaseEngineIcon`: DBManager's own engine marks (PostgreSQL, MySQL, MariaDB, SQL Server,
  Oracle, Db2 LUW, Db2 for z/OS, unknown fallback) with sizes, accessible names, dark-theme outline,
  spelling aliases (`sqlserver`, `db2-zos`, …) and detected versions.
- Optional operator-supplied licensed engine logos: `DBM_BRANDING_DIR`,
  `GET /api/v1/branding/engine-logos` and `/branding/engine-logos/{engine}` (whitelisted file names,
  512 KiB limit, no symbolic links, `nosniff`, sandboxing CSP).
- `docs/development/brand-assets.md` (asset inventory, rules, trademark notice).

### Changed
- `GET /api/v1/reports` no longer returns `planned_formats`; `formats` is `["csv", "pdf"]` for every
  report. PDF downloads return `200` instead of `422`.
- The report scope printed in files and e-mails uses environment and server names instead of ids.
- Product name *DBManager* everywhere in current text (UI, API title, e-mail subjects, Teams
  cards, documentation). The default `DBM_MFA_ISSUER` is now `DBManager`; existing authenticator
  entries keep working.
- Engine colours were adjusted so that the white monogram has at least 4.5:1 contrast.
- `scripts/backup.sh`, `restore.sh` and `restore-drill.sh` read `.env` with a parser instead of
  sourcing it (`scripts/lib/dotenv.sh`).

### Fixed
- The *Docker Compose* CI workflow failed in the restore drill since 0.11 (exit 127): `.env` was
  sourced as shell code and the unquoted value `DB Manager` ran `Manager` as a command. The 0.13.0
  release checklist wrongly listed this workflow as passing; corrected.

### Security
- CSV exports neutralise values that spreadsheets evaluate as formulas (`=`, `+`, `-`, `@`, tab,
  carriage return), numbers excepted.
- Scheduled reports never exceed their owner's current access; choosing another owner requires
  `users.manage`; only e-mail channels are accepted.
- Static files under `/assets/` are served with `X-Content-Type-Options: nosniff` (a location-level
  header had replaced the server-level one); the web manifest has an explicit media type.
- Licensed-logo files are served only for fixed names with `nosniff` and
  `Content-Security-Policy: default-src 'none'; sandbox`.

### Database Compatibility
- No adapter changes. Reports read collected data for every engine; plan-change columns are filled
  for Oracle and SQL Server only (no plan identity in the other engines' statement statistics).
- PostgreSQL 16 adapter and backup suites re-run locally for this release.

### User Interface
- New Reports page and Schedules tab with the shared wizard, history dialog and confirmations.
- Engine marks in every list, filter and header that shows an engine; the dashboard engine
  distribution keeps click-to-filter and shows the active filter with its mark.
- Engines listed with their marks on the sign-in page and the first-run screen; the first-run text
  no longer says other engines can only be inventoried.

### Branding
- See *Added → Branding*. Vendor logos are not bundled: their owners' trademark policies require
  permission. The logo files are derived from `docs/assets/brand/dbmanager-logo-source.png` by
  cropping and scaling only.

### Documentation
- Reports guide rewritten (catalog, parameters, PDF, scheduled reports, owner access);
  `docs/development/brand-assets.md`; release notes 0.14.0; roadmap (0.14 DONE, 0.15 NEXT),
  current milestone, capability matrix, gap analysis (G-30, G-31, G-32 closed), traceability, roadmap
  audit (0.13.0 baseline findings and milestone 0.14), configuration reference
  (`DBM_REPORT_MAX_ATTACHMENT_MB`, `DBM_BRANDING_DIR`, `DBM_MFA_ISSUER`); documentation site logo and
  favicon; README logo and trademark notice.

### Known Limitations
- Scheduled reports are e-mailed only; PDF reports list at most 5,000 rows; no Excel format.
- The full Docker real-engine matrix and the Docker image build run on the owner's Docker host
  (`scripts/integration-test`); the release-preparation environment cannot pull container images.
- Unchanged: Active Directory not validated (OpenLDAP only), Oracle TCPS not exercised, TLS
  certificate verification of target databases not real-validated, Db2 for z/OS external
  validation, no point-in-time recovery, Kubernetes manifests not validated in CI.

## [0.13.0] — 2026-10-19

Milestone 0.13 — Performance Intelligence — and the fixes found by the first full real-engine
run in the Docker integration environment. Upgrading from 0.12.0 applies migration `0011`
automatically; no configuration change is needed.

### Added

**Performance intelligence** (milestone 0.13)
- **z-score alert rules** (0.13.1): *Evaluate → Deviation from baseline* compares the latest value
  or a window average with the server's own baseline (mean and standard deviation over
  `baseline_days`, at least 60 samples, the recent window excluded, cached for 10 minutes). The
  rule wizard previews the baseline for a server-scoped rule. API: `aggregation: "zscore"`,
  `baseline_days`.
- **Query regression detection** (0.13.2): **Queries → Regressions** and
  `GET /api/v1/servers/{id}/query-regressions` — statements whose mean latency in a recent window
  is well above their baseline (relative and absolute thresholds, minimum executions; platform
  settings `performance.regression_*`, `performance.baseline_days`). Advisor rule `GEN-PERF-011`.
- **Plan-change detection** for Oracle (dominant `PLAN_HASH_VALUE` from `V$SQLSTATS`) and SQL Server
  (distinct `query_plan_hash` per `query_hash`); new statement metrics `db_query_plans` and
  `db_query_plan_id`.
- **Capacity forecasting** (0.13.3): **Monitoring → Capacity forecast**,
  `GET /api/v1/capacity/forecast` and `/servers/{id}/capacity-forecast` — Theil–Sen growth with a
  90 % confidence interval and forecast band, days to full (earliest / likely / latest) per server,
  database and tablespace. The hourly discovery now records `db_database_size_bytes`,
  `db_tablespace_used_bytes` and `db_tablespace_max_bytes`. Advisor rule `GEN-PERF-012`
  (setting `performance.forecast_warning_days`).
- **Query comparison** (0.13.4): **Queries → Compare periods** (`GET /servers/{id}/query-compare`,
  presets or custom periods) and **Queries → Compare plans** (`GET /plans/compare`) for plans saved
  with *Save plan as* on Execution plans (`POST /servers/{id}/explain` with `save_label`;
  `GET /servers/{id}/plans`, `GET`/`DELETE /plans/{id}`).
- **Recommendation validation** (0.13.5): resolving a recommendation measures its triggering
  metric (or the statement's latency) before and after; job `validate_recommendations`; outcome
  in the recommendation dialog and on **Optimization → Outcomes**
  (`GET /recommendations/validation-summary`; setting `performance.validation_window_hours`).
- Migration `0011`: `alert_rules.baseline_days`, `recommendations.validation`,
  `query_plan_snapshots` (included in platform backups, component *findings*).

**Documentation**
- `docs/product/real-engine-validation.md` (per-engine validation report),
  `docs/product/current-milestone.md`, `docs/database-permissions/` (accounts the real-engine
  suite uses), `docs/troubleshooting/real-engine-findings.md`,
  `docs/performance/regressions-and-comparison.md`.

### Changed
- Roadmap status labels are now DONE / CURRENT / NEXT / PLANNED / FUTURE / BLOCKED (DONE replaces
  VERIFIED COMPLETE).
- Simulation: cumulative statement counters integrate latency over time (hourly noise no longer
  looks like a regression), per-database size history and capacity history are generated, and
  database sizes are consistent with the server's data volume.
- Integration tests: the Db2 backup runs as a SYSMAINT backup operator (`SYSMAINT_GROUP`), not the
  instance owner; `scripts/integration-test` adds `IT_DB2_BK_PASSWORD` to an existing `.env.test`.
- The upgrade test now seeds users, role assignments, credentials, findings, backup targets and
  policies and settings at the previous release's schema and checks that every row survives.

### Fixed
Defects found by the real-engine run of 0.12.0 (container images listed under *Database
compatibility*):
- PostgreSQL restore failed on servers older than the client tools (`pg_restore` 17 →
  PostgreSQL 16: `transaction_timeout`); such restores now go through a rendered script without the
  unknown settings, run by `psql` with `ON_ERROR_STOP`.
- MySQL restore of dumps with triggers or routines failed with error 1419 when binary logging is
  on (MySQL 8 default); it is now checked before anything is created and refused with the fix.
- MySQL / MariaDB restores no longer need `SUPER` / `SET_USER_ID` for objects of other definers:
  `DEFINER` clauses are removed, so restored objects belong to the restoring account.
- SQL Server, Oracle and Db2 backup errors surfaced as adapter errors instead of backup errors.
- SQL Server backup metadata is read with `RESTORE HEADERONLY` (a `db_backupoperator` cannot read
  `msdb.dbo.backupset`).
- Db2 restore command: logs are extracted with `LOGTARGET` and used as `OVERFLOW LOG PATH`.
- LDAP: groups found both through `memberOf` and the group search were listed twice.
- Integration environment: Db2 setup no longer hangs after an interrupted run; Db2 test container
  uses archive logging; OpenLDAP container ACLs let the service account search; the runner shows
  progress, enforces a per-test timeout and writes a JUnit report; compose commands documented
  with `--env-file .env.test`.

### Security
- Real-engine validation uses least-privilege accounts only (no `root`, `sa`/`sysadmin`, `SYS`,
  `SYSTEM`, `DBA` or `SYSADM` for monitoring, backup or restore); the accounts are documented in
  `docs/database-permissions/`.
- New endpoints follow the existing RBAC: regressions and period comparison `query.view` (statement
  text masked without `query.view_text`), forecasts `performance.view`, saved plans
  `query.explain` (deletion by the author or `performance.tune`). Saving and deleting a plan is
  audited (`query.plan_saved`, `query.plan_deleted`); statements are stored only when a user saves
  a plan explicitly.
- Oracle: no optional packs are used (no AWR, ASH, ADDM, SQL Monitor, SQL Tuning Advisor).

### Database compatibility
Real-engine run on 2026-10-19 21:34 UTC of this release (Docker Desktop / WSL 2, official images), see
`docs/product/real-engine-validation.md`:

| Engine (image) | Version | Result |
|---|---|---|
| PostgreSQL (`postgres:16-alpine`) | 16.15 | PASS (backup/restore included) |
| MySQL (`mysql:8.4`) | 8.4.11 | PASS (backup/restore with binary logging on included) |
| MariaDB (`mariadb:11.4`) | 11.4.13 | PASS (backup/restore included) |
| SQL Server (`mssql/server:2022-latest`) | 16.0.4305.5 | PASS (backup/restore included) |
| Oracle Database XE (`database/express:21.3.0-xe`) | 21.3.0.0.0 | PASS (Data Pump included); TCPS not tested |
| Db2 LUW (`db2_community/db2:11.5.9.0`) | 11.5.9.0 | PASS (backup as a SYSMAINT operator); restore is an external step |
| OpenLDAP (`osixia/openldap:1.5.0`), Keycloak 26.0.7, Vault 1.17.6 | — | PASS |
| Db2 for z/OS | — | EXTERNAL VALIDATION REQUIRED |

Drivers: asyncpg 0.32.0, PyMySQL 1.2.3, pyodbc 5.3.0 with Microsoft ODBC Driver 18.7,
python-oracledb 26.0.1 (thin), ibm_db 3.3.0. 86 tests: 85 passed, 1 skipped (Oracle TCPS). An earlier run the same day (84 passed, 1 failed) found the MySQL error 1419 fixed in this release.

### Known limitations
- Plan changes are detected only for Oracle and SQL Server; the other engines' statement
  statistics carry no plan identity.
- Forecasts use a robust linear trend without seasonality. Database and tablespace sizes are
  recorded from this release on; their forecasts appear after 7 days of history. SQL Server
  filegroups are not forecast; PostgreSQL and MySQL tablespaces have no reported maximum.
- Before/after validation shows correlation in time, not proof of cause; configuration and
  security findings are not measurable from the monitoring history.
- Oracle TCPS has not been exercised (the XE image has no TCPS listener). Replication was validated
  on standalone servers only (no replica topology in the test environment).
- Db2 LUW restores and Oracle RMAN remain external steps; no point-in-time recovery.
- Db2 for z/OS: EXTERNAL VALIDATION REQUIRED (z/OS subsystem with Db2 Connect).

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

