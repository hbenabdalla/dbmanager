# Capability matrix

Evidence-based inventory of what KBM DBManager does **today**, produced by inspecting the source tree
(backend `app/`, frontend `src/`, migrations, deployment files, tests). Engine-level detail is in
the [engine capability matrix](engine-matrix.md); what is missing and why is in the
[gap analysis](gap-analysis.md).

| Status | Meaning |
|---|---|
| **COMPLETE** | Implemented end to end (API + UI where relevant), covered by automated tests |
| **PARTIAL** | Works, but a documented part of the capability is missing |
| **IMPLEMENTED / NOT REAL-VALIDATED** | Implemented and unit/API-tested, never exercised against the real external system |
| **PLANNED** | On the [roadmap](roadmap.md), not implemented |
| **MISSING** | Not implemented and not yet scheduled in detail |
| **BLOCKED BY EXTERNAL DEPENDENCY** | Cannot be completed or validated without something outside the repository |
| **NOT APPLICABLE** | Deliberately out of scope |

Paths are relative to the repository root (`backend/app/...`, `frontend/src/...`).

> **Reconciled for 0.14.0.** Every row was checked again against the code, the tests and the
> documentation ([roadmap audit](roadmap-audit.md)). Rows whose claim was not true were fixed in
> remediation sprints R1 – R7, and their evidence now names the tests that prove them. Test
> counts at 0.14.0:
>
> - backend: 839 (unit, API and migration);
> - frontend: 180;
> - real-engine runs in this audit: PostgreSQL 16 (13), MariaDB 10.11 (15) and VictoriaMetrics (5);
> - the other engines: the last Docker run, see the [real-engine validation report](real-engine-validation.md).
>
> **Updated for 0.15.0** after a clean validation ([test validation](test-validation.md)):
> backend 869 (684 unit, 164 API, 8 migration, 13 Helm chart), frontend 183, local real-engine
> runs 35 passed (PostgreSQL 16, MariaDB 10.11 with TLS, VictoriaMetrics, OpenLDAP StartTLS).

## Platform

| Capability | Status | Evidence |
|---|---|---|
| Installation (Docker Compose) | COMPLETE | `docker-compose.yml` (frontend, backend, PostgreSQL, VictoriaMetrics), `scripts/init-env.sh` / `.ps1`, `.env.example` |
| Bootstrap (first admin, system roles, environments) | COMPLETE | `backend/app/seed/bootstrap.py`, `python -m app.cli bootstrap`, forced password change (`must_change_password`) |
| Platform setup wizard (resumable) | COMPLETE | `backend/app/api/v1/setup.py`, `frontend/src/features/setup/`, `backend/tests/api/test_onboarding.py` |
| Configuration | COMPLETE | `backend/app/core/config.py` (`DBM_*` env), runtime settings `GET/PUT /api/v1/settings` (`backend/app/services/app_settings.py`) |
| Health / readiness | COMPLETE | `GET /healthz`, `GET /readyz` with per-component status (`backend/app/api/ops.py`), tests in `backend/tests/api/test_metrics.py` |
| Upgrade (schema migrations) | COMPLETE | Alembic `0001–0013`, applied on start (`DBM_RUN_MIGRATIONS_ON_START`) or `python -m app.cli migrate`; `backend/tests/migrations/test_migrations.py` upgrades step by step, downgrades to base, re-upgrades, checks head schema = models, and upgrades an installation with data from the first schema and from the published releases 0.12.0 (`0010`) and 0.13.0 (`0011`) |
| Backup | COMPLETE | In-app **Backup & Restore** (`backend/app/backup/`): platform archives by component, age encryption, local / mounted / NFS / S3-compatible targets, policies, retention, verification (`tests/api/test_backup.py`, `tests/unit/test_backup_units.py`); stack-level `scripts/backup.sh` (pg_dump + VictoriaMetrics snapshot) with `scripts/restore-drill.sh` in Docker CI |
| Restore | COMPLETE | Restore wizard with integrity, compatibility and impact analysis (rolled-back trial), pre-restore backup, typed confirmation; `scripts/restore.sh` for the whole stack; credentials decrypt with the same master key (tested) |
| Logging | COMPLETE | Structured JSON logging `backend/app/core/logging.py`, request IDs in `backend/app/core/middleware.py`, secrets masked (`clean_message`) |
| Observability (self-metrics) | COMPLETE | Prometheus `GET /metrics` (`backend/app/core/metrics.py`: HTTP, collectors, scheduler leader, pipeline counters) |
| Diagnostics | COMPLETE | Collectors page, monitoring-storage page, driver health and a redacted per-server diagnostic bundle (`GET /servers/{id}/diagnostics`, `app/services/diagnostics.py`, `tests/api/test_diagnostics.py`, `tests/unit/test_redact.py`) |
| Version / build metadata | COMPLETE | `0.15.0` in `backend/app/__init__.py`, `pyproject.toml`, `frontend/package.json`; build args `DBM_BUILD_VERSION/DBM_GIT_COMMIT/DBM_BUILD_DATE`; `GET /api/v1/version` (exact keys tested), `GET /api/v1/about`; Platform → About |

## Identity

| Capability | Status | Evidence |
|---|---|---|
| Local authentication | COMPLETE | `POST /api/v1/auth/login`, Argon2 hashing `backend/app/core/security.py`, lockout (`login_max_failures`, `login_lockout_minutes`) enforced on the password and the TOTP step, `tests/api/test_auth.py`, `tests/api/test_mfa.py` |
| LDAP | COMPLETE (real-validated: OpenLDAP 2.6 local and osixia/openldap 1.5.0) | `backend/app/services/ldap_auth.py`, wizard; `tests/integration/test_identity.py` (service bind, search, password bind, groups, StartTLS, injection) run against OpenLDAP |
| Active Directory | IMPLEMENTED / NOT REAL-VALIDATED | Same LDAP path (`sAMAccountName`/`memberOf` configurable); no AD test environment |
| OIDC | COMPLETE (real-validated: Keycloak 26.0.7) | `backend/app/services/oidc.py` (PKCE, state sealing, claims mapping, MFA `amr`/`acr` checks); `test_identity.py::test_oidc_code_flow_with_pkce_against_keycloak` passed in the Docker run of 2026-10-19 |
| SSO (via OIDC) | COMPLETE (real-validated through Keycloak) | `GET /auth/oidc/{id}/start` + `/callback`; SAML is not supported |
| Session management | COMPLETE | Server-side sessions `user_sessions` table, TTL + idle timeout (`session_ttl_minutes`, `session_idle_timeout_minutes`), HttpOnly cookies + CSRF token |
| Password policy | PARTIAL | Fixed policy: minimum length + 3 of 4 classes (`password_policy_errors`); not configurable, no history/expiry |
| MFA | COMPLETE | Native TOTP with recovery codes, enrollment policy `DBM_MFA_REQUIRED_FOR`, administrator reset (`app/services/mfa.py`, `tests/api/test_mfa.py`); OIDC `require_mfa` with `amr`/`acr` |
| Group mapping | COMPLETE | `GroupMapping` model, `POST /identity-providers/{id}/mappings`, sync in `backend/app/services/identity_sync.py` |

## Authorization

| Capability | Status | Evidence |
|---|---|---|
| RBAC (roles, permissions, assignments) | COMPLETE | `backend/app/domain/permissions.py`, `backend/app/services/authz.py`, `/api/v1/roles` (needs a role or user management permission), `/role-assignments`, `tests/api/test_rbac.py`, `tests/unit/test_authz.py` |
| Server / database / schema scope | COMPLETE | `backend/app/domain/scopes.py` (platform ⊃ environment ⊃ site ⊃ cluster ⊃ server ⊃ database ⊃ schema) |
| Environment / site scope | COMPLETE | Attribute scopes in `scopes.py`, anti-escalation checks |
| Administrative actions (guarded) | COMPLETE | Preview → confirm → execute (`backend/app/api/v1/admin_actions.py`), Monitoring Only default, production confirmation |
| Monitoring-only access | COMPLETE | `management_mode` (`monitoring_only` default), read-only workspace enforcement |
| Auditor access | COMPLETE | `audit.view`, `audit.export` permissions; "Security Auditor" system role in `backend/app/domain/permissions.py` |

## Database fleet

| Capability | Status | Evidence |
|---|---|---|
| Registration | COMPLETE | `POST /api/v1/servers`, 9-step onboarding wizard with streamed staged test (`backend/app/api/v1/onboarding.py`, `frontend/src/features/onboarding/onboarding-wizard.tsx`) |
| Capability discovery | COMPLETE | `discover_capabilities()` per adapter, `GET /servers/{id}/capabilities` |
| Network discovery (find servers) | MISSING | No subnet/cloud-API discovery; servers are registered explicitly |
| Inventory (databases, schemas, objects) | COMPLETE | `backend/app/api/v1/server_data.py`, object browser UI |
| Tagging | COMPLETE | `ServerTag` model, tag filters `GET /servers?tag=` |
| Sites / environments / applications / clusters | COMPLETE | Generic CRUD `backend/app/api/v1/inventory.py` |
| Grouping (clusters, topology) | COMPLETE | Cluster membership + roles, topology page |
| Bulk onboarding | COMPLETE | `/servers/bulk/template.csv`, `/validate`, `/create`; CSV/JSON/grid UI `frontend/src/features/onboarding/bulk.tsx`; passwords rejected |
| Import / export of inventory | COMPLETE | Import via bulk onboarding; round-trip export/restore of the whole configuration through platform backups (inventory component) |

## Monitoring

| Capability | Status | Evidence |
|---|---|---|
| Current metrics | COMPLETE | `GET /metrics/current`, `/servers/{id}/metrics/latest` |
| Historical metrics | COMPLETE | VictoriaMetrics backend (`backend/app/timeseries/victoriametrics.py`), `GET /metrics/range`, downsampling/aggregation |
| Connections / sessions / locks / blocking | COMPLETE | Adapters' `get_sessions` / `get_locks` with `blocked_by`; validated on six engines |
| Transactions / I/O / cache | COMPLETE | Normalized counters (`backend/app/timeseries/catalog.py`, `normalizer.py`) |
| CPU / memory | PARTIAL | Only where the engine exposes it (SQL Server, Oracle, Db2 LUW; MySQL memory partial); never fabricated (`unavailable` reasons) |
| Storage | COMPLETE | `get_storage_metrics`, storage tab, capacity endpoint |
| Replication | PARTIAL | FULL on PostgreSQL / MySQL / MariaDB; PARTIAL on SQL Server (AG), Oracle (Data Guard), Db2 (HADR); replication topology view |
| Availability | COMPLETE | `availability.up`, health status + reasons, circuit breaker per server (`backend/app/jobs/runner.py`) |
| Collector health | COMPLETE | `/collectors/status`, `/collectors/jobs`, `collector_state.components`, Prometheus collector metrics |

## Query performance

| Capability | Status | Evidence |
|---|---|---|
| Top SQL | COMPLETE | `GET /queries/top`, `/servers/{id}/query-stats`, top-N visualization |
| Query fingerprints | COMPLETE | `QueryFingerprint` model (`backend/alembic/versions/0003_query_fingerprints.py`), `/query-fingerprints` |
| Execution statistics | COMPLETE | Calls, total/mean/max, rows, reads, temp, CPU where available |
| P95 / P99 | NOT APPLICABLE | Engines expose cumulative mean/max per statement, not distributions; documented in the UI instead of approximated |
| Historical trends per statement | COMPLETE | `/query-stats/{fingerprint}/history` (time-series) |
| Execution plans | COMPLETE | `POST /servers/{id}/explain` (estimate + rolled-back analyze), graph/tree/raw viewer `frontend/src/components/plan-graph.tsx` |
| Query comparison | COMPLETE | Period vs period `GET /servers/{id}/query-compare` (presets, custom periods, unclassified when a period has no data); plan vs plan of saved plans `GET /plans/compare` (`app/services/perf_intel.py`, `tests/unit/test_perf_intel.py`, `tests/api/test_performance.py`) |
| Regression detection | COMPLETE | Per-fingerprint mean latency, recent window vs baseline, thresholds in settings, rule `GEN-PERF-011`; plan changes for Oracle (plan hash) and SQL Server (cached plan count), not detectable on engines whose statistics carry no plan identity (`GET /servers/{id}/query-regressions`) |

## Performance optimization

| Capability | Status | Evidence |
|---|---|---|
| Recommendations | COMPLETE | Rule engine `backend/app/advisors/engine.py`, 61 performance rules across engines (`advisors/performance/*.py`); every rule has a firing and a quiet test (`tests/unit/test_rules_coverage.py`, meta-test fails for an untested rule) |
| Evidence | COMPLETE | Each finding stores evidence; rules must not fire on unknown evidence |
| Confidence / risk | COMPLETE | `confidence`, severity and risk on findings (`backend/app/models/advisor.py`, `backend/app/domain/risk.py`) |
| Historical validation (did the fix help?) | COMPLETE | Before/after measurement of the triggering metric or statement latency when a recommendation is resolved, job `validate_recommendations`, Outcomes page; configuration/security findings reported as not measurable |
| Baselines | COMPLETE | `GET /servers/{id}/metrics/baseline`; z-score alert rules (`aggregation: zscore`, `baseline_days`, min. 60 samples, recent window excluded) in `app/services/alerting.py` |
| Capacity planning | COMPLETE | Theil–Sen trend with Sen 90 % interval and residual band, days to full earliest/likely/latest per server, database and tablespace (`GET /capacity/forecast`, `/servers/{id}/capacity-forecast`), rule `GEN-PERF-012`; seasonality is not modelled. Tablespace maximums only where the engine reports one (Oracle, Db2) |

## Security

| Capability | Status | Evidence |
|---|---|---|
| Privileges / excessive access | COMPLETE | Per-engine privilege inventory, `GET /access/privileged-accounts` |
| Administrative users | COMPLETE | `admin_reasons` per engine, privileged accounts page |
| Encryption (TLS, TDE) | COMPLETE | Security evidence per engine (e.g. `force_encryption`, `network_encryption`, unencrypted tablespaces) |
| Audit configuration | COMPLETE | Rules for server audits / unified auditing / Db2 audit |
| Dormant accounts | COMPLETE | `dormant_accounts` rule (`backend/app/advisors/security/common.py`) |
| PUBLIC grants | COMPLETE | Engine rules (PG, Oracle `public_system_privileges`, Db2 `public_grants`) |
| Security recommendations | COMPLETE | 55 security rules (`advisors/security/*.py`), `POST /servers/{id}/security-scan`; `tests/unit/test_rules_coverage.py`; Compliance counts from `GET /advisor/rule-summary` |
| Security posture | COMPLETE | `GET /security/posture`, posture dashboard |

## Administration (target databases)

| Capability | Status | Evidence |
|---|---|---|
| Users / roles / privileges | COMPLETE | `build_admin` per engine (create/drop user, create role, grant, revoke); Db2 users are external (by design) |
| Schemas | COMPLETE | `create_schema` (not on Oracle: schema = user) |
| Indexes | COMPLETE | `create_index` on every engine with admin support |
| Database objects (browse / DDL) | COMPLETE | Object browser, DDL extraction (PARTIAL on some engines, see engine matrix) |
| Configuration (read) | COMPLETE | `get_configuration`; configuration changes are not offered |
| Safe administrative actions | COMPLETE | Guarded catalogue `backend/app/adapters/admin.py`, preview, confirmation, audit, passwords bound/masked |

## Alerting

| Capability | Status | Evidence |
|---|---|---|
| Thresholds / alert rules | COMPLETE | Metric threshold (latest or avg/min/max over a window) + for-duration, health-state and collector rules; scope all/environment/server/tag; `tests/unit/test_alerting*.py`, `tests/api/test_alerting*.py` |
| Alert lifecycle / acknowledgement | COMPLETE | pending → firing → acknowledged → resolved, auto-resolve, de-duplication (partial unique index), `/alerts/{id}/acknowledge` and `/alerts/{id}/resolve`, audit; Operations → Alerts |
| Notification channels | COMPLETE | Webhook (generic, Slack, Teams presets; HMAC, SSRF guard; URL and headers encrypted and write-only, never audited) and SMTP (STARTTLS / implicit TLS, CA file); routing by environment/tag/engine; retries with backoff; delivery log; tested against local SMTP (aiosmtpd) and HTTP receivers |
| Escalation | PARTIAL | Re-notification every N minutes until acknowledged (`renotify_minutes`); no multi-level escalation chains |
| Maintenance windows | COMPLETE | One-off and recurring (daily/weekly, time zone, DST), scope incl. tags; suppressed alerts notify after the window if still firing |

## Reporting

| Capability | Status | Evidence |
|---|---|---|
| Fleet (inventory) report | COMPLETE | `backend/app/services/reports.py` `inventory`, `GET /reports/{key}.{fmt}` |
| Performance report | COMPLETE | `performance` report |
| Capacity report | COMPLETE | `capacity` report (size + 7-day growth) |
| Recommendations report | COMPLETE | `recommendations` report |
| Security report | COMPLETE | `security-findings` (statuses, minimum severity, lifecycle dates, remediation) and `security-posture` reports; `tests/api/test_reports.py` |
| Query report | COMPLETE | `query-statistics` (window, share per server, masked text), `slow-queries`, `query-regressions`; `tests/api/test_reports.py` |
| Audit report / export | COMPLETE | `GET /audit/export.csv` (`audit.export` permission) |
| Export formats | COMPLETE | CSV (formula-injection guard) and server-side PDF (`backend/app/services/report_pdf.py`, ReportLab) for every report; preview of 50 rows; `tests/unit/test_report_pdf.py`. No Excel format |
| Scheduling | COMPLETE | `backend/app/services/report_schedules.py`, job `report_scheduler`, migration `0012`, Reports → Schedules wizard; changed or run only by owner, creator or `users.manage`; delivered to a real SMTP server in `tests/api/test_reports.py`; e-mail only |

## Topology

| Capability | Status | Evidence |
|---|---|---|
| Fleet / environment / site / cluster topology | COMPLETE | `frontend/src/features/topology/topology-page.tsx` (React Flow, grouping for 1,000+ servers), `src/test/topology.test.tsx` |
| Replication topology | COMPLETE | `frontend/src/features/topology/replication.tsx`, per-engine payload parsing `replication-model.ts` |

## User experience

| Capability | Status | Evidence |
|---|---|---|
| Dashboard | COMPLETE | `frontend/src/features/dashboard/dashboard.tsx` (filterable KPIs, sparklines, fleet map) |
| Search / command palette | COMPLETE | `frontend/src/components/command-palette.tsx` (servers, pages, RBAC-filtered actions) |
| Onboarding / wizards | COMPLETE | Setup, LDAP, OIDC, database onboarding, bulk onboarding |
| Dark mode | COMPLETE | Token-based themes `frontend/src/index.css` |
| Accessibility | PARTIAL | Keyboard navigation, labels, reduced motion, skip link; no automated a11y audit (axe) in CI |
| Responsive layout | COMPLETE | Mobile navigation drawer, checked at 390 px |
| Empty / loading / error states | COMPLETE | `frontend/src/components/ui/states.tsx` used across pages |

## API

| Capability | Status | Evidence |
|---|---|---|
| REST API | COMPLETE | `backend/app/api/v1/*` (~180 routes) |
| OpenAPI | COMPLETE | FastAPI `/api/openapi.json`, snapshot in `docs/openapi.json` |
| Pagination / filtering / sorting | COMPLETE | `PageParams`, `Page[...]` responses on list endpoints |
| Versioning | PARTIAL | URL prefix `/api/v1`; no deprecation policy |
| Error model | COMPLETE | Uniform `{"error": {code, message, details, request_id}}` (`backend/app/core/errors.py`) |
| Rate limiting | COMPLETE | Per-principal policies `api` / `target` / `expensive` plus `login` and `token_failures`; sliding-window counters in PostgreSQL shared by replicas (`backend/app/services/ratelimit.py`, migration 0006); `429` with `Retry-After` and `RateLimit-*`; `dbm_rate_limited_total` metric; tests for exact Retry-After, concurrency and replica sharing |
| API tokens (non-interactive clients) | COMPLETE | `ApiToken` (`backend/app/models/identity.py`, migration 0005), `app/services/api_tokens.py`: hashed, expiring, scope-narrowed personal tokens, `Authorization: Bearer`, interactive-only deny-list, CIDR allow-list, audit; user menu → API tokens; tests `tests/unit/test_api_tokens.py`, `tests/api/test_api_tokens.py` |

## Integration

| Capability | Status | Evidence |
|---|---|---|
| LDAP / OIDC | COMPLETE (real-validated: OpenLDAP, Keycloak) | See Identity |
| SMTP | COMPLETE | Alert e-mail channels (`DBM_SMTP_*` or per channel) and scheduled report e-mail (0.14.2) |
| Webhooks | COMPLETE | Alert webhook channel with HMAC signing and SSRF protection (`DBM_WEBHOOK_ALLOW_PRIVATE`) |
| Teams / Slack | COMPLETE | Webhook presets (Slack blocks, Teams Adaptive Card); payloads tested |
| SIEM | COMPLETE | Audit forwarding: syslog RFC 5424 over UDP/TCP/TLS and signed webhook batches, at-least-once cursor (`app/services/siem.py`, `tests/api/test_siem.py`) |
| Prometheus (platform metrics) | COMPLETE | `GET /metrics` |
| Prometheus export of *managed database* metrics | MISSING | Database telemetry is in VictoriaMetrics (Prometheus-compatible query API), not re-exposed on `/metrics` |
| OpenTelemetry | PARTIAL | Scrape `/metrics` with the OTel Prometheus receiver; no native OTLP traces |
| External secret managers | PARTIAL | Providers `local`, `env`, `file`, `vault` (HashiCorp Vault KV v2: token/AppRole/Kubernetes, `tests/api/test_vault.py`); AWS/Azure/Google secret managers planned (0.11.7) |

## Operations

| Capability | Status | Evidence |
|---|---|---|
| Docker Compose | COMPLETE | `docker-compose.yml`; backend image with ODBC Driver 18 (`backend/Dockerfile`) |
| Kubernetes readiness | COMPLETE | Helm chart `deployment/helm/dbmanager` (API, workers, frontend, bundled or external PostgreSQL/VictoriaMetrics, PDB, HPA, NetworkPolicy, Ingress, `helm test`, values schema) and reference manifests `deployment/kubernetes/`; restricted Pod Security; CI: kubeconform 1.29/1.31, policy check, `tests/deploy/test_helm_chart.py`, kind install with `helm test` and leader failover (`ci-kubernetes.yml`) |
| OpenShift readiness | PARTIAL | Chart `openshift=true` (no pinned UID/GID/fsGroup, Route with edge TLS) checked against restricted-v2 rules in CI (`scripts/k8s-policy-check.py --openshift`); not installed on an OpenShift cluster |
| Backup / restore | COMPLETE | In-app Backup & Restore for the platform and managed databases (see Platform); stack scripts `scripts/backup.sh`, `restore.sh`, `restore-drill.sh` |
| Upgrade | COMPLETE | Migrations step by step, model parity, upgrades with data from the first schema and the releases 0.12.0, 0.13.0 and 0.14.0 (`tests/migrations/test_migrations.py`); concurrent replica start (`test_concurrent_start.py`); Helm rolling upgrade with migrations under a lock |
| High availability | COMPLETE | Stateless API replicas, scheduler leader election with failover tests (`tests/api/test_leader_failover.py`, kind pod deletion), concurrent-start locks, instance registry (`app/services/instances.py`, `GET /api/v1/platform/instances`, Deployment card, `tests/api/test_instances.py`), TLS to PostgreSQL (`DBM_DATABASE_TLS_*`, `tests/api/test_control_plane_tls.py`) and VictoriaMetrics; HA of PostgreSQL/VictoriaMetrics themselves is left to the operator ([high availability](../deployment/high-availability.md)) |
| Scaling | PARTIAL | Collector concurrency, worker processes (`docs/deployment/scaling.md`); no load test |
| Troubleshooting | PARTIAL | Troubleshooting table in `docs/operations/index.md`; full guide being written |
| Real-engine test environment | COMPLETE | `docker-compose.test.yml`, `scripts/integration-test.{sh,ps1}`, matrix in `backend/app/data/real_engine_validation.json` |

## Asset protection (backup & restore)

| Capability | Status | Evidence |
|---|---|---|
| Platform backup (configuration, inventory, identity, findings, alerting, settings, audit optional, telemetry optional) | COMPLETE | `backend/app/backup/platform.py`, `service.py`; every table classified (unit test) |
| Encryption (age, recipient or passphrase; master key never in the archive) | COMPLETE | `backend/app/backup/encryption.py`; unit + API tests |
| Storage targets (local, mounted, NFS, S3-compatible) | COMPLETE | `backend/app/backup/storage.py`; S3 tested against a moto server |
| Azure Blob / Google Cloud Storage (native) | PLANNED | Sprint 0.12.6 (S3-compatible endpoints work today) |
| Verification (SHA-256 read-back, contents, engine checks), CORRUPTED detection | COMPLETE | Tamper test in `tests/api/test_backup.py` |
| Platform restore (compatibility, impact analysis, pre-restore backup) | COMPLETE | `tests/api/test_backup.py::test_platform_backup_verify_restore_end_to_end` |
| PostgreSQL backup / restore | COMPLETE (real-validated: 16.15, least privilege) | `engines.PostgreSQLProvider`; API + integration tests |
| MariaDB backup / restore | COMPLETE (real-validated: 11.4.13 and 10.11, least privilege) | `engines.MySQLFamilyProvider`; `tests/integration/test_backup_engines.py` |
| SQL Server backup / restore | COMPLETE (real-validated: 2022, `db_backupoperator` / `dbcreator`) | `engines.SQLServerProvider` |
| Oracle Data Pump export / import | COMPLETE (real-validated: 21c XE, no `DBA`) | `engines.OracleProvider` |
| MySQL backup / restore | COMPLETE (real-validated: 8.4.11 with binary logging on, least privilege) | `engines.MySQLFamilyProvider` binary-logging preflight |
| Db2 LUW online backup | COMPLETE (real-validated: 11.5.9.0 as a SYSMAINT operator) | `engines.Db2LUWProvider` |
| Db2 LUW restore, Oracle RMAN, Db2 for z/OS | NOT APPLICABLE (external tool required) | Reported as such by capability discovery; Db2 LUW restore command generated |
| Point-in-time recovery | PLANNED | Sprint 0.12.7; not claimed by any provider |
| Policies, schedules, retention, bulk, notifications, metrics, protection status | COMPLETE | `service.schedule`, `apply_retention`, `protection` (manual policies without a maximum age protect for 8 days), `scope_servers`; jobs, policies, targets and overview scoped to the caller; `tests/api/test_backup.py`, `test_backup_scope.py`, `test_backup_extras.py`, `tests/unit/test_backup_units.py` (DST both ways) |
| Disaster recovery (scan a target into a new catalog) | COMPLETE | `service.scan_target`; tested |

## Repository and release engineering

| Capability | Status | Evidence |
|---|---|---|
| Source control | COMPLETE | Public repository [hbenabdalla/dbmanager](https://github.com/hbenabdalla/dbmanager), `main` branch, logical commits |
| License | COMPLETE | Apache-2.0 — `LICENSE`, README, `backend/pyproject.toml`, `frontend/package.json`, About page |
| CI | COMPLETE | `.github/workflows/` backend (lint, unit, API, migrations, PostgreSQL real engine, control-plane TLS), frontend, Kubernetes (manifests, chart tests, kind install and failover), docs (`mkdocs build --strict`), Docker images + Compose health + restore drill, security (gitleaks, pip-audit, npm audit); required checks run on every pull request (no path filters) |
| Contribution / security policy | COMPLETE | `CONTRIBUTING.md`, `SECURITY.md` (private advisories), `CODE_OF_CONDUCT.md`, issue forms, PR template |
| Changelog / versioning | COMPLETE | `CHANGELOG.md` (Keep a Changelog, SemVer); version `0.15.0` (published tags: v0.12.0, v0.13.0, v0.14.0) |
| Release artefacts | COMPLETE (runs on the tag) | `.github/workflows/release.yml`: GHCR images with SBOM and SLSA provenance, keyless cosign signatures, signed OCI Helm chart, SPDX SBOMs and notes on the GitHub release |
| Published documentation | COMPLETE | Read the Docs project `db-manager`: [https://db-manager.readthedocs.io/en/latest/](https://db-manager.readthedocs.io/en/latest/), built from `.readthedocs.yaml` with `mkdocs build --strict`; linked from the app's Help menu and About page |
