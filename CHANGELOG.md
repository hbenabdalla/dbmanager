# Changelog

All notable changes to DB Manager are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Pre-1.0 minor versions may contain breaking changes;
they are listed under **Changed** with migration notes.

No version has been tagged yet. Everything below is the content of the first public pre-release
(`0.9.0`), which will be tagged once the release checklist (`docs/product/release-checklist.md`)
passes.

## [Unreleased] — 0.9.0

### Added

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
