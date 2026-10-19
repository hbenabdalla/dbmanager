# Product roadmap

Milestone-based; no calendar dates are promised. Every item traces back to a gap in the
[gap analysis](gap-analysis.md) and forward to code and tests in the
[traceability matrix](roadmap-traceability.md). An item is **VERIFIED COMPLETE** only with a working
implementation and automated tests that were run — never because a menu entry, a static endpoint
or a page of documentation exists. The task-level evidence is in the
[roadmap audit](roadmap-audit.md).

| Status | Meaning |
|---|---|
| VERIFIED COMPLETE | Implemented, tested (tests executed), documented |
| PLANNED | Scheduled in a milestone, not started (or deliberately deferred, with the reason) |
| BLOCKED | Implemented as far as possible; waiting for an external dependency (named) |
| NOT APPLICABLE | Out of scope for the product, with the reason |

## Where the product stands

The current version is **0.12.0**, the first public release. It contains the baseline capability
groups below (listed as the baseline, not as past releases) and milestones 0.9 – 0.12.

### Versioning

Milestone numbers double as version numbers. The product version is the highest milestone with
completed work: **0.12.0** contains milestones 0.9 (release foundation), 0.10 (operations &
alerting), 0.11 (enterprise integration) and 0.12 (asset protection — backup & restore). Versions
0.9, 0.10 and 0.11 were development states and were never released.

| Baseline capability group | Status |
|---|---|
| Platform core: FastAPI backend, PostgreSQL control plane, Alembic, bootstrap, settings, health, Prometheus self-metrics | VERIFIED COMPLETE |
| Identity & access: local auth with lockout, sessions + CSRF, LDAP/AD and OIDC (not real-validated), group mapping, scoped RBAC, audit with CSV export | VERIFIED COMPLETE |
| Seven engine adapters behind one contract with capability discovery; six real-engine validated (2026-10-18); Db2 for z/OS partial | VERIFIED COMPLETE |
| Telemetry: collectors, scheduler with leader election, circuit breaker, normalized metric catalog, VictoriaMetrics storage, retention | VERIFIED COMPLETE |
| Query performance: statistics, fingerprints, history, graphical execution plans | VERIFIED COMPLETE |
| Advisors: 59 performance + 55 security rules with evidence, confidence, lifecycle; security posture | VERIFIED COMPLETE |
| Guarded administration (Monitoring Only default, preview → confirm → execute) | VERIFIED COMPLETE |
| Simulation: deterministic demo fleets, isolated from real data | VERIFIED COMPLETE |
| UX: command-center dashboard, topology, replication topology, wizards (setup, LDAP, OIDC, onboarding, bulk), command palette, dark mode | VERIFIED COMPLETE |
| Real-engine test environment (`docker-compose.test.yml`, `scripts/integration-test`) | VERIFIED COMPLETE |

---

## Milestone 0.9 — Release Foundation

*Goal: a public, installable, documented, legally usable pre-release.* Closes P0 gaps G-01 … G-07.

### Sprint 0.9.1 — Repository & governance · VERIFIED COMPLETE
- **Objective:** publishable repository for `hbenabdalla/dbmanager`.
- **Features:** Git history with logical commits, README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, CHANGELOG, issue forms, PR template, `.gitignore` hardening, secret scan.
- **Technical tasks:** `git init`, `origin` remote, ignore rules for env files, volumes, coverage and results; scan tree for credentials.
- **Dependencies:** GitHub access (push). License decided by the owner: Apache-2.0.
- **Tests:** secret-scan script exits clean; CI runs on the first push.
- **Documentation:** README, contribution guide, release process.
- **Acceptance criteria:** clean clone builds and tests; no secrets in history; templates render on GitHub.
- **Definition of Done:** pushed to `main` (or exact push steps documented if no credentials), Apache-2.0 `LICENSE` committed.

### Sprint 0.9.2 — Continuous integration · VERIFIED COMPLETE
- **Objective:** every pull request validated automatically.
- **Features:** workflows for backend (ruff + pytest unit/API against a PostgreSQL service), frontend (typecheck, vitest, production build), docs (`mkdocs build --strict`), Docker build, dependency audit.
- **Technical tasks:** `.github/workflows/ci-*.yml`, caching, PostgreSQL service container, no secrets required.
- **Dependencies:** 0.9.1.
- **Tests:** workflows green on `main`.
- **Documentation:** CONTRIBUTING "Testing" section.
- **Acceptance criteria:** a failing test blocks merge; real-engine suite stays a manual/dispatch workflow (no Oracle/Db2 z/OS in PR CI).
- **Definition of Done:** branch protection documented; badges in README.

### Sprint 0.9.3 — Documentation site · VERIFIED COMPLETE
- **Objective:** Read the Docs–ready, audience-organized documentation.
- **Features:** MkDocs Material, `.readthedocs.yaml` (v2), pinned `docs/requirements.txt`, getting started, user/admin guides, engine pages, monitoring, security, architecture, API, development, operations, troubleshooting, product pages.
- **Dependencies:** none (RTD import needs the owner's account).
- **Tests:** `mkdocs build --strict` in CI.
- **Acceptance criteria:** a new user installs and onboards a database using only the docs.
- **Definition of Done:** strict build passes; published at [https://db-manager.readthedocs.io/en/latest/](https://db-manager.readthedocs.io/en/latest/).

### Sprint 0.9.4 — Version & product metadata · VERIFIED COMPLETE
- **Objective:** users and support know exactly what is running.
- **Features:** single version source (one value in backend, `pyproject.toml` and `package.json`), build/commit and schema version via API, About page (version, engines, backend status, docs/GitHub/roadmap links, license), configurable `DBM_DOCUMENTATION_URL`, `DBM_GITHUB_URL`, `DBM_ROADMAP_URL`, Documentation entry in the app menu.
- **Dependencies:** none.
- **Tests:** API test for the version endpoint (no sensitive build details); UI test for the About page and the documentation link.
- **Acceptance criteria:** no guessed Read the Docs URL — the link was set once the project `db-manager` existed; installations can override it or hide it (`off`).
- **Definition of Done:** CHANGELOG entry, capability matrix updated.

### Sprint 0.9.5 — Upgrade & recovery verification · VERIFIED COMPLETE
- **Objective:** upgrades and restores are proven, not assumed.
- **Features:** CI job applying all migrations from an empty database and from the previous release's schema; `scripts/backup.sh` / `restore.sh` (PostgreSQL dump, VictoriaMetrics snapshot, master key reminder); restore drill test.
- **Dependencies:** 0.9.2.
- **Tests:** migration upgrade/downgrade test; scripted backup → wipe → restore → login + inventory intact.
- **Documentation:** operations guide: upgrade, backup, restore.
- **Acceptance criteria:** restore reproduces users, servers, encrypted credentials (with the same master key).
- **Definition of Done:** runbook verified end to end in CI or in the test environment.

---

## Milestone 0.10 — Operations & Alerting

*Goal: problems reach people without someone watching the dashboard.* Closes G-10 … G-17, G-23.

### Sprint 0.10.1 — Alert engine · VERIFIED COMPLETE
- **Objective:** evaluate alert rules against normalized metrics and server health.
- **Features:** rule model (metric, scope by server/environment/tag, comparison, threshold, sustained-for duration, severity), evaluator job, de-duplicated alert instances.
- **Technical tasks:** Alembic migration, scheduler job, time-series queries, RBAC permissions (`alerts.view`, `alerts.manage`), audit events.
- **Dependencies:** time-series query API (exists).
- **Tests:** evaluator unit tests (thresholds, sustained windows, recovery), API tests incl. RBAC.
- **Documentation:** user guide "Alerts", architecture page.
- **Acceptance criteria:** a rule fires once, stays firing while the condition holds, resolves automatically.
- **Definition of Done:** works on simulated and real servers; no static/demo-only data paths.

### Sprint 0.10.2 — Notification channels (SMTP, webhook) · VERIFIED COMPLETE
- **Objective:** deliver alerts outside the UI.
- **Features:** channel model, SMTP (STARTTLS/TLS), generic JSON webhook with HMAC signature, routing rules → channels, test-send, retry with backoff, delivery log.
- **Dependencies:** 0.10.1.
- **Tests:** SMTP against a local test server, webhook against a local receiver, secret masking.
- **Documentation:** administration guide "Notifications".
- **Acceptance criteria:** channel secrets encrypted and never returned; delivery failures visible.
- **Definition of Done:** audited configuration changes.

### Sprint 0.10.3 — Maintenance windows & suppression · VERIFIED COMPLETE
- **Objective:** no alert noise during planned work.
- **Features:** one-off and recurring windows scoped to servers/environments/tags; suppressed alerts recorded but not notified.
- **Dependencies:** 0.10.1.
- **Tests:** window boundaries, scope matching, time zones.
- **Acceptance criteria:** alerts raised in a window are marked suppressed and notify after it ends only if still firing.
- **Definition of Done:** visible on server pages.

### Sprint 0.10.4 — Alert UX & chat presets · VERIFIED COMPLETE
- **Features:** alert inbox with acknowledge/resolve/comment, dashboard and server badges, Teams and Slack webhook presets, simple re-notify escalation.
- **Dependencies:** 0.10.1–0.10.3.
- **Tests:** UI tests for the lifecycle; payload snapshot tests per preset.
- **Acceptance criteria:** acknowledging stops re-notification and is audited.

### Sprint 0.10.5 — Collector diagnostics · VERIFIED COMPLETE
- **Features:** per-server diagnostic bundle (last errors, timings, capability snapshot, driver versions), redacted download.
- **Tests:** redaction tests (no credentials, no connection strings with secrets) — `tests/unit/test_redact.py`, `tests/api/test_diagnostics.py`.

---

## Milestone 0.11 — Enterprise Integration

*Closes G-18, G-19, G-33, G-36, G-38, G-40.*

| Sprint | Scope | Status |
|---|---|---|
| 0.11.1 API tokens | Scoped, hashed, expiring personal tokens; `Authorization: Bearer`; audited; UI to create/revoke (service accounts later) | VERIFIED COMPLETE |
| 0.11.2 API rate limiting | Per-principal limits (`api`, live `target` queries, `expensive` operations), `429` with exact `Retry-After` and `RateLimit-*` headers, sliding-window counters in PostgreSQL shared across backend instances, UI retries after `Retry-After` | VERIFIED COMPLETE |
| 0.11.3 Identity validation | OpenLDAP, Keycloak and Vault in `docker-compose.test.yml` (`identity`, `vault` targets); real bind/search/group/StartTLS tests, OIDC code flow with PKCE through Keycloak's login form | VERIFIED COMPLETE for OpenLDAP (run against OpenLDAP 2.6); Keycloak and Vault runs BLOCKED (need a Docker host: `scripts/integration-test identity` / `vault`) |
| 0.11.4 SIEM export | Audit event stream via syslog RFC 5424 (UDP/TCP/TLS) and signed webhook batches, at-least-once cursor | VERIFIED COMPLETE |
| 0.11.5 Secret managers | HashiCorp Vault provider (KV v2; token, AppRole, Kubernetes auth) | VERIFIED COMPLETE |
| 0.11.6 MFA readiness | Native TOTP with recovery codes and enrollment policy; OIDC `amr`/`acr` enforcement | VERIFIED COMPLETE |
| 0.11.7 Cloud secret stores | AWS Secrets Manager, Azure Key Vault, Google Secret Manager providers | PLANNED — deferred: each needs its own SDK, auth model and a real cloud account to validate; Vault covers the self-hosted case now |

Each sprint follows the same template: objective, features, technical tasks, dependencies, tests,
documentation, acceptance criteria (as above), Definition of Done = code + tests + docs +
capability matrix + changelog.

## Milestone 0.12 — Asset Protection: Backup & Restore

*Goal: DB Manager protects its own configuration and the databases it manages, and proves every
backup can be restored.* Details: [Backup & Restore documentation](../backup-restore/index.md).

| Sprint | Scope | Status |
|---|---|---|
| 0.12.1 Platform backup | Versioned archive of the control plane by component (organization, inventory with encrypted credential references, identity, findings state, alerting, settings, backup configuration, optional audit), optional VictoriaMetrics native export; age encryption (recipient or passphrase); manifest with schema/app version, row counts, SHA-256 per file and the master-key ids needed; storage providers local / mounted / NFS / S3-compatible; status only `SUCCESS` after read-back verification | VERIFIED COMPLETE |
| 0.12.2 Platform restore | Restore wizard: manifest → integrity → compatibility (compatible / migration required / unsupported) → components → impact analysis (trial run in a rolled-back transaction, incl. cascade effects) → typed confirmation → pre-restore safety backup → restore → validation; audit merge-only; RBAC `restore.platform` | VERIFIED COMPLETE |
| 0.12.3 Managed database backup | `DatabaseBackupProvider` per engine with capability discovery: PostgreSQL `pg_dump`/`pg_restore`, MySQL `mysqldump`, MariaDB `mariadb-dump`, SQL Server native `BACKUP … COPY_ONLY, CHECKSUM` / `RESTORE … WITH MOVE`, Oracle Data Pump schema export/import, Db2 LUW online `BACKUP` (restore external), Db2 for z/OS external tool required; restores only into a new database | VERIFIED COMPLETE for PostgreSQL 16 and MariaDB 10.11 (run); SQL Server, Oracle, Db2 LUW and MySQL real runs BLOCKED (need the Docker integration environment: `tests/integration/test_backup_engines.py`) |
| 0.12.4 Backup automation | Policies (daily/weekly/monthly in a time zone, DST-aware), retention (keep last N / N days / daily-weekly-monthly generations, only policy-owned backups), bulk backup by servers/environments/sites/tags with per-asset progress and `PARTIAL_SUCCESS`, cancel, notifications (failed, storage unavailable, verification failed, restore failed, retention failed, no successful backup within policy), Prometheus metrics, asset protection status, Backup & Restore UI | VERIFIED COMPLETE |
| 0.12.5 Disaster recovery | Catalog entries next to every backup; *Scan* a target to register backups in a new installation; DR runbook; automated backup → change → restore → validate tests in isolated environments only | VERIFIED COMPLETE |
| 0.12.6 Cloud object stores | Native Azure Blob Storage and Google Cloud Storage targets (S3-compatible endpoints already work) | PLANNED |
| 0.12.7 Point-in-time recovery | WAL/binlog/log-chain management for PITR | PLANNED — needs per-engine archive management; not claimed today |

## Milestone 0.13 — Performance Intelligence

| Sprint | Scope | Status |
|---|---|---|
| 0.13.1 Baselines in alerts/rules | Baseline-relative alert conditions (z-score) | PLANNED |
| 0.13.2 Query regression detection | Per-fingerprint latency and plan-change detection, findings | PLANNED |
| 0.13.3 Capacity forecasting | Robust trend with confidence bands, days-to-full per tablespace/database | PLANNED |
| 0.13.4 Query comparison | Period-vs-period and plan-vs-plan comparison views | PLANNED |
| 0.13.5 Recommendation validation | Before/after measurement when a finding is resolved | PLANNED |

## Milestone 0.14 — Reporting

| Sprint | Scope | Status |
|---|---|---|
| 0.14.1 Report coverage | Security findings and query-statistics report definitions | PLANNED |
| 0.14.2 Scheduled reports | Schedules delivered by SMTP (requires 0.10.2) | PLANNED |
| 0.14.3 PDF export | Server-side PDF rendering of reports | PLANNED |

## Milestone 0.15 — Cloud-Native Deployment

| Sprint | Scope | Status |
|---|---|---|
| 0.15.1 Container images | Published, versioned, signed images; SBOM | PLANNED |
| 0.15.2 Helm chart | Chart with external PostgreSQL/VictoriaMetrics options, migrations hook | PLANNED |
| 0.15.3 Manifest validation | kubeconform + kind smoke test in CI; OpenShift restricted-v2 check | PLANNED |
| 0.15.4 HA & TLS reference | Multi-replica API, worker leader failover test, TLS termination and internal TLS guide | PLANNED |

## Milestone 1.0 — Production Release

| Sprint | Scope | Status |
|---|---|---|
| 1.0.1 Scale testing | 1,000+ simulated servers: collector throughput, UI responsiveness, TSDB cardinality | PLANNED |
| 1.0.2 Security review | Threat model review, dependency audit, authenticated DAST, fixes | PLANNED |
| 1.0.3 Accessibility | axe in CI, WCAG 2.1 AA fixes | PLANNED |
| 1.0.4 Upgrade & recovery drill | Upgrade from every 0.x schema; backup/restore drill | PLANNED |
| 1.0.5 Release candidate | Freeze, RC builds, documentation review, `v1.0.0` tag | PLANNED |

## Future

| Item | Status |
|---|---|
| Anomaly detection on baselines | PLANNED |
| Custom recommendation rules (declarative) | PLANNED |
| Custom dashboards | PLANNED |
| Custom read-only metric collectors | PLANNED |
| Database logs (alert/error logs) | PLANNED |
| Cloud-API database discovery | PLANNED |
| OpenTelemetry traces | PLANNED |
| Plugin framework, operator | PLANNED |
| Oracle TCPS real validation | PLANNED (needs a TCPS listener in the test environment) |
| Db2 for z/OS real validation; thread, lock and subsystem monitoring | BLOCKED (z/OS subsystem + Db2 Connect) |

## Known limitations of the current release

- LDAP/AD were validated against OpenLDAP only; Keycloak (OIDC) and Vault real runs need the Docker integration environment.
- Reports export CSV only and cannot be scheduled (milestone 0.14).
- Kubernetes/OpenShift manifests are provided but not validated in CI; no Helm chart (milestone 0.15).
- Database backups: real-engine runs exist for PostgreSQL and MariaDB; SQL Server, Oracle, Db2 LUW and MySQL providers are implemented with integration tests that have not been run yet. No point-in-time recovery; Db2 LUW restores and Oracle RMAN are external.
- Backup storage targets: local / mounted / NFS directories and S3-compatible buckets; Azure Blob and GCS natively are planned.
- Cloud secret managers (AWS, Azure, Google) are planned; HashiCorp Vault is available.
- Db2 for z/OS support is partial (BLOCKED: z/OS subsystem + Db2 Connect); Oracle TCPS has not been exercised against a real listener.
