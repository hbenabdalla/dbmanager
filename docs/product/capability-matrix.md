# Capability matrix

Evidence-based inventory of what DB Manager does **today**, produced by inspecting the source tree
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

Paths are relative to the repository root (`backend/app/...`, `frontend/src/...`). Test counts
at the time of the audit: 221 backend unit, 73 backend API, 75 real-engine integration (plus 1
documented skip), 54 frontend tests.

> **Updated after this release's sprints.** Version/build metadata and the About page (0.9.4),
> upgrade and recovery verification (0.9.5), and alerting with lifecycle, maintenance windows,
> webhook and SMTP channels (0.10.1–0.10.3) have landed with tests; their rows below are updated.
> Test counts now: 313 backend unit + API, 3 migration/backup, 75 real-engine integration (plus 1
> documented skip), 59 frontend. See the [roadmap](roadmap.md).

## Platform

| Capability | Status | Evidence |
|---|---|---|
| Installation (Docker Compose) | COMPLETE | `docker-compose.yml` (frontend, backend, PostgreSQL, VictoriaMetrics), `scripts/init-env.sh` / `.ps1`, `.env.example` |
| Bootstrap (first admin, system roles, environments) | COMPLETE | `backend/app/seed/bootstrap.py`, `python -m app.cli bootstrap`, forced password change (`must_change_password`) |
| Platform setup wizard (resumable) | COMPLETE | `backend/app/api/v1/setup.py`, `frontend/src/features/setup/`, `backend/tests/api/test_onboarding.py` |
| Configuration | COMPLETE | `backend/app/core/config.py` (`DBM_*` env), runtime settings `GET/PUT /api/v1/settings` (`backend/app/services/app_settings.py`) |
| Health / readiness | COMPLETE | `GET /healthz`, `GET /readyz` with per-component status (`backend/app/api/ops.py`), tests in `backend/tests/api/test_metrics.py` |
| Upgrade (schema migrations) | COMPLETE | Alembic `0001–0004`, applied on start (`DBM_RUN_MIGRATIONS_ON_START`) or `python -m app.cli migrate`; `backend/tests/migrations/test_migrations.py` upgrades step by step, downgrades to base, re-upgrades and checks head schema = models (CI) |
| Backup | COMPLETE | `scripts/backup.sh` (pg_dump -Fc + readability check, optional VictoriaMetrics snapshot); procedure in `docs/operations/backup-restore.md`; round trip tested in `backend/tests/migrations/test_backup_restore.py` |
| Restore | COMPLETE | `scripts/restore.sh` (recreate DB, pg_restore, migrations on start); credentials decrypt after restore with the same key and fail with another (tested) |
| Logging | COMPLETE | Structured JSON logging `backend/app/core/logging.py`, request IDs in `backend/app/core/middleware.py`, secrets masked (`clean_message`) |
| Observability (self-metrics) | COMPLETE | Prometheus `GET /metrics` (`backend/app/core/metrics.py`: HTTP, collectors, scheduler leader, pipeline counters) |
| Diagnostics | PARTIAL | Collectors page (`/api/v1/collectors/status`, `/collectors/jobs`), monitoring-storage page, driver health; no downloadable diagnostic bundle |
| Version / build metadata | COMPLETE | `0.9.0-dev` in `backend/app/__init__.py`, `pyproject.toml`, `frontend/package.json`; build args `DBM_BUILD_VERSION/DBM_GIT_COMMIT/DBM_BUILD_DATE`; `GET /api/v1/version`, `GET /api/v1/about` (versions, schema revision, engines, status, links); Platform → About |

## Identity

| Capability | Status | Evidence |
|---|---|---|
| Local authentication | COMPLETE | `POST /api/v1/auth/login`, Argon2 hashing `backend/app/core/security.py`, lockout (`login_max_failures`, `login_lockout_minutes`), `tests/api/test_auth.py` |
| LDAP | IMPLEMENTED / NOT REAL-VALIDATED | `backend/app/services/ldap_auth.py`, LDAP wizard with staged tests (`backend/app/api/v1/idp_wizard.py`, `frontend/src/features/platform/idp-wizards.tsx`); tested only for failure stages, never against a directory server |
| Active Directory | IMPLEMENTED / NOT REAL-VALIDATED | Same LDAP path (`sAMAccountName`/`memberOf` configurable); no AD test environment |
| OIDC | IMPLEMENTED / NOT REAL-VALIDATED | `backend/app/services/oidc.py` (PKCE, state sealing, claims mapping), presets for Keycloak / Entra ID / Okta / generic; no IdP in CI |
| SSO (via OIDC) | IMPLEMENTED / NOT REAL-VALIDATED | `GET /auth/oidc/{id}/start` + `/callback`; SAML is not supported |
| Session management | COMPLETE | Server-side sessions `user_sessions` table, TTL + idle timeout (`session_ttl_minutes`, `session_idle_timeout_minutes`), HttpOnly cookies + CSRF token |
| Password policy | PARTIAL | Fixed policy: minimum length + 3 of 4 classes (`password_policy_errors`); not configurable, no history/expiry |
| MFA | MISSING | No TOTP/WebAuthn; MFA can be delegated to the OIDC provider |
| Group mapping | COMPLETE | `GroupMapping` model, `POST /identity-providers/{id}/mappings`, sync in `backend/app/services/identity_sync.py` |

## Authorization

| Capability | Status | Evidence |
|---|---|---|
| RBAC (roles, permissions, assignments) | COMPLETE | `backend/app/domain/permissions.py`, `backend/app/services/authz.py`, `/api/v1/roles`, `/role-assignments`, `tests/api/test_rbac.py`, `tests/unit/test_authz.py` |
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
| Import / export of inventory | PARTIAL | Import via bulk onboarding; inventory export only as CSV report; no round-trip export of configuration |

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
| Query comparison | MISSING | No side-by-side comparison of two periods or two plans |
| Regression detection | MISSING | No automatic detection of per-fingerprint latency regressions |

## Performance optimization

| Capability | Status | Evidence |
|---|---|---|
| Recommendations | COMPLETE | Rule engine `backend/app/advisors/engine.py`, 59 performance rules across engines (`advisors/performance/*.py`) |
| Evidence | COMPLETE | Each finding stores evidence; rules must not fire on unknown evidence |
| Confidence / risk | COMPLETE | `confidence`, severity and risk on findings (`backend/app/models/advisor.py`, `backend/app/domain/risk.py`) |
| Historical validation (did the fix help?) | MISSING | Findings have a lifecycle (open/acknowledged/dismissed/resolved) but no before/after measurement |
| Baselines | PARTIAL | `GET /servers/{id}/metrics/baseline` (mean, stddev, percentiles, z-score); not yet used by rules or alerts |
| Capacity planning | PARTIAL | `GET /metrics/capacity` (30-day growth, days until full), capacity report; no forecasting model beyond linear trend |

## Security

| Capability | Status | Evidence |
|---|---|---|
| Privileges / excessive access | COMPLETE | Per-engine privilege inventory, `GET /access/privileged-accounts` |
| Administrative users | COMPLETE | `admin_reasons` per engine, privileged accounts page |
| Encryption (TLS, TDE) | COMPLETE | Security evidence per engine (e.g. `force_encryption`, `network_encryption`, unencrypted tablespaces) |
| Audit configuration | COMPLETE | Rules for server audits / unified auditing / Db2 audit |
| Dormant accounts | COMPLETE | `dormant_accounts` rule (`backend/app/advisors/security/common.py`) |
| PUBLIC grants | COMPLETE | Engine rules (PG, Oracle `public_system_privileges`, Db2 `public_grants`) |
| Security recommendations | COMPLETE | 55 security rules (`advisors/security/*.py`), `POST /servers/{id}/security-scan` |
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
| Thresholds / alert rules | COMPLETE | `backend/app/models/alerting.py`, `app/services/alerting.py` (metric threshold + for-duration, health-state and collector rules; scope all/environment/server), job `evaluate_alerts`; tests `tests/unit/test_alerting.py`, `tests/api/test_alerting.py` |
| Alert lifecycle / acknowledgement | COMPLETE | pending → firing → acknowledged → resolved, auto-resolve, de-duplication (partial unique index), `/alerts/{id}/acknowledge` and `/alerts/{id}/resolve`, audit; Operations → Alerts |
| Notification channels | COMPLETE | `app/services/notify.py`: webhook (HMAC signature, SSRF guard, timeout) and SMTP (STARTTLS); write-only encrypted secrets, test send, delivery log; real SMTP/webhook endpoints not exercised in CI (mocked / local servers) |
| Escalation | PLANNED | After channels exist |
| Maintenance windows | COMPLETE | `/maintenance-windows`; alerts raised but marked suppressed and deliveries skipped (tested) |

## Reporting

| Capability | Status | Evidence |
|---|---|---|
| Fleet (inventory) report | COMPLETE | `backend/app/services/reports.py` `inventory`, `GET /reports/{key}.{fmt}` |
| Performance report | COMPLETE | `performance` report |
| Capacity report | COMPLETE | `capacity` report (size + 7-day growth) |
| Recommendations report | COMPLETE | `recommendations` report |
| Security report | PARTIAL | Security findings exportable via the findings list; no dedicated report definition |
| Query report | PARTIAL | Workspace result export (`POST /workspace/export.csv`); no query-statistics report |
| Audit report / export | COMPLETE | `GET /audit/export.csv` (`audit.export` permission) |
| Export formats | PARTIAL | CSV only (`RENDERERS = {"csv": ...}`); PDF not implemented |
| Scheduling | MISSING | No scheduled report delivery |

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
| REST API | COMPLETE | `backend/app/api/v1/*` (~140 routes) |
| OpenAPI | COMPLETE | FastAPI `/api/openapi.json`, snapshot in `docs/openapi.json` |
| Pagination / filtering / sorting | COMPLETE | `PageParams`, `Page[...]` responses on list endpoints |
| Versioning | PARTIAL | URL prefix `/api/v1`; no deprecation policy |
| Error model | COMPLETE | Uniform `{"error": {code, message, details, request_id}}` (`backend/app/core/errors.py`) |
| Rate limiting | COMPLETE | Per-principal policies `api` / `target` / `expensive` plus `login` and `token_failures`; sliding-window counters in PostgreSQL shared by replicas (`backend/app/services/ratelimit.py`, migration 0006); `429` with `Retry-After` and `RateLimit-*`; `dbm_rate_limited_total` metric; tests for exact Retry-After, concurrency and replica sharing |
| API tokens (non-interactive clients) | COMPLETE | `ApiToken` (`backend/app/models/identity.py`, migration 0005), `app/services/api_tokens.py`: hashed, expiring, scope-narrowed personal tokens, `Authorization: Bearer`, interactive-only deny-list, CIDR allow-list, audit; user menu → API tokens; tests `tests/unit/test_api_tokens.py`, `tests/api/test_api_tokens.py` |

## Integration

| Capability | Status | Evidence |
|---|---|---|
| LDAP / OIDC | IMPLEMENTED / NOT REAL-VALIDATED | See Identity |
| SMTP | COMPLETE | Alert email channel (`DBM_SMTP_*` or per-channel); scheduled report e-mail not yet |
| Webhooks | COMPLETE | Alert webhook channel with HMAC signing and SSRF protection (`DBM_WEBHOOK_ALLOW_PRIVATE`) |
| Teams / Slack | PLANNED | Via generic webhook first |
| SIEM | PARTIAL | Audit CSV export and structured JSON logs can be shipped; no syslog/CEF forwarder |
| Prometheus (platform metrics) | COMPLETE | `GET /metrics` |
| Prometheus export of *managed database* metrics | MISSING | Database telemetry is in VictoriaMetrics (Prometheus-compatible query API), not re-exposed on `/metrics` |
| OpenTelemetry | PARTIAL | Scrape `/metrics` with the OTel Prometheus receiver; no native OTLP traces |
| External secret managers | PARTIAL | Providers `local`, `env`, `file` (`backend/app/services/secrets.py`); `vault` reserved, not implemented |

## Operations

| Capability | Status | Evidence |
|---|---|---|
| Docker Compose | COMPLETE | `docker-compose.yml`; backend image with ODBC Driver 18 (`backend/Dockerfile`) |
| Kubernetes readiness | PARTIAL | Plain manifests `deployment/kubernetes/` (ConfigMap, Secret, migrate Job, Deployments, StatefulSet, Ingress, NetworkPolicy); **no Helm chart**, not CI-validated |
| OpenShift readiness | PARTIAL | Non-root UID 10001 / GID 0 images, `deployment/openshift/route.yaml`; not tested on a cluster |
| Backup / restore | COMPLETE | `scripts/backup.sh`, `scripts/restore.sh`, automated round-trip test |
| Upgrade | PARTIAL | Migrations tested step by step and for model parity; no upgrade test between two *released* versions yet (no release tagged) |
| High availability | PARTIAL | Stateless API, scheduler leader election (`dbm_scheduler_leader`), `process_role` split (api/worker); HA of PostgreSQL/VictoriaMetrics left to the operator |
| Scaling | PARTIAL | Collector concurrency, worker processes (`docs/deployment/scaling.md`); no load test |
| Troubleshooting | PARTIAL | Troubleshooting table in `docs/operations/index.md`; full guide being written |
| Real-engine test environment | COMPLETE | `docker-compose.test.yml`, `scripts/integration-test.{sh,ps1}`, matrix in `backend/app/data/real_engine_validation.json` |

## Repository and release engineering

| Capability | Status | Evidence |
|---|---|---|
| Source control | COMPLETE | Public repository [hbenabdalla/dbmanager](https://github.com/hbenabdalla/dbmanager), `main` branch, logical commits |
| License | COMPLETE | Apache-2.0 — `LICENSE`, README, `backend/pyproject.toml`, `frontend/package.json`, About page |
| CI | PARTIAL | `.github/workflows/` backend (lint, unit, API, migrations, PostgreSQL real engine), frontend, docs (`mkdocs build --strict`), Docker images + Compose health, security (gitleaks, pip-audit, npm audit), on-demand real-engine workflow. First run on `main`: backend, frontend, docs and image builds green; the Compose health job (script not executable) and gitleaks (history re-parented) failed and are fixed — awaiting the next run |
| Contribution / security policy | COMPLETE | `CONTRIBUTING.md`, `SECURITY.md` (private advisories), `CODE_OF_CONDUCT.md`, issue forms, PR template |
| Changelog / versioning | COMPLETE | `CHANGELOG.md` (Keep a Changelog, SemVer); version `0.9.0-dev` |
| Published documentation | COMPLETE | Read the Docs project `db-manager`: [https://db-manager.readthedocs.io/en/latest/](https://db-manager.readthedocs.io/en/latest/), built from `.readthedocs.yaml` with `mkdocs build --strict`; linked from the app's Help menu and About page |
