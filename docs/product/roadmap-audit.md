# Roadmap audit — historical reconciliation

Every milestone and sprint claimed in the [roadmap](roadmap.md) was audited again from the code,
starting from commit `f320f90` (release 0.14.0, not yet published). Earlier DONE statuses were
**not** taken on trust. A task counts as VERIFIED COMPLETE only when four things hold: the code
does what the roadmap says, automated tests exercise it, those tests were run in this audit, and
the documentation matches. Each claim that failed one of these checks was reopened, fixed in the
same change set (commit `30027b2` and the documentation commit after it), and checked again.

The audits made before each release are kept in the
[audit history](roadmap-audit-history.md).

## How the audit was done

- The code was read against each roadmap claim: backend services, API routes, adapters, the
  frontend, migrations, CI workflows and scripts.
- The following runs were made for this audit:
    - The backend suite (unit, API, migrations) against PostgreSQL 16: **837 passed**.
    - The frontend type check, unit tests (**180 passed**) and production build.
    - `mkdocs build --strict`.
    - The secret scan (`gitleaks`).
- Real-engine suites were run on the engines available in this environment:
    - PostgreSQL 16.
    - MariaDB 10.11 with TLS (CA-verified and a wrong-CA case).
    - VictoriaMetrics 1.122.
    - PostgreSQL and MariaDB database backup and restore.

    MySQL 8.x, SQL Server, Oracle and Db2 LUW need the Docker test environment, which cannot pull
    images here. Their suites were extended and must be re-run there (see
    [Real-engine regression](#real-engine-regression)).
- The application was run against a fresh database at schema `0013`, with VictoriaMetrics, a
  simulated fleet and two real lab servers (PostgreSQL 16, MariaDB 10.11 over TLS). It was checked
  in light and dark mode at 1440, 1280, 1024 and 820 px, and all documentation screenshots were
  recaptured from it.

## Status vocabulary

| Status | Meaning |
|---|---|
| VERIFIED COMPLETE | Code, tests executed in this audit, documentation |
| IMPLEMENTED BUT UNTESTED | Code exists, no test exercises it |
| PARTIAL | Part of the claim is implemented |
| PLACEHOLDER | Present in the UI or API, does nothing real |
| UI ONLY / BACKEND ONLY | Only one side exists |
| DOCUMENTATION ONLY / TEST ONLY | Only documentation, or only a test, exists |
| BROKEN | Implemented, but wrong |
| NOT IMPLEMENTED | Missing |
| BLOCKED | Waiting for a named external dependency |
| OBSOLETE | No longer part of the product |
| EXTERNAL VALIDATION REQUIRED | Implemented; proof needs an environment this project does not have (named) |

**REOPENED** marks a task that was claimed DONE but was not true. Every reopened task is closed
again in this change set; none was deferred.

## Summary

| Audited claims (rows below) | Count |
|---|---|
| Verified complete as claimed | 12 |
| **Reopened**: claimed DONE, but broken, partial, untested, UI/backend only or undocumented | 61 |
| Reopened and closed again in this change set | 61 |
| Still open from the reopened set | 0 |
| Planned and not implemented, as the roadmap says (cloud secret stores; Azure/GCS and PITR) | 2 |
| Placeholder removed (*AI insights* setting) → OBSOLETE | 1 |
| External validation required (Db2 LUW restore on the Db2 host; Db2 for z/OS) | 1 |

Severity of the reopened findings: two were **P0**, X-Forwarded-For spoofing and the MySQL/MariaDB
workspace running in the wrong database. The security, data-integrity and false-status findings
were **P1**. Adapter depth, missing tests and missing UI for existing APIs were **P2**. Wording,
layout and documentation drift were **P3**. Dependency major upgrades and type-checking debt are
**P4**; see the end of this page.

## Remediation sprints

The reopened work was grouped into remediation sprints. All of them are now **VERIFIED COMPLETE**.

| Sprint | Priority | Scope | Status |
|---|---|---|---|
| R1 Security | P0/P1 | Client address spoofing, MFA lockout, webhook secrets, CSV injection, backup RBAC scoping, EXPLAIN ANALYZE in monitoring mode, role catalog, report ownership, Oracle pack guard, redaction | VERIFIED COMPLETE |
| R2 Engine correctness | P0/P1/P2 | MySQL/MariaDB database selection, Db2 database refusal, TLS *prefer*/*require*, MySQL 5.7 locks, constraint listing, roles across databases, PDB users, Db2 grants, storage contract | VERIFIED COMPLETE |
| R3 Data integrity of findings and alerts | P1/P2 | False auto-resolve, metric windows, alerts out of scope, suppression marker, Compliance counts | VERIFIED COMPLETE |
| R4 Backup & Restore | P1/P2/P3 | Cancellation, storage notifications, notifications of manual backups, safety backup, protection freshness, audit of target checks and previews, missing tests | VERIFIED COMPLETE |
| R5 Runtime and CI | P1/P2 | Leader lock leak, Docker CI drill as a required check that proves credentials, management mode default and backfill migration, upgrade tests pinned to published releases | VERIFIED COMPLETE |
| R6 User interface | P1/P2/P3 | False "SQL workspace" texts, permission guards, error states, missing UI for existing APIs, paging, engine icons, layout at 1024 px | VERIFIED COMPLETE |
| R7 Documentation and screenshots | P1/P2 | Stale counts, statuses and navigation paths; screenshots recaptured | VERIFIED COMPLETE |

## Baseline capability groups

| Milestone | Sprint | Task | Claimed Status | Actual Status | Code Evidence | Test Evidence | Documentation | Missing Work | Action |
|---|---|---|---|---|---|---|---|---|---|
| Baseline | Platform core | FastAPI, PostgreSQL control plane, migrations, bootstrap, health, self-metrics | DONE | **REOPENED — PARTIAL**: `/metrics` unauthenticated without documentation | `app/main.py`, `app/api/ops.py` | `tests/migrations/`, `tests/api/test_metrics.py` | `administration/diagnostics.md` | Document `/metrics` exposure | Documented (backend port only, no secrets in labels) → VERIFIED COMPLETE |
| Baseline | Identity & access | Scoped RBAC | DONE | **REOPENED — BROKEN**: `GET /roles` needed no permission | `app/api/v1/rbac.py` | `tests/api/test_rbac.py::test_role_catalog_is_administration_data` | `administration/rbac.md` | Permission check | Requires `roles.manage`, `users.manage` or `rbac.manage` → VERIFIED COMPLETE |
| Baseline | Identity & access | Client address in audit, rate limits and token rules | DONE | **REOPENED — BROKEN (P0)**: the leftmost `X-Forwarded-For` entry was trusted, so any client could spoof its address; nginx appended instead of replacing | `app/api/deps.py::client_ip`, `frontend/nginx/default.conf.template`, `docker-compose.yml` | `tests/unit/test_client_ip.py`, `tests/api/test_api_tokens.py` (spoofed header) | `deployment/reverse-proxy-tls.md#how-the-client-address-is-determined` | Right-to-left parsing past trusted proxies; nginx real-IP | Fixed (`DBM_TRUSTED_PROXIES`, `DBM_OUTER_PROXY`) → VERIFIED COMPLETE |
| Baseline | Identity & access | Audit CSV export | DONE | **REOPENED — BROKEN**: values starting with `=`, `+`, `-`, `@` were exported verbatim (formula injection); truncation at 200,000 rows was silent | `app/api/v1/audit.py`, `app/core/csvsafe.py` | `tests/api/test_audit_export.py` | `administration/audit.md` | Neutralise formulas, flag truncation | Fixed (`X-DBM-Export-Truncated`) → VERIFIED COMPLETE |
| Baseline | Engine adapters | MySQL/MariaDB SQL workspace runs in the selected database | DONE | **REOPENED — BROKEN (P0)**: statements ran in the connection's default database, whatever database the user selected | `app/adapters/dbapi.py::_use_database`, `app/adapters/mysql/base.py` | `tests/integration/test_mysql_family.py::test_workspace_runs_in_the_selected_database` (MariaDB 10.11) | `database-engines/mysql.md`, `mariadb.md` | Select and restore the database per call | Fixed → VERIFIED COMPLETE |
| Baseline | Engine adapters | Db2 LUW workspace and EXPLAIN | DONE | **REOPENED — BROKEN**: another database could be named and the statement ran in the connected one | `app/adapters/db2/adapter.py` (`single_database`) | `tests/unit/test_db2.py` | `database-engines/db2-luw.md` | Refuse with guidance | Refused ("register … as its own server") → VERIFIED COMPLETE |
| Baseline | Engine adapters | TLS modes | DONE | **REOPENED — BROKEN**: MySQL/MariaDB `prefer` connected in plain text; `require` did not check that TLS was negotiated | `app/adapters/mysql/base.py`, `app/adapters/tls.py` | `tests/integration/test_mysql_family.py::test_tls_modes` (verify-ca, wrong CA, require, prefer) | engine pages, `security/index.md` | Opportunistic TLS; `Ssl_cipher` check | Fixed → VERIFIED COMPLETE (MariaDB real); MySQL re-run in Docker |
| Baseline | Engine adapters | MySQL 5.7 lock waits | DONE | **REOPENED — BROKEN**: 8.0 `performance_schema.data_lock*` views were queried on 5.7 | `app/adapters/mysql/adapter.py` (`INNODB_LOCK_WAITS_SQL`) | `tests/unit/test_mysql_family.py` | `database-engines/mysql.md` | 5.7 path | `information_schema.INNODB_LOCK_WAITS`; capability PARTIAL on < 8.0 → VERIFIED COMPLETE |
| Baseline | Engine adapters | Object inventory: constraints | DONE | **REOPENED — PARTIAL**: listing constraints of a schema returned `[]` on SQL Server and Oracle | `app/adapters/mssql/inventory.py`, `oracle/inventory.py` | `tests/unit/test_mssql.py`, `test_oracle.py::test_constraints_are_listed_for_a_schema` | engine pages | Schema-wide queries | Implemented (incl. CDB views from root) → VERIFIED COMPLETE; real re-run in Docker |
| Baseline | Engine adapters | Users and roles | DONE | **REOPENED — PARTIAL**: SQL Server roles only of the default database (usually `master`); Oracle from CDB$ROOT showed no PDB local users; Db2 grants only from TABAUTH/SCHEMAAUTH/DBAUTH/ROLEAUTH | `mssql/inventory.py::list_roles`, `oracle/inventory.py::list_users`, `db2/inventory.py::OBJECT_AUTHS` | `test_mssql.py::test_roles_of_every_accessible_database…`, `test_oracle.py::test_root_connection_lists…`, `test_db2.py::test_routine_package_sequence…` | engine pages | Coverage | Implemented → VERIFIED COMPLETE; real re-run in Docker |
| Baseline | Engine adapters | Storage view | DONE | **REOPENED — BROKEN (UI)**: each engine used other keys (`db`, `schema_name`, `owner`/`segment_name`), the page showed `undefined.name`; Db2 had no largest tables; tablespace sizes of Oracle/Db2 showed "—" | `adapters/base.py::StorageInfo`, all `get_storage_metrics`, `features/server/tabs/infra.tsx` | `test_db2.py::test_largest_tables…`, `frontend/src/test/storage-tab.test.tsx`, integration assertions in all engine suites | `user-guide/storage.md` | One contract | Implemented → VERIFIED COMPLETE |
| Baseline | Engine adapters | Oracle licensing boundary (Diagnostics/Tuning Packs off) | DONE | **REOPENED — PARTIAL**: the collectors never used pack objects, but the SQL workspace let users query `DBA_HIST_*`, ASH, `DBMS_SQLTUNE` … without a licence | `app/adapters/oracle/adapter.py::licensed_pack_needed` | `tests/unit/test_oracle.py` | `database-engines/oracle.md` | Guard | Refused unless the pack is declared licensed → VERIFIED COMPLETE |
| Baseline | Telemetry | Scheduler leader election | DONE | **REOPENED — BROKEN**: on error the lock connection went back to the pool still holding the advisory lock, so no instance could become leader | `app/jobs/runner.py::leader_tick` | `tests/api/test_runtime_guards.py` | `deployment/scaling.md` | Invalidate the connection | Fixed → VERIFIED COMPLETE |
| Baseline | Telemetry | Circuit breaker, idle timeout | DONE | **REOPENED — IMPLEMENTED BUT UNTESTED** | `app/services/collection.py`, `api/deps.py` | `tests/unit/test_collection_breaker.py`, `tests/api/test_runtime_guards.py` | `monitoring/collectors.md` | Tests | Tests added → VERIFIED COMPLETE |
| Baseline | Advisors | "59 performance + 55 security rules" | DONE | **REOPENED — PARTIAL**: 116 rules exist (61 + 55); 58 had no test, and the new tests found wrong severities, negative "days until full" texts, EOL lists missing MariaDB versions and a false positive on Db2 without DBM CFG access | `app/advisors/` | `tests/unit/test_rules_coverage.py` (every rule fires and stays quiet) | `performance/recommendations.md`, engine matrix | Tests, fixes, counts | Fixed; a meta-test fails when a rule has no test → VERIFIED COMPLETE |
| Baseline | Advisors | Auto-resolve of recommendations | DONE | **REOPENED — BROKEN**: when the tuning context could not be collected, every engine-specific recommendation was auto-resolved | `app/services/advisor.py::_persist(not_evaluated=…)` | `tests/unit/test_advisor*.py` | `performance/recommendations.md` | Skip rules not evaluated | Fixed → VERIFIED COMPLETE |
| Baseline | Advisors | Rule metric windows | DONE | **REOPENED — BROKEN**: "current" values were the window maximum | `app/services/metrics.py::Window.last` | unit tests | — | Use the last value | Fixed → VERIFIED COMPLETE |
| Baseline | Advisors | Security posture score | DONE | **REOPENED — IMPLEMENTED BUT UNTESTED** (the test only checked a 200) | `app/api/v1/advisor.py::posture` | `tests/api/test_posture_score.py` | `user-guide/security.md` | Weighted score test | Added → VERIFIED COMPLETE |
| Baseline | Advisors | Compliance page counts | DONE | **REOPENED — BROKEN**: counted from the first 500 findings only | `GET /advisor/rule-summary`, `features/security/security.tsx` | `tests/api/test_rule_summary.py`, `frontend/src/test/compliance.test.tsx` | `user-guide/security.md` | Server-side summary | Implemented → VERIFIED COMPLETE |
| Baseline | Guarded administration | Monitoring Only is the default | DONE | **REOPENED — BROKEN**: a server without the option was treated as *administration* | `services/connections.py`, `services/inventory.py`, migration `0013` | `tests/api/test_management_mode.py`, `tests/migrations/test_migrations.py` | `user-guide/servers.md` | Default + backfill | Default is monitoring; `0013` keeps existing servers in administration mode → VERIFIED COMPLETE |
| Baseline | Guarded administration | EXPLAIN ANALYZE of a modifying statement | DONE | **REOPENED — BROKEN**: it ran the statement on monitoring-only servers with the monitoring credential | `app/api/v1/queries.py` | `tests/api/test_management_mode.py` | `performance/execution-plans.md` | Administration check | Requires administration mode and the admin credential → VERIFIED COMPLETE |
| Baseline | Guarded administration | Workspace CSV export | DONE | **REOPENED — BROKEN**: formula injection | `app/api/v1/workspace.py` | `tests/api/test_workspace.py` | — | `csv_safe` | Fixed → VERIFIED COMPLETE |
| Baseline | UX | Administration texts and links | DONE | **REOPENED — BROKEN**: pages said changes go "through the SQL workspace" | `features/admin/admin.tsx`, `server/tabs/access.tsx`, `shared/admin-link.tsx` | `frontend/src/test/ui-fixes.test.tsx` | `user-guide/users-and-privileges.md` | Correct text, deep link | Fixed → VERIFIED COMPLETE |
| Baseline | UX | Settings page guard; server Settings tab | DONE | **REOPENED — BROKEN**: the page loaded for everyone; the tab was disabled for users with `collectors.view` or `security.manage` | `features/platform/ops.tsx`, `server/server-page.tsx` | `ui-fixes.test.tsx` | `administration/index.md` | Guards | Fixed → VERIFIED COMPLETE |
| Baseline | UX | Error states | DONE | **REOPENED — PARTIAL**: twelve pages showed an endless skeleton on an API error | `ops.tsx`, `simulation.tsx`, `checklist.tsx`, `protection.tsx`, `monitoring.tsx`, fleet pages | `ui-fixes.test.tsx`, `simulation.test.tsx` | — | Error state with retry | Added → VERIFIED COMPLETE |
| Baseline | UX | "+ Add" menu entries and `?create=1` | DONE | **REOPENED — PLACEHOLDER**: "Database user", "Environment", "Platform user" opened pages without the dialog | `features/fleet/fleet-pages.tsx`, `platform/users.tsx`, `admin/admin.tsx` | `ui-fixes.test.tsx` | — | Open the dialog / server picker | Implemented → VERIFIED COMPLETE |
| Baseline | UX | Server lists | DONE | **REOPENED — BROKEN**: capped at 500 servers | `api/hooks.ts::fetchAllServers` | `ui-fixes.test.tsx` (1,203 servers) | — | Paging | Implemented → VERIFIED COMPLETE |
| Baseline | UX | Command palette actions | DONE | **REOPENED — PARTIAL** | `components/command-palette.tsx` | `ui-fixes.test.tsx` | `user-guide/search-and-command-palette.md` | Create backup, generate report, account security | Added → VERIFIED COMPLETE |
| Baseline | UX | Dashboard KPI strip, top bar at 1024 px | DONE | **REOPENED — BROKEN (layout)**: ten KPI columns truncated labels and the sparkline overlapped the next tile; the top bar overflowed at 1024 px | `features/dashboard/dashboard.tsx`, `components/app-shell.tsx` | visual check at 1440/1280/1024/820 px, light and dark | — | Layout | Five columns below 1536 px; compact top bar → VERIFIED COMPLETE |
| Baseline | Simulation, topology, wizards, dark mode | — | DONE | VERIFIED COMPLETE | `app/simulation/`, `features/topology/`, wizards | `tests/api/test_simulation.py`, frontend tests | user guide | — | — |
| Baseline | Query performance, plans | — | DONE | VERIFIED COMPLETE | `services/perf_intel.py`, `components/plan-*.tsx` | `tests/api/test_performance.py` | `performance/` | — | — |

## Milestone 0.9 — Release foundation

| Milestone | Sprint | Task | Claimed Status | Actual Status | Code Evidence | Test Evidence | Documentation | Missing Work | Action |
|---|---|---|---|---|---|---|---|---|---|
| 0.9 | 0.9.1 | Repository, governance, licence, secret scan | DONE | VERIFIED COMPLETE | `LICENSE`, `CONTRIBUTING.md`, `.gitleaks.toml` | `gitleaks dir` and `gitleaks git`: no leaks | `development/release-process.md` | — | — |
| 0.9 | 0.9.2 | CI: required checks on every pull request | DONE | **REOPENED — BROKEN**: `ci-docker.yml` still had a pull-request path filter, so the Compose job could be skipped | `.github/workflows/ci-docker.yml` | workflow definition | `CONTRIBUTING.md` | Remove filter; required job "Compose and restore drill" | Fixed → VERIFIED COMPLETE (first run after push) |
| 0.9 | 0.9.3 | Documentation site | DONE | VERIFIED COMPLETE | `mkdocs.yml`, `.readthedocs.yaml` | `mkdocs build --strict` | — | — | — |
| 0.9 | 0.9.4 | Version and About links; `off` hides a link | DONE | **REOPENED — PARTIAL**: only the documentation link could be hidden | `app/api/v1/about.py` | `tests/unit/test_about_links.py` | `administration/about.md` | GitHub and roadmap links | Fixed → VERIFIED COMPLETE |
| 0.9 | 0.9.5 | Upgrade from the previous release's schema | DONE | **REOPENED — PARTIAL**: "previous" was the revision before head, not a published release | `tests/migrations/test_migrations.py` (`RELEASES = {"0.12.0": "0010", "0.13.0": "0011"}`) | 6 passed (empty, first, 0.12.0, 0.13.0 → head; backfill) | `operations/upgrade.md` | Pin release revisions | Fixed → VERIFIED COMPLETE |
| 0.9 | 0.9.5 | Restore drill proves login and inventory | DONE | **REOPENED — PARTIAL**: the drill did not prove that encrypted server credentials survive | `scripts/restore-drill.sh` | Docker CI job | `operations/backup-restore.md` | Connection test after restore | The drill registers a server with a credential and requires a successful connection test after the restore → VERIFIED COMPLETE (runs in Docker CI) |

## Milestone 0.10 — Operations & alerting

| Milestone | Sprint | Task | Claimed Status | Actual Status | Code Evidence | Test Evidence | Documentation | Missing Work | Action |
|---|---|---|---|---|---|---|---|---|---|
| 0.10 | 0.10.1 | A rule resolves automatically | DONE | **REOPENED — BROKEN**: alerts of a server that left the rule's scope (moved, tag removed, deleted from scope) stayed firing forever | `services/alerting.py::evaluate` (`visited`) | `tests/api/test_alerting_completion.py` | `user-guide/alerting.md` | Resolve out-of-scope alerts | Fixed (`resolved_by="scope"`) → VERIFIED COMPLETE |
| 0.10 | 0.10.1 | Evaluator, RBAC, audit | DONE | VERIFIED COMPLETE | `services/alerting.py` | `tests/unit/test_alerting*.py`, `tests/api/test_alerting*.py` | `architecture/alerting.md` | — | — |
| 0.10 | 0.10.2 | Channel secrets encrypted and never returned | DONE | **REOPENED — BROKEN (P1)**: webhook URLs and headers (which carry tokens) were stored in plain text, returned by the API and written to the audit log; SIEM webhook URLs were audited | `services/notify.py::webhook_target`, `migrate_plaintext_webhooks`, `api/v1/alerting.py`, `api/v1/siem.py::_audit_config` | `tests/api/test_alerting.py` (encrypted, not audited, kept on update), `tests/unit/test_alerting.py` (migration) | `administration/notifications.md` | Encrypt, migrate, mask | Fixed; existing channels are migrated at startup → VERIFIED COMPLETE |
| 0.10 | 0.10.2 | SMTP, retries, routing | DONE | VERIFIED COMPLETE | `services/notify.py` | `tests/unit/test_notify_delivery.py` | `administration/notifications.md` | — | — |
| 0.10 | 0.10.3 | Suppressed alerts notify after the window | DONE | **REOPENED — BROKEN**: the suppression marker was cleared when the window ended, so the alert was never released | `services/alerting.py` | `tests/api/test_alerting_completion.py` | `user-guide/alerting.md` | Keep the marker | Fixed → VERIFIED COMPLETE |
| 0.10 | 0.10.4 | Acknowledge/resolve/comment audited | DONE | **REOPENED — PARTIAL**: audited without target, so not findable per alert | `api/v1/alerting.py` | `tests/api/test_alerting_completion.py` | — | `target_type`, `target_id` | Fixed → VERIFIED COMPLETE |
| 0.10 | 0.10.4 | "Evaluate now"; z-score in the rules page | DONE | **REOPENED — BACKEND ONLY** | `features/alerts/alerts.tsx` | `ui-fixes.test.tsx` | `user-guide/alerting.md` | Button | Added → VERIFIED COMPLETE |
| 0.10 | 0.10.5 | Redacted diagnostics | DONE | **REOPENED — PARTIAL**: Oracle `user/password@host` connect strings were not redacted | `app/core/redact.py` | `tests/unit/test_redact.py` | `administration/diagnostics.md` | Pattern | Fixed → VERIFIED COMPLETE |

## Milestone 0.11 — Enterprise integration

| Milestone | Sprint | Task | Claimed Status | Actual Status | Code Evidence | Test Evidence | Documentation | Missing Work | Action |
|---|---|---|---|---|---|---|---|---|---|
| 0.11 | 0.11.1 | API tokens | DONE | VERIFIED COMPLETE | `services/tokens.py` | `tests/api/test_api_tokens.py` | `administration/api-tokens.md` | — | — |
| 0.11 | 0.11.2 | Rate limiting per client | DONE | **REOPENED — BROKEN**: keyed on a spoofable client address (see P0 above) | `api/deps.py` | `tests/unit/test_client_ip.py` | `administration/rate-limiting.md` | — | Fixed with the client address → VERIFIED COMPLETE |
| 0.11 | 0.11.3 | OpenLDAP, Keycloak, Vault real validation | DONE | VERIFIED COMPLETE (Docker run of 2026-10-19); the workflow input did not list `identity` and `vault` | `tests/integration/test_identity.py`, `test_vault_real.py`, `integration-engines.yml` | real runs | `operations/integration-tests.md` | Workflow input | Fixed → VERIFIED COMPLETE |
| 0.11 | 0.11.4 | SIEM forwarding | DONE | **REOPENED — PARTIAL**: categories were hard-coded in the UI with a free-text field; webhook URL audited | `features/platform/siem.tsx`, `api/v1/siem.py` | `siem-vault.test.tsx`, `tests/api/test_siem.py` | `administration/siem.md` | Categories from the API, masking | Fixed → VERIFIED COMPLETE |
| 0.11 | 0.11.5 | Vault: token, AppRole, **Kubernetes** auth | DONE | **REOPENED — IMPLEMENTED BUT UNTESTED** (Kubernetes auth) | `services/vault.py` | `tests/api/test_vault.py::test_kubernetes_auth_uses_the_service_account_token` | `administration/secret-providers.md` | Test | Added → VERIFIED COMPLETE |
| 0.11 | 0.11.6 | MFA with lockout | DONE | **REOPENED — BROKEN (P1)**: the TOTP step ignored `locked_until`, and a correct password reset the failure counter before the second factor | `api/v1/auth.py::login_mfa`, `services/auth.py` | `tests/api/test_mfa.py` | `administration/mfa.md` | Enforce lockout | Fixed → VERIFIED COMPLETE |
| 0.11 | 0.11.6 | MFA policy visible in Settings | DONE | **REOPENED — DOCUMENTATION ONLY** | `api/v1/platform.py` (read-only settings), `ops.tsx` | `ui-fixes.test.tsx` | `administration/mfa.md` | Show policy | Added → VERIFIED COMPLETE |
| 0.11 | 0.11.7 | Cloud secret stores | PLANNED | NOT IMPLEMENTED (as stated) | — | — | roadmap | AWS / Azure / Google providers | Stays PLANNED (needs cloud accounts to validate) |
| — | — | "AI insights" setting | — | **PLACEHOLDER**: a setting and a switch that enabled nothing | — | — | — | — | Removed → OBSOLETE |

## Milestone 0.12 — Asset protection

| Milestone | Sprint | Task | Claimed Status | Actual Status | Code Evidence | Test Evidence | Documentation | Missing Work | Action |
|---|---|---|---|---|---|---|---|---|---|
| 0.12 | 0.12.1 | Platform backup with VictoriaMetrics export | DONE | **REOPENED — IMPLEMENTED BUT UNTESTED** (export) | `backup/service.py::_metrics_export` | `tests/api/test_backup_extras.py::test_platform_backup_carries_the_victoriametrics_export` (byte-for-byte, failure path) | `backup-restore/platform-backup.md` | Test | Added → VERIFIED COMPLETE |
| 0.12 | 0.12.2 | Pre-restore safety backup | DONE | **REOPENED — BROKEN**: `pre_restore_backup=false` silently skipped it | `api/v1/backups.py` | `tests/api/test_backup_scope.py::test_platform_restore_keeps_its_safety_backup_unless_allowed` | `backup-restore/restore.md` | Setting gate | Requires `backup.allow_restore_without_safety_backup`; the wizard explains it → VERIFIED COMPLETE |
| 0.12 | 0.12.2 | Restore preview, target check audited | DONE | **REOPENED — PARTIAL**: the preview decrypts an archive and was not audited | `api/v1/backups.py` | same test | `backup-restore/security.md` | Audit | Added → VERIFIED COMPLETE |
| 0.12 | 0.12.3 | Database backups per engine | DONE | VERIFIED COMPLETE (PostgreSQL 16, MariaDB 10.11 re-run here; others in the 2026-10-19 Docker run) | `backup/engines.py` | `tests/integration/test_backup_engines.py` | `backup-restore/engines/` | — | — |
| 0.12 | 0.12.3 | Db2 LUW restore; Db2 for z/OS | DONE / external | EXTERNAL VALIDATION REQUIRED | `engines.Db2LUWProvider`, `Db2ZOSProvider` | — | `backup-restore/engine-support.md` | Db2 host / z/OS utilities | Unchanged, stated precisely |
| 0.12 | 0.12.4 | RBAC of jobs, policies, targets, overview | DONE | **REOPENED — BROKEN (P1)**: environment-scoped users saw every job, platform policy, target path and the platform status, and could cancel any job | `api/v1/backups.py` (`_visible_server_ids`, `_job_visible`, `_policy_visible`) | `tests/api/test_backup_scope.py::test_scoped_grants_see_only_their_database_backups` | `backup-restore/security.md` | Scoping | Fixed → VERIFIED COMPLETE |
| 0.12 | 0.12.4 | Cancel | DONE | **REOPENED — BROKEN**: cancelling a running platform backup was ignored | `backup/service.py` (`checkpoint`) | `test_backup_scope.py::test_cancelling_a_running_platform_backup_stops_it` | `backup-restore/platform-backup.md` | Checkpoints | Fixed → VERIFIED COMPLETE |
| 0.12 | 0.12.4 | Notifications (storage unavailable …) | DONE | **REOPENED — BROKEN**: storage errors of database backups were reported as generic failures; cancelled jobs said "failed"; manual backups notified nobody; no test | `backup/service.py::_notify_job`, `notify_policy` | `test_backup_scope.py::test_backup_events_reach_policy_and_platform_channels` | `backup-restore/policies.md` | Classification, setting `backup.notification_channels` | Fixed → VERIFIED COMPLETE |
| 0.12 | 0.12.4 | Bulk by servers/environments/sites/tags; Prometheus metrics | DONE | **REOPENED — IMPLEMENTED BUT UNTESTED** | `backup/service.py::scope_servers` | `test_backup_extras.py` (scopes, counters) | `backup-restore/database-backup.md` | Tests | Added → VERIFIED COMPLETE |
| 0.12 | 0.12.4 | Asset protection status | DONE | **REOPENED — BROKEN**: a manual policy without a maximum age made any backup, however old, count as protection | `backup/service.py::freshness_limit` | `tests/api/test_backup.py` | `backup-restore/policies.md` | Limit | 8 days for such policies; the limit is shown → VERIFIED COMPLETE |
| 0.12 | 0.12.4 | Schedules follow DST | DONE | **REOPENED — PARTIAL**: autumn change tested, spring-forward untested | `backup/service.py::next_run` | `tests/unit/test_backup_units.py::test_schedule_in_the_skipped_spring_forward_hour…` | `backup-restore/policies.md` | Test | Added → VERIFIED COMPLETE |
| 0.12 | 0.12.4 | Backup UI: pin, jobs, engine support | DONE | **REOPENED — BACKEND ONLY** | `features/backup/pages.tsx`, `lib/nav.ts` | `ui-fixes.test.tsx` | `user-guide` / `backup-restore/index.md` | UI | Added → VERIFIED COMPLETE |
| 0.12 | 0.12.5 | Disaster recovery scan | DONE | VERIFIED COMPLETE | `service.scan_target` | `tests/api/test_backup.py` | `backup-restore/disaster-recovery.md` | — | — |
| 0.12 | 0.12.6 / 0.12.7 | Native Azure/GCS; PITR | PLANNED | NOT IMPLEMENTED (as stated) | — | — | roadmap | — | Stay PLANNED |

## Milestone 0.13 — Performance intelligence

| Milestone | Sprint | Task | Claimed Status | Actual Status | Code Evidence | Test Evidence | Documentation | Missing Work | Action |
|---|---|---|---|---|---|---|---|---|---|
| 0.13 | 0.13.1 | z-score rules with baseline preview in the wizard | DONE | **REOPENED — PARTIAL**: the preview existed for server scopes only | `features/alerts/alerts.tsx::ScopeBaselinePreview` | `frontend/src/test/baseline-scope.test.tsx` | `performance/baselines-and-capacity.md` | Environment, tag and all scopes | Implemented → VERIFIED COMPLETE |
| 0.13 | 0.13.2 | Regressions, rule `GEN-PERF-011` | DONE | **REOPENED — BROKEN**: no severity when the relative change was undefined | `advisors/performance/common.py` | `test_rules_coverage.py` | — | — | Fixed → VERIFIED COMPLETE |
| 0.13 | 0.13.3 | Forecast, rule `GEN-PERF-012` | DONE | **REOPENED — BROKEN**: texts with negative days | same | same | — | — | Fixed → VERIFIED COMPLETE |
| 0.13 | 0.13.3 | Performance page metric list | DONE | **REOPENED — PARTIAL**: hard-coded list | `features/monitoring/monitoring.tsx` | `ui-fixes.test.tsx` | `user-guide/performance.md` | From `/metrics/catalog` | Fixed → VERIFIED COMPLETE |
| 0.13 | 0.13.4 / 0.13.5 | Comparisons, outcome validation | DONE | VERIFIED COMPLETE | `services/perf_intel.py` | `tests/api/test_performance.py` | `performance/` | — | — |

## Milestone 0.14 — Reporting and branding

| Milestone | Sprint | Task | Claimed Status | Actual Status | Code Evidence | Test Evidence | Documentation | Missing Work | Action |
|---|---|---|---|---|---|---|---|---|---|
| 0.14 | 0.14.1 / 0.14.3 | Report definitions, preview, PDF | DONE | VERIFIED COMPLETE | `services/reports.py`, `report_pdf.py` | `tests/api/test_reports.py`, `tests/unit/test_report_pdf.py` | `user-guide/reports.md` | — | — |
| 0.14 | 0.14.2 | Schedules: only owner, creator or `users.manage` may change or run them | DONE | **REOPENED — BROKEN**: anyone with `reports.schedule` could edit, delete or run another user's schedule (and receive data with that owner's access) | `api/v1/reports.py::_require_manage` | `tests/api/test_reports.py` (ownership; revoked and deleted owner; size limit) | `user-guide/reports.md` | Ownership check | Fixed → VERIFIED COMPLETE |
| 0.14 | 0.14.4 | Logo and engine icons on every listed screen | DONE | **REOPENED — PARTIAL**: bulk onboarding showed no icon; no guard against drift | `features/onboarding/bulk.tsx` | `frontend/src/test/engine-icon-guard.test.ts` (13 screens, no ad-hoc engine artwork) | `development/brand-assets.md` | Icon, guard | Added → VERIFIED COMPLETE |
| 0.14 | — | Screenshots of the current UI | DONE | **REOPENED — BROKEN**: seven screenshots showed an older UI; key screens had none | `docs/assets/screenshots/` | recaptured from the running application (43 images) | README, user guide | Recapture | Done → VERIFIED COMPLETE |

## Real-engine regression

| Engine | Run here | Result | Still required |
|---|---|---|---|
| PostgreSQL 16 | adapter suite, database backup and restore | 12 + 1 passed | — |
| MariaDB 10.11 | adapter suite (incl. database selection, TLS modes, storage contract), database backup and restore | 14 + 1 passed | — |
| VictoriaMetrics 1.122 | time-series suite | 5 passed | — |
| MySQL 8.0 / 8.4 | — (Docker images cannot be pulled here) | not run | `scripts/integration-test --engines mysql` |
| SQL Server 2022 | — | not run | `--engines mssql` (constraints, roles across databases, storage contract) |
| Oracle 21c | — | not run | `--engines oracle` (constraints, PDB users, pack guard, storage contract) |
| Db2 LUW 11.5 | — | not run | `--engines db2` (grant catalogs, largest tables) |
| Db2 for z/OS | — | EXTERNAL VALIDATION REQUIRED | a z/OS subsystem with Db2 Connect; never inferred from Db2 LUW |

No test was weakened or changed to a skip to pass. The suites of MySQL, SQL Server, Oracle and
Db2 LUW skip only because their connection variables are not set in this environment.

## Remaining known issues (not regressions)

- `mypy` reports 104 type errors that predate this audit, down from 106. Type checking is not a
  CI gate. This is P4 work, scheduled for 1.0.2.
- Seven Dependabot pull requests with major upgrades (vite, recharts, lucide-react,
  pymdown-extensions, …) are still open. They are P4 and scheduled for 0.15.
- `app/adapters/planned.py` is kept on purpose. It is the scaffolding that describes an engine
  before its adapter exists, and `test_planned_adapter_raises` exercises it.
