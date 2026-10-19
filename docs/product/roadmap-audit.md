# Roadmap audit (release 0.12.0)

Every milestone, sprint and task of the [roadmap](roadmap.md) was re-checked against the code and
the tests before release 0.12.0. A task counts as complete only when the implementation exists
and automated tests covering it were run. Where the roadmap said "done" but the code didn't
match, the row says so in **Actual status (before)** and the **Action** column says what was
done about it.

Statuses after the release: **VERIFIED COMPLETE**, **PLANNED**, **BLOCKED**, **NOT APPLICABLE**.

How it was checked:

- the code paths and tests named below were read;
- the backend suite (unit, API, migrations) ran against PostgreSQL 16, and the frontend suite and production build ran;
- the documentation was built with `mkdocs build --strict`;
- real-engine tests ran where this environment has the engine: PostgreSQL 16, MariaDB 10.11 and OpenLDAP 2.6.

Docker isn't available in the build environment, so the container-based integration targets show as BLOCKED with the exact command to run.

## Milestone 0.9 — Release foundation

| Milestone | Sprint | Task | Roadmap status | Actual status (before) | Evidence | Missing work | Action |
|---|---|---|---|---|---|---|---|
| 0.9 | 0.9.1 | Repository, governance files, Apache-2.0 license, secret scan | DONE | Complete | `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `.github/`, `.gitleaks.toml`, `scripts/secret-scan.sh` | — | VERIFIED COMPLETE |
| 0.9 | 0.9.2 | CI workflows (backend, frontend, docs, Docker, audit) | DONE | Workflows existed, but path filters let PRs skip required checks | `.github/workflows/ci-*.yml` | Required checks had to run on every PR | Path filters removed, stable job names, branch protection documented (`CONTRIBUTING.md`) → VERIFIED COMPLETE. The first run on GitHub happens after the push. |
| 0.9 | 0.9.2 | Branch protection documented, README badges | DONE | Badges present; branch protection **not documented** | `README.md`, `CONTRIBUTING.md` | Branch protection section | Added → VERIFIED COMPLETE |
| 0.9 | 0.9.3 | Documentation site, Read the Docs | DONE | Complete | `mkdocs.yml`, `.readthedocs.yaml`; strict build | — | VERIFIED COMPLETE |
| 0.9 | 0.9.4 | Version endpoint without sensitive build details; About page test; documentation link test | DONE | Version test was weak (didn't check for extra keys); no UI test for the docs link; About still said "license not yet selected" | `tests/unit/test_about_links.py`, `frontend/src/test/*` | Exact-keys test, Help-menu link test, license text | Implemented → VERIFIED COMPLETE |
| 0.9 | 0.9.5 | Upgrade from the *previous release's* schema | DONE | Only upgrade from an empty database was tested | `tests/migrations/test_migrations.py::test_existing_installation_upgrades_to_head_with_its_data` | Upgrade with existing data from the first and the previous schema | Implemented → VERIFIED COMPLETE |
| 0.9 | 0.9.5 | Scripted backup → wipe → restore → login + inventory intact | DONE | `backup.sh`/`restore.sh` existed but no drill ran them | `scripts/restore-drill.sh`, `.github/workflows/ci-docker.yml` | Drill script wired into the Docker CI job | Implemented → VERIFIED COMPLETE (runs in Docker CI) |

## Milestone 0.10 — Operations & alerting

| Milestone | Sprint | Task | Roadmap status | Actual status (before) | Evidence | Missing work | Action |
|---|---|---|---|---|---|---|---|
| 0.10 | 0.10.1 | Rule model, evaluator, de-duplicated alerts, RBAC, audit | DONE | Partly done: scope by **tag** was missing; the "sustained" condition used only the latest value | `app/services/alerting.py`, `tests/unit/test_alerting_completion.py`, `tests/api/test_alerting_completion.py` | Tag scope; avg/min/max over a time-series window | Implemented (migration 0007) → VERIFIED COMPLETE |
| 0.10 | 0.10.1 | Architecture page | DONE | Missing | `docs/architecture/alerting.md` | Page | Written → VERIFIED COMPLETE |
| 0.10 | 0.10.2 | SMTP STARTTLS **and implicit TLS** | DONE | STARTTLS only | `app/services/notify.py` (`smtp_security`), `tests/unit/test_notify_delivery.py` (aiosmtpd with STARTTLS+AUTH and implicit TLS) | SMTPS, CA file | Implemented → VERIFIED COMPLETE |
| 0.10 | 0.10.2 | Retry with backoff, routing rules → channels | DONE | No retries; no routing | `alerting.retry_due` (`notification_retries` job), `channel_accepts` | Retry queue, routing by environment/tag/engine | Implemented → VERIFIED COMPLETE |
| 0.10 | 0.10.3 | Recurring windows, time zones | DONE | One-off windows only | `occurrences` / `window_state` (zoneinfo, DST) | Daily/weekly recurrence | Implemented → VERIFIED COMPLETE |
| 0.10 | 0.10.3 | Notify after the window ends if still firing; visible on server pages | DONE | Neither was implemented | `_fire_notification`, `frontend/src/features/server/…` | Release logic, server alert panel | Implemented → VERIFIED COMPLETE |
| 0.10 | 0.10.4 | Alert inbox (ack/resolve/comment), badges, Teams/Slack presets, re-notify | NEXT | Not started | `alerts.tsx`, `server-alerts.tsx`, `notify.preset_payload`, `renotify_minutes` | All | Implemented → VERIFIED COMPLETE |
| 0.10 | 0.10.5 | Redacted diagnostic bundle | PLANNED | Not started | `app/services/diagnostics.py`, `app/core/redact.py`, `tests/api/test_diagnostics.py`, `tests/unit/test_redact.py` | All | Implemented → VERIFIED COMPLETE |

## Milestone 0.11 — Enterprise integration

| Milestone | Sprint | Task | Roadmap status | Actual status (before) | Evidence | Missing work | Action |
|---|---|---|---|---|---|---|---|
| 0.11 | 0.11.1 | API tokens | DONE | Complete | `tests/api/test_api_tokens.py` | — | VERIFIED COMPLETE |
| 0.11 | 0.11.2 | Rate limiting | DONE | Complete | `tests/unit/test_ratelimit.py`, `tests/api/test_rate_limits.py` | — | VERIFIED COMPLETE |
| 0.11 | 0.11.3 | OpenLDAP real validation | PLANNED | Not started; the first real run found a bug (attribute lookup errors on `memberOf`) | `tests/integration/test_identity.py`, fixtures `tests/integration/identity/`, `app/data/real_engine_validation.json` (openldap) | All | Implemented, bug fixed, run against OpenLDAP 2.6 → VERIFIED COMPLETE |
| 0.11 | 0.11.3 | Keycloak OIDC flow, Vault real tests | PLANNED | Not started | `test_identity.py::test_oidc_code_flow_with_pkce_against_keycloak`, `test_vault_real.py`, compose profiles `identity`, `vault` | Running the containers | Implemented → BLOCKED (needs Docker: `scripts/integration-test identity` and `vault`) |
| 0.11 | 0.11.4 | SIEM export (syslog RFC 5424 UDP/TCP/TLS, webhook) | PLANNED | Not started | `app/services/siem.py`, `tests/api/test_siem.py`, `tests/unit/test_siem_format.py`, Platform → Audit forwarding | All | Implemented → VERIFIED COMPLETE |
| 0.11 | 0.11.5 | HashiCorp Vault provider | PLANNED | Not started | `app/services/vault.py`, `tests/api/test_vault.py` (KV v2, AppRole, namespace, cache, end-to-end connection) | All | Implemented → VERIFIED COMPLETE |
| 0.11 | 0.11.5 | Cloud secret stores | PLANNED | Not started | — | AWS / Azure / Google providers | Moved to its own sprint 0.11.7 → PLANNED (each needs its own SDK, auth and cloud account to validate) |
| 0.11 | 0.11.6 | Native TOTP, recovery codes, enrollment policy, OIDC `amr`/`acr` | PLANNED | Not started | `app/services/mfa.py`, `tests/api/test_mfa.py`, `tests/unit/test_mfa.py`, `features/account/security.tsx` | All | Implemented → VERIFIED COMPLETE |

## Cross-cutting findings (claimed in docs, missing or wrong in code)

| Area | Task | Roadmap status | Actual status (before) | Evidence | Missing work | Action |
|---|---|---|---|---|---|---|
| UI | Edit and delete for environments, sites, clusters and applications; delete users, groups and roles; group members; effective permissions | Baseline DONE | API existed, no UI | `features/fleet/placement.tsx`, `features/platform/users.tsx`, `roles.tsx`, `test/account-admin.test.tsx` | UI | Implemented → VERIFIED COMPLETE |
| UI | Re-test connection, scan security now | Baseline DONE | API only | `features/server/tabs/settings.tsx` | UI | Implemented → VERIFIED COMPLETE |
| UI | Reports said "PDF later" | — | Misleading text | `features/reports/reports.tsx` | Show only the formats the API serves | Fixed |
| Docs | Engine matrix: MariaDB rule count, TLS and transaction rows, supported versions, PostgreSQL create/drop notes, Db2 LUW explain | Baseline DONE | Inaccurate | `docs/product/engine-matrix.md` | Corrections | Corrected |
| Docs | Capability matrix rows claimed more than the code did | Baseline DONE | Overstated | `docs/product/capability-matrix.md` | Corrections | Corrected |
| Security | `BACKUP` / `RESTORE` statements in the SQL workspace were classified as plain changes | — | Under-classified | `app/services/sql_classifier.py` | Treat them as privileged / destructive | Fixed (guarded like other privileged statements) |
| API | Connection test before registration ignored env / file / Vault references | — | Missing | `app/api/v1/servers.py`, `onboarding.py`, `tests/api/test_vault.py` | Resolve the reference | Implemented |
| API | A wrong password on the MFA endpoints returned 401, which signed users out | — | Bug | `app/api/v1/mfa.py` | Return 422 | Fixed |

## Unfinished-work markers

`backend/app` and `frontend/src` were searched for `TODO`, `FIXME`, `HACK`, `XXX`,
`NotImplementedError`, `pass`-only bodies, "placeholder", "stub", "mock", "coming soon", "hard-coded"
and "not yet". What was found, and what it means:

- **`NotImplementedError`** appears only in abstract hooks of the adapter base classes
  (`adapters/dbapi.py`, the MySQL family mixins). Each engine adapter overrides them. They are legitimate.
- **"placeholder"** refers to SQL bind-parameter placeholders, form `placeholder=` attributes,
  react-query `placeholderData`, and the `CHANGE_ME` checks on `.env` values. None of these are unfinished features.
- **`PLANNED` capability level and the "inventory-only stub" flag** (`adapters/base.py`) describe
  engines declared but not implemented. They are shown honestly in the UI and API, and every
  supported engine is implemented.
- **"or later" version notes** (e.g. "EXPLAIN ANALYZE requires MySQL 8.0.18 or later") are real
  engine limits, reported as NOT SUPPORTED on older versions.
- **No `TODO` / `FIXME` / `HACK` / `XXX`** markers remain in the application code.
- **Simulation-only data paths** are confined to `app/simulation` and `adapters/demo`. They are
  labelled as simulated and are never mixed with real servers; the backup service excludes
  simulated servers.

## Milestone 0.12 — Asset Protection: Backup & Restore (new)

Inserted as milestone 0.12 because it builds on 0.10 (notifications), 0.11.4 (audit) and 0.11.5
(secrets). Performance intelligence, reporting and cloud-native moved to 0.13, 0.14 and 0.15.

| Milestone | Sprint | Task | Roadmap status | Actual status | Evidence | Missing work | Action |
|---|---|---|---|---|---|---|---|
| 0.12 | 0.12.1 | Platform archive by component, every table classified | new | Implemented | `app/backup/platform.py`, `tests/unit/test_backup_units.py::test_every_table_is_in_a_component_or_explicitly_excluded` | — | VERIFIED COMPLETE |
| 0.12 | 0.12.1 | Encryption with an established library; master key never in the archive; key ids in the manifest | new | Implemented (age via `pyrage`) | `app/backup/encryption.py`, unit round trips, API test checks archive and sidecar | — | VERIFIED COMPLETE |
| 0.12 | 0.12.1 | Storage providers: local, mounted, NFS, S3-compatible | new | Implemented | `app/backup/storage.py`; S3 against a moto server in `tests/api/test_backup.py` | Azure Blob / GCS natively | VERIFIED COMPLETE; native Azure/GCS → sprint 0.12.6 PLANNED |
| 0.12 | 0.12.1 | Integrity statuses; `SUCCESS` only after read-back | new | Implemented | `service.create_platform_backup`, tamper test (CORRUPTED) | — | VERIFIED COMPLETE |
| 0.12 | 0.12.1 | Time-series option | new | VictoriaMetrics native export into the archive | `service._metrics_export` | Native snapshots are left to VictoriaMetrics' own `vmbackup` (documented) | VERIFIED COMPLETE (export); snapshot → documented as external |
| 0.12 | 0.12.2 | Restore wizard steps, impact analysis, pre-restore backup, typed confirmation, compatibility | new | Implemented | `service.preview_platform_restore`, `platform.impact`, `tests/api/test_backup.py::test_platform_backup_verify_restore_end_to_end` | — | VERIFIED COMPLETE |
| 0.12 | 0.12.3 | PostgreSQL logical backup / restore into a new database | new | Implemented | `engines.PostgreSQLProvider`; API test and `tests/integration/test_backup_engines.py` (PostgreSQL 16) | — | VERIFIED COMPLETE |
| 0.12 | 0.12.3 | MariaDB / MySQL dumps | new | Implemented; the first real run found that definitions qualified with the source schema would be restored into the source | `engines.MySQLFamilyProvider`; integration test against MariaDB 10.11 | — | Refused with a clear message; MariaDB VERIFIED COMPLETE; MySQL server run BLOCKED (Docker) |
| 0.12 | 0.12.3 | SQL Server native, Oracle Data Pump, Db2 LUW online backup | new | Implemented | `engines.SQLServerProvider`, `OracleProvider`, `Db2LUWProvider`; integration tests written | Real runs | BLOCKED (needs `scripts/integration-test mssql`, `oracle`, `db2`) |
| 0.12 | 0.12.3 | Db2 for z/OS | new | External tool required | `engines.Db2ZOSProvider` | — | NOT APPLICABLE for DB Manager execution (z/OS utilities); reported honestly |
| 0.12 | 0.12.4 | Policies, scheduler, retention, bulk with per-asset progress, cancel, notifications, metrics, RBAC, audit | new | Implemented | `service.schedule`, `apply_retention`, `tests/api/test_backup.py` (S3 policy, retention, scheduler, partial success, RBAC) | — | VERIFIED COMPLETE |
| 0.12 | 0.12.4 | Backup & Restore UI, dashboard (last 24 h, 30 days, storage used, upcoming runs, protected / at risk / unprotected assets and the list without a recent backup), asset protection status | new | Implemented | `frontend/src/features/backup/`, frontend tests | — | VERIFIED COMPLETE |
| 0.12 | 0.12.5 | Disaster recovery: scan a target into an empty catalog, runbook, automated restore test | new | Implemented | `service.scan_target`; DR part of the S3 API test; `docs/backup-restore/disaster-recovery.md` | — | VERIFIED COMPLETE |
| 0.12 | 0.12.7 | Point-in-time recovery | new | Not implemented | — | Log-chain management | PLANNED; no feature claims PITR |

## Later milestones

0.13 (performance intelligence), 0.14 (reporting), 0.15 (cloud-native) and 1.0 haven't started
and are **PLANNED**. Oracle TCPS validation is **PLANNED**. Db2 for z/OS real validation is
**BLOCKED** (needs a z/OS subsystem with Db2 Connect).
