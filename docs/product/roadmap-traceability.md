# Roadmap traceability

Capability → gap → milestone/sprint → implementation → tests → documentation. A row is only
marked VERIFIED COMPLETE when the implementation and tests columns point at real code. Paths are relative to
the repository root; "—" means not yet created.

## Baseline (VERIFIED COMPLETE)

| Capability | Gap | Sprint | Implementation | Tests | Documentation |
|---|---|---|---|---|---|
| Engine adapters (7) with capability discovery | — | Baseline | `backend/app/adapters/{postgres,mysql,mssql,oracle,db2,db2zos}/` | `backend/tests/unit/test_{adapters,mssql,oracle,db2,mysql_family}.py`, `backend/tests/integration/` (75 real-engine tests) | `docs/development/adding-a-database-engine.md`, `docs/database-engines/permissions.md` |
| Real-engine validation matrix | — | Baseline | `backend/tests/integration/{matrix,conftest}.py`, `backend/app/data/real_engine_validation.json`, `scripts/integration-test.{sh,ps1}` | the integration suites themselves | `docs/operations/index.md` |
| Telemetry pipeline | — | Baseline | `backend/app/timeseries/`, `backend/app/services/collection.py`, `backend/app/jobs/` | `backend/tests/unit/test_timeseries.py`, `backend/tests/integration/test_victoriametrics.py`, `backend/tests/api/test_metrics.py` | `docs/monitoring/architecture.md`, `docs/monitoring/timeseries-storage.md` |
| Advisors (performance + security rules) | — | Baseline | `backend/app/advisors/` | `backend/tests/unit/test_rules.py`, security/recommendation tests in integration suites | `docs/security/index.md` |
| RBAC & scopes | — | Baseline | `backend/app/domain/{permissions,scopes}.py`, `backend/app/services/authz.py` | `backend/tests/unit/test_authz.py`, `backend/tests/api/test_rbac.py` | `docs/administration/rbac.md` |
| Guarded administration | — | Baseline | `backend/app/adapters/admin.py`, `backend/app/api/v1/admin_actions.py` | administration tests in each integration suite | `docs/database-engines/permissions.md` |
| Onboarding & wizards | — | Baseline | `backend/app/api/v1/{setup,onboarding,idp_wizard}.py`, `frontend/src/features/{setup,onboarding}/` | `backend/tests/api/test_onboarding.py`, `frontend/src/test/wizards.test.tsx` | `docs/architecture/ux-audit.md` |
| Topology | — | Baseline | `frontend/src/features/topology/` | `frontend/src/test/topology.test.tsx` | `docs/architecture/ux-audit.md` |
| Driver health (SQL Server ODBC) | — | Baseline | `backend/app/adapters/drivers.py` | `backend/tests/unit/test_drivers.py`, `backend/tests/api/test_servers.py` | `docs/database-engines/index.md` |

## Milestone 0.9 — Release foundation

| Capability | Gap | Sprint | Implementation | Tests | Documentation | Status |
|---|---|---|---|---|---|---|
| Source control & publication | G-01 | 0.9.1 | public repository `hbenabdalla/dbmanager` | CI on every push and pull request | `CONTRIBUTING.md` | VERIFIED COMPLETE |
| License | G-02 | 0.9.1 | `LICENSE` (Apache-2.0), `backend/pyproject.toml`, `frontend/package.json`, About page | `tests/api/test_alerting.py` (About reports Apache-2.0) | `README.md`, `CONTRIBUTING.md` | VERIFIED COMPLETE |
| Governance files | G-05, G-07 | 0.9.1 | `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md`, `.gitignore` | gitleaks (`.github/workflows/security.yml`) | same files | VERIFIED COMPLETE |
| CI | G-03 | 0.9.2 | `.github/workflows/` | workflows on GitHub | `CONTRIBUTING.md` | VERIFIED COMPLETE |
| Documentation site | G-06 | 0.9.3 | `mkdocs.yml`, `.readthedocs.yaml`, `docs/requirements.txt`, `docs/**` | `mkdocs build --strict` (CI and Read the Docs) | [https://db-manager.readthedocs.io/en/latest/](https://db-manager.readthedocs.io/en/latest/) | VERIFIED COMPLETE |
| Version & About page | G-04 | 0.9.4 | `backend/app/api/v1/about.py`, Platform → About, Help menu | `tests/api/test_alerting.py::test_about_and_version`, `tests/unit/test_about_links.py`, frontend `alerts.test.tsx` | [About](../administration/about.md) | VERIFIED COMPLETE |
| Upgrade & recovery verification | G-20, G-22 | 0.9.5 | `scripts/backup.sh`, `scripts/restore.sh`, `scripts/restore-drill.sh` (Docker CI) | `tests/migrations/` (upgrade from empty, first and previous schema with data; downgrade; model parity; backup/restore round trip) | [upgrade](../operations/upgrade.md), [backup and restore](../operations/backup-restore.md) | VERIFIED COMPLETE |

## Milestones 0.10 and 0.11 — Operations, alerting and enterprise integration

| Capability | Gap | Sprint | Implementation | Tests | Documentation | Status |
|---|---|---|---|---|---|---|
| Alert rules & evaluation (tag scope, window aggregates) | G-10, G-11 | 0.10.1 | `backend/app/services/alerting.py`, migrations `0004`, `0007` | `tests/api/test_alerting.py`, `tests/unit/test_alerting_completion.py`, `tests/api/test_alerting_completion.py` | [alerting](../user-guide/alerting.md), [architecture](../architecture/alerting.md) | VERIFIED COMPLETE |
| SMTP (STARTTLS/TLS) & webhook channels, retries, routing | G-12, G-13, G-14 | 0.10.2 | `backend/app/services/notify.py`, `alerting.retry_due` | `tests/unit/test_notify_delivery.py`, `tests/api/test_alerting*.py` | [notifications](../administration/notifications.md) | VERIFIED COMPLETE |
| Maintenance windows (recurring, time zones) | G-15 | 0.10.3 | `alerting.occurrences` / `window_state` | `tests/unit/test_alerting_completion.py` | [alerting](../user-guide/alerting.md) | VERIFIED COMPLETE |
| Alert inbox, Slack/Teams presets, re-notify | G-16, G-17 | 0.10.4 | `frontend/src/features/alerts/`, `notify.preset_payload` | `frontend/src/test/alerting-ui.test.tsx`, preset tests | [alerting](../user-guide/alerting.md) | VERIFIED COMPLETE |
| Collector diagnostics bundle | G-23 | 0.10.5 | `backend/app/services/diagnostics.py`, `app/core/redact.py` | `tests/api/test_diagnostics.py`, `tests/unit/test_redact.py` | [diagnostics](../administration/diagnostics.md) | VERIFIED COMPLETE |
| API tokens | G-18 | 0.11.1 | `backend/app/services/api_tokens.py`, migration `0005` | `tests/api/test_api_tokens.py` | [API tokens](../administration/api-tokens.md) | VERIFIED COMPLETE |
| API rate limiting | G-19 | 0.11.2 | `backend/app/services/ratelimit.py`, migration `0006` | `tests/unit/test_ratelimit.py`, `tests/api/test_rate_limits.py` | [rate limiting](../administration/rate-limiting.md) | VERIFIED COMPLETE |
| Real LDAP validation | G-40 | 0.11.3 | `docker-compose.test.yml` (`identity`), fixtures `backend/tests/integration/identity/` | `tests/integration/test_identity.py` (run: OpenLDAP 2.6) | [integration tests](../operations/integration-tests.md) | VERIFIED COMPLETE |
| Real OIDC (Keycloak) and Vault validation | G-40, G-36 | 0.11.3 | same | `test_identity.py::test_oidc_code_flow_with_pkce_against_keycloak`, `test_vault_real.py` (run 2026-10-19: PASS) | same | DONE |
| SIEM forwarding | G-33 | 0.11.4 | `backend/app/services/siem.py`, migration `0009`, Platform → Audit forwarding | `tests/api/test_siem.py`, `tests/unit/test_siem_format.py` | [SIEM](../administration/siem.md) | VERIFIED COMPLETE |
| HashiCorp Vault credentials | G-36 | 0.11.5 | `backend/app/services/vault.py` | `tests/api/test_vault.py` | [secret providers](../administration/secret-providers.md) | VERIFIED COMPLETE |
| MFA (TOTP, OIDC amr/acr) | G-38 | 0.11.6 | `backend/app/services/mfa.py`, migration `0008`, Account → Security | `tests/api/test_mfa.py`, `tests/unit/test_mfa.py`, `frontend/src/test/account-admin.test.tsx` | [MFA](../administration/mfa.md) | VERIFIED COMPLETE |
| Cloud secret managers | G-36 | 0.11.7 | — | — | — | PLANNED |

## Milestone 0.12 — Asset protection (backup & restore)

| Capability | Gap | Sprint | Implementation | Tests | Documentation | Status |
|---|---|---|---|---|---|---|
| Platform backup, encryption, storage targets | G-20 | 0.12.1 | `backend/app/backup/{platform,encryption,storage,service}.py`, migration `0010` | `tests/unit/test_backup_units.py`, `tests/api/test_backup.py` | [Backup & Restore](../backup-restore/index.md) | VERIFIED COMPLETE |
| Platform restore with impact analysis | G-22 | 0.12.2 | `service.preview_platform_restore`, `platform.apply` / `impact` | `tests/api/test_backup.py` | [restore](../backup-restore/restore.md) | VERIFIED COMPLETE |
| Managed database backup (PostgreSQL, MariaDB, SQL Server, Oracle, MySQL, Db2 LUW) | — | 0.12.3 | `backend/app/backup/engines.py` | `tests/api/test_backup.py`, `tests/integration/test_backup_engines.py` (run) | [engine support](../backup-restore/engine-support.md) | VERIFIED COMPLETE |
| Policies, retention, bulk, notifications, metrics, protection status, UI | — | 0.12.4 | `service.schedule`, `apply_retention`, `protection`; `frontend/src/features/backup/` | `tests/api/test_backup.py`, frontend tests | [policies](../backup-restore/policies.md) | VERIFIED COMPLETE |
| Disaster recovery | G-22 | 0.12.5 | `service.scan_target` | `tests/api/test_backup.py` | [disaster recovery](../backup-restore/disaster-recovery.md) | VERIFIED COMPLETE |
| Azure Blob / GCS targets, PITR | — | 0.12.6, 0.12.7 | — | — | — | PLANNED |

## Milestone 0.13 — Performance Intelligence (DONE)

| Capability | Gap | Sprint | Code | Tests | Documentation | Status |
|---|---|---|---|---|---|---|
| Baselines in alert rules (z-score) | G-24 | 0.13.1 | `app/services/alerting.py` (`_zscore_condition`, `zscore_inputs`), `app/services/perf_intel.py` (`baselines`, `zscore`), migration `0011` (`alert_rules.baseline_days`), `frontend/src/features/alerts/alerts.tsx` (wizard, `BaselinePreview`) | `tests/unit/test_perf_intel.py`, `tests/api/test_performance.py::test_zscore_alert_rule`, `frontend/src/test/performance.test.tsx` | [baselines](../performance/baselines-and-capacity.md#baseline-relative-alert-rules-z-score) | DONE |
| Query regression and plan-change detection | G-27 | 0.13.2 | `perf_intel.query_regressions`, `QueryStat.plans/plan_id`, Oracle/SQL Server query statistics, metrics `db_query_plans`, `db_query_plan_id`, rule `GEN-PERF-011`, `app/api/v1/performance.py`, `features/performance/intelligence.tsx` | unit + API tests; real-engine assertions in `tests/integration/test_oracle.py`, `test_mssql.py` | [regressions](../performance/regressions-and-comparison.md) | DONE |
| Capacity forecasting | G-25 | 0.13.3 | `perf_intel.theil_sen`, `forecast`, `capacity_forecast`; `collection.size_samples` (discovery); rule `GEN-PERF-012`; `ForecastChart` | unit + API tests; size series from real storage data in the PostgreSQL, SQL Server, Oracle and Db2 integration tests | [capacity forecasting](../performance/baselines-and-capacity.md#capacity-forecasting) | DONE |
| Query comparison (period and plan) | G-28 | 0.13.4 | `perf_intel.compare_periods`, `compare_plans`; `query_plan_snapshots` (migration `0011`); explain `save_label` | unit + API tests (RBAC, audit) | [comparison](../performance/regressions-and-comparison.md#period-vs-period-comparison) | DONE |
| Recommendation validation | G-29 | 0.13.5 | `perf_intel.start_validation`, `complete_validation`; `recommendations.validation`; job `validate_recommendations`; Outcomes page | unit + API tests | [validation](../performance/regressions-and-comparison.md#recommendation-validation-before-after) | DONE |

## Milestone 0.14 — Reporting (DONE)

| Capability | Gap | Sprint | Code | Tests | Documentation | Status |
|---|---|---|---|---|---|---|
| Security-findings, query-statistics, regression and forecast reports; parameters; preview | G-32 | 0.14.1 | `backend/app/services/reports.py` (`ReportParams`, `REPORTS`, `produce`), `backend/app/api/v1/reports.py`, `frontend/src/features/reports/reports.tsx` | `tests/api/test_reports.py` (catalog, every report as CSV and PDF, parameters, RBAC scope, masking), `frontend/src/test/reports.test.tsx` | [reports](../user-guide/reports.md) | DONE |
| Scheduled reports by e-mail | G-30 | 0.14.2 | `app/services/report_schedules.py`, `app/models/reporting.py`, migration `0012`, job `report_scheduler`, `notify.send_file_email`, permission `reports.schedule`, `features/reports/schedules.tsx` | `tests/api/test_reports.py` (aiosmtpd delivery, owner disabled, scheduler job, validation), `tests/migrations`, `frontend/src/test/reports.test.tsx` | [scheduled reports](../user-guide/reports.md#scheduled-reports) | DONE |
| Server-side PDF | G-31 | 0.14.3 | `app/services/report_pdf.py` | `tests/unit/test_report_pdf.py`, `tests/api/test_reports.py` | [reports](../user-guide/reports.md#generating-a-report) | DONE |
| Branding and engine identity | — | 0.14.4 | `frontend/src/components/brand.tsx`, `engine-icon.tsx`, `lib/engines.ts`, `app/api/v1/branding.py`, `frontend/public/*` icons | `frontend/src/test/engine-icon.test.tsx`, `tests/api/test_branding.py` | [brand assets](../development/brand-assets.md) | DONE |

## Later milestones

| Capability | Gap | Sprint | Status |
|---|---|---|---|
| Helm, images, manifest CI, HA/TLS reference | G-41, G-42, G-44, G-45 | 0.15.x | NEXT |
| Scale, security, accessibility, upgrade drill, RC | G-54, G-58, G-22 | 1.0.x | PLANNED |
| Oracle TCPS validation | G-55 | Future | PLANNED |
| Db2 for z/OS validation and deep monitoring | G-56 | Future | BLOCKED |

Back to the [roadmap](roadmap.md).
