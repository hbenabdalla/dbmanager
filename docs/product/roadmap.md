# Product roadmap

Milestone-based; no calendar dates are promised. Every item traces back to a gap in the
[gap analysis](gap-analysis.md) and forward to code and tests in the
[traceability matrix](roadmap-traceability.md). An item is **DONE** only with a working
implementation and automated tests that were run — never because a menu entry, a static endpoint
or a page of documentation exists. The task-level evidence is in the
[roadmap audit](roadmap-audit.md).

| Status | Meaning |
|---|---|
| DONE | Implemented, tested (tests executed), documented (earlier documents call this *DONE*) |
| CURRENT | The milestone being worked on |
| NEXT | The milestone that starts after the current one |
| PLANNED | Scheduled in a milestone, not started (or deliberately deferred, with the reason) |
| FUTURE | Accepted for the product, not yet scheduled in a milestone |
| BLOCKED | Implemented as far as possible; waiting for an external dependency (named) |
| NOT APPLICABLE | Out of scope for the product, with the reason |

## Where the product stands

The current version is **0.15.0** (being released; **0.14.0** is the latest published release). It contains the baseline capability groups below (listed as the
baseline, not as past releases) and milestones 0.9 – 0.15. **0.12.0** was the first public
release. The milestone of this release is described in [Current milestone](current-milestone.md).

!!! note "Roadmap reconciliation (0.14.0)"
    Before 0.14.0, every DONE status below was audited again from the code, the tests and the
    documentation. 62 claims were reopened because they were broken, partial, untested, or
    present only in the UI or only in the backend. Two of them were P0 security issues. All 62
    were fixed and verified again in the same release, as remediation sprints R1 – R7. The
    statuses below are the verified ones. The row-by-row evidence is in the
    [roadmap audit](roadmap-audit.md).

### Versioning

Milestone numbers double as version numbers. The product version is the highest milestone with
completed work: **0.15.0** adds milestone 0.15 (cloud-native deployment) to **0.14.0**, which added milestone 0.14 (reporting, with the branding and engine-identity
work) to **0.13.0**, which added milestone 0.13 (performance intelligence) to **0.12.0**, which
contained milestones 0.9 (release foundation), 0.10 (operations & alerting), 0.11 (enterprise
integration) and 0.12 (asset protection — backup & restore). Versions 0.9, 0.10 and 0.11 were
development states and were never released.

| Baseline capability group | Status |
|---|---|
| Platform core: FastAPI backend, PostgreSQL control plane, Alembic, bootstrap, settings, health, Prometheus self-metrics | DONE |
| Identity & access: local auth with lockout, sessions + CSRF, LDAP/AD and OIDC (real-validated against OpenLDAP and Keycloak in 0.13.0), group mapping, scoped RBAC, audit with CSV export | DONE |
| Seven engine adapters behind one contract with capability discovery; six real-engine validated (2026-10-18, again 2026-10-19 for 0.13.0); Db2 for z/OS partial | DONE |
| Telemetry: collectors, scheduler with leader election, circuit breaker, normalized metric catalog, VictoriaMetrics storage, retention | DONE |
| Query performance: statistics, fingerprints, history, graphical execution plans | DONE |
| Advisors: 61 performance + 55 security rules with evidence, confidence, lifecycle (each rule has a firing and a quiet test); security posture | DONE |
| Guarded administration (Monitoring Only default — also for servers registered without the option — preview → confirm → execute; EXPLAIN ANALYZE of modifying statements only in administration mode) | DONE |
| Simulation: deterministic demo fleets, isolated from real data | DONE |
| UX: command-center dashboard, topology, replication topology, wizards (setup, LDAP, OIDC, onboarding, bulk), command palette, dark mode | DONE |
| Real-engine test environment (`docker-compose.test.yml`, `scripts/integration-test`) | DONE |

---

## Milestone 0.9 — Release Foundation

*Goal: a public, installable, documented, legally usable pre-release.* Closes P0 gaps G-01 … G-07.

### Sprint 0.9.1 — Repository & governance · DONE
- **Objective:** publishable repository for `hbenabdalla/dbmanager`.
- **Features:** Git history with logical commits, README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, CHANGELOG, issue forms, PR template, `.gitignore` hardening, secret scan.
- **Technical tasks:** `git init`, `origin` remote, ignore rules for env files, volumes, coverage and results; scan tree for credentials.
- **Dependencies:** GitHub access (push). License decided by the owner: Apache-2.0.
- **Tests:** secret-scan script exits clean; CI runs on the first push.
- **Documentation:** README, contribution guide, release process.
- **Acceptance criteria:** clean clone builds and tests; no secrets in history; templates render on GitHub.
- **Definition of Done:** pushed to `main` (or exact push steps documented if no credentials), Apache-2.0 `LICENSE` committed.

### Sprint 0.9.2 — Continuous integration · DONE
- **Objective:** every pull request validated automatically.
- **Features:** workflows for backend (ruff + pytest unit/API against a PostgreSQL service), frontend (typecheck, vitest, production build), docs (`mkdocs build --strict`), Docker build, dependency audit.
- **Technical tasks:** `.github/workflows/ci-*.yml`, caching, PostgreSQL service container, no secrets required.
- **Dependencies:** 0.9.1.
- **Tests:** workflows green on `main`.
- **Documentation:** CONTRIBUTING "Testing" section.
- **Acceptance criteria:** a failing test blocks merge; real-engine suite stays a manual/dispatch workflow (no Oracle/Db2 z/OS in PR CI).
- **Definition of Done:** branch protection documented; badges in README.

### Sprint 0.9.3 — Documentation site · DONE
- **Objective:** Read the Docs–ready, audience-organized documentation.
- **Features:** MkDocs Material, `.readthedocs.yaml` (v2), pinned `docs/requirements.txt`, getting started, user/admin guides, engine pages, monitoring, security, architecture, API, development, operations, troubleshooting, product pages.
- **Dependencies:** none (RTD import needs the owner's account).
- **Tests:** `mkdocs build --strict` in CI.
- **Acceptance criteria:** a new user installs and onboards a database using only the docs.
- **Definition of Done:** strict build passes; published at [https://db-manager.readthedocs.io/en/latest/](https://db-manager.readthedocs.io/en/latest/).

### Sprint 0.9.4 — Version & product metadata · DONE
- **Objective:** users and support know exactly what is running.
- **Features:** single version source (one value in backend, `pyproject.toml` and `package.json`), build/commit and schema version via API, About page (version, engines, backend status, docs/GitHub/roadmap links, license), configurable `DBM_DOCUMENTATION_URL`, `DBM_GITHUB_URL`, `DBM_ROADMAP_URL`, Documentation entry in the app menu.
- **Dependencies:** none.
- **Tests:** API test for the version endpoint (no sensitive build details); UI test for the About page and the documentation link.
- **Acceptance criteria:** no guessed Read the Docs URL — the link was set once the project `db-manager` existed; installations can override it or hide it (`off`).
- **Definition of Done:** CHANGELOG entry, capability matrix updated.

### Sprint 0.9.5 — Upgrade & recovery verification · DONE
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

### Sprint 0.10.1 — Alert engine · DONE
- **Objective:** evaluate alert rules against normalized metrics and server health.
- **Features:** rule model (metric, scope by server/environment/tag, comparison, threshold, sustained-for duration, severity), evaluator job, de-duplicated alert instances.
- **Technical tasks:** Alembic migration, scheduler job, time-series queries, RBAC permissions (`alerts.view`, `alerts.manage`), audit events.
- **Dependencies:** time-series query API (exists).
- **Tests:** evaluator unit tests (thresholds, sustained windows, recovery), API tests incl. RBAC.
- **Documentation:** user guide "Alerts", architecture page.
- **Acceptance criteria:** a rule fires once, stays firing while the condition holds, resolves automatically.
- **Definition of Done:** works on simulated and real servers; no static/demo-only data paths.

### Sprint 0.10.2 — Notification channels (SMTP, webhook) · DONE
- **Objective:** deliver alerts outside the UI.
- **Features:** channel model, SMTP (STARTTLS/TLS), generic JSON webhook with HMAC signature, routing rules → channels, test-send, retry with backoff, delivery log.
- **Dependencies:** 0.10.1.
- **Tests:** SMTP against a local test server, webhook against a local receiver, secret masking.
- **Documentation:** administration guide "Notifications".
- **Acceptance criteria:** channel secrets encrypted and never returned; delivery failures visible.
- **Definition of Done:** audited configuration changes.

### Sprint 0.10.3 — Maintenance windows & suppression · DONE
- **Objective:** no alert noise during planned work.
- **Features:** one-off and recurring windows scoped to servers/environments/tags; suppressed alerts recorded but not notified.
- **Dependencies:** 0.10.1.
- **Tests:** window boundaries, scope matching, time zones.
- **Acceptance criteria:** alerts raised in a window are marked suppressed and notify after it ends only if still firing.
- **Definition of Done:** visible on server pages.

### Sprint 0.10.4 — Alert UX & chat presets · DONE
- **Features:** alert inbox with acknowledge/resolve/comment, dashboard and server badges, Teams and Slack webhook presets, simple re-notify escalation.
- **Dependencies:** 0.10.1–0.10.3.
- **Tests:** UI tests for the lifecycle; payload snapshot tests per preset.
- **Acceptance criteria:** acknowledging stops re-notification and is audited.

### Sprint 0.10.5 — Collector diagnostics · DONE
- **Features:** per-server diagnostic bundle (last errors, timings, capability snapshot, driver versions), redacted download.
- **Tests:** redaction tests (no credentials, no connection strings with secrets) — `tests/unit/test_redact.py`, `tests/api/test_diagnostics.py`.

---

## Milestone 0.11 — Enterprise Integration

*Closes G-18, G-19, G-33, G-36, G-38, G-40.*

| Sprint | Scope | Status |
|---|---|---|
| 0.11.1 API tokens | Scoped, hashed, expiring personal tokens; `Authorization: Bearer`; audited; UI to create/revoke (service accounts later) | DONE |
| 0.11.2 API rate limiting | Per-principal limits (`api`, live `target` queries, `expensive` operations), `429` with exact `Retry-After` and `RateLimit-*` headers, sliding-window counters in PostgreSQL shared across backend instances, UI retries after `Retry-After` | DONE |
| 0.11.3 Identity validation | OpenLDAP, Keycloak and Vault in `docker-compose.test.yml` (`identity`, `vault` targets); real bind/search/group/StartTLS tests, OIDC code flow with PKCE through Keycloak's login form | DONE — OpenLDAP 2.6 (local) and osixia/openldap 1.5.0, Keycloak 26 and Vault 1.17.6 real runs passed on 2026-10-19 |
| 0.11.4 SIEM export | Audit event stream via syslog RFC 5424 (UDP/TCP/TLS) and signed webhook batches, at-least-once cursor | DONE |
| 0.11.5 Secret managers | HashiCorp Vault provider (KV v2; token, AppRole, Kubernetes auth) | DONE |
| 0.11.6 MFA readiness | Native TOTP with recovery codes and enrollment policy; OIDC `amr`/`acr` enforcement | DONE |
| 0.11.7 Cloud secret stores | AWS Secrets Manager, Azure Key Vault, Google Secret Manager providers | PLANNED — deferred: each needs its own SDK, auth model and a real cloud account to validate; Vault covers the self-hosted case now |

Each sprint follows the same template: objective, features, technical tasks, dependencies, tests,
documentation, acceptance criteria (as above), Definition of Done = code + tests + docs +
capability matrix + changelog.

## Milestone 0.12 — Asset Protection: Backup & Restore

*Goal: KBM DBManager protects its own configuration and the databases it manages, and proves every
backup can be restored.* Details: [Backup & Restore documentation](../backup-restore/index.md).

| Sprint | Scope | Status |
|---|---|---|
| 0.12.1 Platform backup | Versioned archive of the control plane by component (organization, inventory with encrypted credential references, identity, findings state, alerting, settings, backup configuration, optional audit), optional VictoriaMetrics native export; age encryption (recipient or passphrase); manifest with schema/app version, row counts, SHA-256 per file and the master-key ids needed; storage providers local / mounted / NFS / S3-compatible; status only `SUCCESS` after read-back verification | DONE |
| 0.12.2 Platform restore | Restore wizard: manifest → integrity → compatibility (compatible / migration required / unsupported) → components → impact analysis (trial run in a rolled-back transaction, incl. cascade effects) → typed confirmation → pre-restore safety backup → restore → validation; audit merge-only; RBAC `restore.platform` | DONE |
| 0.12.3 Managed database backup | `DatabaseBackupProvider` per engine with capability discovery: PostgreSQL `pg_dump`/`pg_restore`, MySQL `mysqldump`, MariaDB `mariadb-dump`, SQL Server native `BACKUP … COPY_ONLY, CHECKSUM` / `RESTORE … WITH MOVE`, Oracle Data Pump schema export/import, Db2 LUW online `BACKUP` (restore external), Db2 for z/OS external tool required; restores only into a new database | DONE — real runs on 2026-10-19 for PostgreSQL 16, MySQL 8.4, MariaDB 11.4, SQL Server 2022, Oracle 21c and Db2 LUW 11.5 (backup); Db2 LUW restore EXTERNAL VALIDATION REQUIRED (run on the Db2 host) |
| 0.12.4 Backup automation | Policies (daily/weekly/monthly in a time zone, DST-aware), retention (keep last N / N days / daily-weekly-monthly generations, only policy-owned backups), bulk backup by servers/environments/sites/tags with per-asset progress and `PARTIAL_SUCCESS`, cancel, notifications (failed, storage unavailable, verification failed, restore failed, retention failed, no successful backup within policy), Prometheus metrics, asset protection status, Backup & Restore UI | DONE |
| 0.12.5 Disaster recovery | Catalog entries next to every backup; *Scan* a target to register backups in a new installation; DR runbook; automated backup → change → restore → validate tests in isolated environments only | DONE |
| 0.12.6 Cloud object stores | Native Azure Blob Storage and Google Cloud Storage targets (S3-compatible endpoints already work) | PLANNED |
| 0.12.7 Point-in-time recovery | WAL/binlog/log-chain management for PITR | PLANNED — needs per-engine archive management; not claimed today |

## Milestone 0.13 — Performance Intelligence · DONE (0.13.0)

*Goal: answers from the stored history — unusual behaviour per server, slower statements and plan
changes, storage forecasts, comparisons, and whether a fix helped.* Details:
[current milestone](current-milestone.md), [baselines and capacity](../performance/baselines-and-capacity.md),
[regressions and comparisons](../performance/regressions-and-comparison.md). Closes G-24, G-25, G-27,
G-28, G-29.

| Sprint | Scope | Status |
|---|---|---|
| 0.13.1 Baselines in alerts/rules | z-score alert rules against each server's own history; wizard with baseline preview | DONE |
| 0.13.2 Query regression detection | Per-fingerprint latency regressions with thresholds in settings; plan changes for Oracle and SQL Server; rule `GEN-PERF-011` | DONE — plan changes not detectable on PostgreSQL, MySQL, MariaDB, Db2 (no plan identity in their statement statistics) |
| 0.13.3 Capacity forecasting | Theil–Sen trend with 90 % band; days to full per server, database and tablespace; rule `GEN-PERF-012` | DONE — no seasonality model |
| 0.13.4 Query comparison | Period vs period and plan vs plan (saved plans) | DONE |
| 0.13.5 Recommendation validation | Before/after measurement when a recommendation is resolved; Outcomes page | DONE |

## Milestone 0.14 — Reporting · DONE (0.14.0)

*Goal: the reports people send around — security findings, query statistics, regressions,
forecasts — as CSV or PDF, on demand or e-mailed on a schedule, never showing more than the
recipient's owner may see.* Details: [reports](../user-guide/reports.md),
[current milestone](current-milestone.md). Closes G-30, G-31, G-32.

| Sprint | Scope | Status |
|---|---|---|
| 0.14.1 Report coverage | Report definitions *Security findings* (statuses, minimum severity), *Query statistics* (window), *Query regressions*, *Capacity forecast*; parameters (environment, server, engine) for every report; preview; reports grouped by area in the UI | DONE |
| 0.14.2 Scheduled reports | Daily/weekly/monthly schedules in a time zone, e-mailed as attachments through e-mail channels; runs with the owner's current access (disabled owner or lost access → failed run, nothing sent); attachment size limit; run history per channel; *Send now*; audit; RBAC `reports.schedule`; migration `0012` | DONE |
| 0.14.3 PDF export | Server-side PDF (ReportLab): logo, scope, generated-for, repeated table header, landscape for wide tables, row cap with the CSV as complete export; CSV formula-injection guard | DONE |
| 0.14.4 Branding & engine identity | KBM DBManager logo in the product, documentation, README, favicon and app icons; `DatabaseEngineIcon` with KBM DBManager's own engine marks (no vendor logos) everywhere engines are shown; optional operator-supplied licensed logos (`DBM_BRANDING_DIR`); trademark notice | DONE |

## Remediation sprints R1 – R7 · DONE (0.14.0)

Created by the roadmap reconciliation for the reopened claims. Details and evidence:
[roadmap audit](roadmap-audit.md#remediation-sprints).

| Sprint | Scope | Status |
|---|---|---|
| R1 Security | Client address behind proxies, MFA lockout, encrypted webhook URLs and headers, CSV formula injection, backup scoping, EXPLAIN ANALYZE guard, role catalog permission, report schedule ownership, Oracle pack guard in the workspace, Oracle connect-string redaction | DONE |
| R2 Engine correctness | MySQL/MariaDB database selection, Db2 database refusal, TLS *prefer*/*require*, MySQL 5.7 locks, constraints on SQL Server and Oracle, roles across SQL Server databases, PDB users from CDB$ROOT, Db2 grant catalogs, one storage contract | DONE |
| R3 Findings and alerts | No false auto-resolve, last values in rule windows, alerts outside a rule's scope resolve, suppression released after windows, Compliance counted server-side | DONE |
| R4 Backup & Restore | Cancel running platform backups, storage-unavailable and cancelled notifications, notifications of manual backups, safety backup required, protection freshness for manual policies, audited previews and target checks, tests for scopes, export and metrics | DONE |
| R5 Runtime and CI | Leader lock release, Compose restore drill required and proving credentials, management-mode default and migration `0013`, upgrade tests from published releases | DONE |
| R6 User interface | Administration texts and links, guards, error states, UI for existing APIs (pin, jobs, evaluate, OIDC, palette), paged server lists, baseline preview for every scope, engine icon guard, layout at 1024 px | DONE |
| R7 Documentation | Counts, statuses, navigation paths, screenshots recaptured from the running application | DONE |

## Milestone 0.15 — Cloud-Native Deployment · DONE (0.15.0)

*Goal: install and operate KBM DBManager on Kubernetes and OpenShift from signed images, with
replicas that coordinate safely and encrypted connections to the control-plane databases, every
artefact validated in CI.* Details: [Kubernetes and OpenShift with Helm](../deployment/kubernetes-helm.md),
[high availability](../deployment/high-availability.md), [current milestone](current-milestone.md),
[test validation](test-validation.md). Closes the capability gaps G-41 (Helm chart), G-42 (manifests validated in CI), G-44 (HA reference) and G-45 (internal TLS) of the [gap analysis](gap-analysis.md#product-capability-gaps); G-43 (operator) stays P4.

| Sprint | Scope | Status |
|---|---|---|
| 0.15.1 Container images | Release workflow: GHCR images per version, SBOM and SLSA provenance attestations, keyless cosign signatures, SPDX SBOMs on the GitHub release | DONE — runs on the `v0.15.0` tag |
| 0.15.2 Helm chart | Chart with bundled or external PostgreSQL/VictoriaMetrics, existing-Secret support, migrations on start under an advisory lock, Ingress/Route, PDB, HPA, NetworkPolicy, `helm test`, values schema; signed OCI chart | DONE |
| 0.15.3 Manifest validation | kubeconform (1.29, 1.31), restricted Pod Security and OpenShift restricted-v2 policy check, chart tests; kind install with `helm test` and leader failover in CI | DONE — kind job runs on push |
| 0.15.4 HA & TLS reference | Concurrent-start locks, instance registry (`0014`) with Deployment card and API, leader failover tests (processes and pods), control-plane PostgreSQL TLS modes, time-series CA, guides | DONE |
| 0.15.5 Dependency majors | Vite 8, plugin-react 6, Vitest 5, jest-dom 7, Node 24 LTS, nginx-unprivileged 1.31, mkdocs-material 9.7.7, Actions majors; Python 3.14, Node 26, TypeScript 7, Recharts 3 and jsdom 30 declined with reasons ([decisions](current-milestone.md#decisions)) | DONE |

## Milestone 1.0 — Production Release · NEXT

| Sprint | Scope | Status |
|---|---|---|
| 1.0.1 Scale testing | 1,000+ simulated servers: collector throughput, UI responsiveness, TSDB cardinality | NEXT |
| 1.0.2 Security review | Threat model review, dependency audit, authenticated DAST, fixes | NEXT |
| 1.0.3 Accessibility | axe in CI, WCAG 2.1 AA fixes | NEXT |
| 1.0.6 Deferred majors | Recharts 3 (with the visual review of every chart), TypeScript 7, Python 3.14 base image | PLANNED |
| 1.0.4 Upgrade & recovery drill | Upgrade from every 0.x schema; backup/restore drill | NEXT |
| 1.0.5 Release candidate | Freeze, RC builds, documentation review, `v1.0.0` tag | NEXT |

## Future

| Item | Status |
|---|---|
| Anomaly detection on baselines (beyond z-score rules) | FUTURE |
| Custom recommendation rules (declarative) | FUTURE |
| Custom dashboards | FUTURE |
| Custom read-only metric collectors | FUTURE |
| Database logs (alert/error logs) | FUTURE |
| Cloud-API database discovery | FUTURE |
| OpenTelemetry traces | FUTURE |
| Plugin framework, operator | FUTURE |
| Oracle TCPS real validation | FUTURE (needs a TCPS listener in the test environment) |
| Db2 for z/OS real validation; thread, lock and subsystem monitoring | BLOCKED (z/OS subsystem + Db2 Connect) |

## Known limitations of the current release

- The last full Docker real-engine run (2026-10-20, 0.14.0 code) passed except one MySQL TLS test bug, fixed since; 0.15 changed no adapter. The full matrix is re-run on the Docker host before the `v0.15.0` tag ([test validation](test-validation.md)).
- LDAP/AD were validated against OpenLDAP (no Active Directory server in the test environment); OIDC against Keycloak 26.
- Scheduled reports are delivered by e-mail only (no webhook/file-share delivery); PDF reports list at most 5,000 rows (the CSV has all rows); no Excel format.
- KBM DBManager shows its own engine marks, not vendor logos; licensed logos can be supplied by the operator (`DBM_BRANDING_DIR`).
- Kubernetes: the Helm chart bundles a single-instance PostgreSQL and VictoriaMetrics for evaluation only; production needs a managed or operator-run PostgreSQL. No operator and no VictoriaMetrics cluster chart. The kind install and leader failover run in GitHub Actions, not on a production-size cluster.
- Database backups: real-engine runs passed for all six engines ([validation report](real-engine-validation.md)). No point-in-time recovery; Db2 LUW restores and Oracle RMAN are external.
- Backup storage targets: local / mounted / NFS directories and S3-compatible buckets; Azure Blob and GCS natively are planned.
- Cloud secret managers (AWS, Azure, Google) are planned; HashiCorp Vault is available.
- Db2 for z/OS support is partial (BLOCKED: z/OS subsystem + Db2 Connect); Oracle TCPS has not been exercised against a real listener.
- Performance intelligence: plan changes are detected only for Oracle and SQL Server; forecasts use a robust linear trend without seasonality; per-database and per-tablespace sizes are recorded from this release on, so their forecasts appear after 7 days of history; before/after validation describes correlation in time, not proof of cause.
