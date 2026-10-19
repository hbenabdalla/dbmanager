# Real-engine validation report

What DB Manager has been validated against: **real database servers from the vendors' official
container images**, driven by the integration suite (`backend/tests/integration/`) through
`scripts/integration-test.ps1` / `.sh`. Nothing here comes from mocks or the simulation. Results
also ship in `backend/app/data/real_engine_validation.json` (shown on **Platform → Engine support**).

Last full run: **2026-10-19 20:31 UTC** (pytest JUnit timestamp; matrix written 20:32:49 UTC), release 0.12.0 + the 0.13.0 fixes up to commit `92c9250`. Last full run: **2026-10-19 21:34 UTC** (pytest JUnit timestamp; matrix written 21:36:02 UTC) of release 0.13.0 (commit `43c48a3`). It includes the 0.13.0 real-engine checks: plan identity in Oracle and SQL Server query statistics, database and tablespace size series built from real storage data, MySQL restore with binary logging on, and the Db2 backup as a SYSMAINT operator. The previous run (2026-10-19 20:31 UTC, 84 passed, 1 failed — MySQL backup/restore, error 1419) is superseded.

## Status legend

| Status | Meaning |
|---|---|
| PASS | Exercised by at least one real-engine test, no failure |
| FAIL | A real-engine test failed in the run named below (see *Limitations* for the fix and its re-validation) |
| PARTIAL | Validated in part; what is not validated is named |
| NOT TESTED | No real-engine test exercised it in this environment — not claimed (reason given) |
| NOT APPLICABLE | The capability does not exist for this engine |
| EXTERNAL VALIDATION REQUIRED | Cannot be validated with containers (named external system needed) |

**Overall** is PASS when every exercised capability passed (the PARTIAL and NOT TESTED cells
are listed as limitations), FAIL when any test failed, PARTIAL when an essential capability is
validated only in part.

## Matrix

| Engine | Version | Connection | TLS | Inventory | Users | Roles | Privileges | Sessions | Locks | Blocking | Performance Metrics | Storage | Queries | Execution Plans | Replication | Security | Recommendations | Administration | Backup | Restore | Overall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| PostgreSQL | 16.15 | PASS | NOT TESTED | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS |
| MySQL | 8.4.11 | PASS | NOT TESTED | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS |
| MariaDB | 11.4.13 | PASS | NOT TESTED | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS |
| SQL Server | 16.0.4305.5 (2022) | PASS | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS |
| Oracle | 21.3.0.0.0 XE | PASS | NOT TESTED | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PARTIAL | PASS | PASS | PASS | PASS | PASS | PASS |
| Db2 LUW | 11.5.9.0 | PASS | NOT TESTED | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PASS | PARTIAL | PASS | PASS | PASS | PASS | EXTERNAL VALIDATION REQUIRED | PASS |
| Db2 for z/OS | — | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED |

Summary (as requested for the release report):

| Engine | Real Engine | Adapter | Monitoring | Queries | Security | Admin | Backup/Restore | Overall |
|---|---|---|---|---|---|---|---|---|
| PostgreSQL | PASS | PASS | PASS | PASS | PASS | PASS | PASS / PASS | PASS |
| MySQL | PASS | PASS | PASS | PASS | PASS | PASS | PASS / PASS | PASS |
| MariaDB | PASS | PASS | PASS | PASS | PASS | PASS | PASS / PASS | PASS |
| SQL Server | PASS | PASS | PASS | PASS | PASS | PASS | PASS / PASS | PASS |
| Oracle | PASS | PASS | PASS | PASS | PASS | PASS | PASS / PASS | PASS |
| Db2 LUW | PASS | PASS | PASS | PASS | PASS | PASS | PASS / EXTERNAL VALIDATION REQUIRED | PASS |
| Db2 for z/OS | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED | EXTERNAL VALIDATION REQUIRED |

## Run details

- **Environment:** Docker Desktop on Windows (WSL 2), `docker-compose.test.yml` via `scripts/integration-test.ps1`, the backend `it` image as the test runner
- **Result:** 86 tests: 85 passed, 0 failed, 1 skipped (Oracle TCPS)
- **Identity and secrets:** OpenLDAP (osixia/openldap 1.5.0): bind, search, groups, StartTLS — PASS; Keycloak 26.0.7: OIDC authorization code flow with PKCE — PASS; HashiCorp Vault 1.17.6: KV v2 with token and AppRole — PASS
- **Time-series:** VictoriaMetrics v1.122.0 suite not part of this run
- **Passwords:** generated per environment into `.env.test`; never in this report or the repository

## Per engine

### PostgreSQL

| | |
|---|---|
| Container image | `postgres:16-alpine` (with `pg_stat_statements`, `track_io_timing`) |
| Database version | 16.15 |
| Driver | asyncpg 0.32.0 |
| Client tools | `pg_dump` / `pg_restore` / `psql` 17 (PGDG `postgresql-client-17`) |
| Accounts | monitoring `dbm_it_monitor` (`pg_monitor`), administration `dbm_it_admin` (`CREATEDB CREATEROLE`, `pg_signal_backend`), backup `CREATEDB` + `pg_read_all_data`; no superuser |
| Tests | 13 executed: 13 passed, 0 failed, 0 skipped |

Limitations:

- TLS NOT TESTED: the image has no server certificate (`prefer` connected without TLS).
- Replication PARTIAL: status query validated on a standalone server; no streaming replica in the environment.
- Restore path for servers older than the client tools validated by forcing it (rendered script + `psql`).

### MySQL

| | |
|---|---|
| Container image | `mysql:8.4` (`--performance-schema=ON`; binary logging on by default) |
| Database version | 8.4.11 |
| Driver | PyMySQL 1.2.3 |
| Client tools | MariaDB client tools (`mysqldump` / `mysql` of Debian 12's MariaDB 10.11 client) in the backend image |
| Accounts | monitoring `dbm_it_monitor` (`PROCESS, SHOW DATABASES, SHOW VIEW`, `REPLICATION CLIENT`, `SHOW_ROUTINE`, `SELECT` on `performance_schema` and `mysql`), backup `SELECT, SHOW VIEW, TRIGGER, EVENT, LOCK TABLES`, restore schema-scoped DDL/DML; no `SUPER` |
| Tests | 13 executed: 13 passed, 0 failed, 0 skipped |

Limitations:

- Backup/restore with binary logging on validated: refusal without `log_bin_trust_function_creators`, then the documented prerequisite (not `SUPER`).
- TLS NOT TESTED: `prefer` mode; the suite does not assert an encrypted session.
- Replication PARTIAL: standalone server only.
- Dumps are made with the MariaDB client tools of the image; mount MySQL's own client tools for full fidelity (`DBM_BACKUP_MYSQL_BIN_DIR`).

### MariaDB

| | |
|---|---|
| Container image | `mariadb:11.4` (`--performance-schema=ON`) |
| Database version | 11.4.13-MariaDB |
| Driver | PyMySQL 1.2.3 |
| Client tools | `mariadb-dump` / `mariadb` (Debian 12) |
| Accounts | as MySQL, with `REPLICA MONITOR`; no `SUPER` |
| Tests | 13 executed: 13 passed, 0 failed, 0 skipped |

Limitations:

- TLS NOT TESTED: `prefer` mode, not asserted.
- Replication PARTIAL: standalone server only.
- Also validated outside the matrix against a local MariaDB 10.11 with and without binary logging.

### SQL Server

| | |
|---|---|
| Container image | `mcr.microsoft.com/mssql/server:2022-latest` |
| Database version | 16.0.4305.5 |
| Driver | pyodbc 5.3.0 + Microsoft ODBC Driver 18.7 for SQL Server |
| Accounts | monitoring `dbm_it_monitor` (`VIEW SERVER STATE`, `VIEW ANY DEFINITION`, `SHOWPLAN`), administration `ALTER ANY LOGIN, ALTER ANY CONNECTION` + `db_owner` of one database, backup `db_backupoperator`, restore `dbcreator`; no `sysadmin` |
| Tests | 14 executed: 14 passed, 0 failed, 0 skipped |

Limitations:

- TLS PARTIAL: encryption required (`require`), certificate verification not exercised (self-signed container certificate).
- Replication PARTIAL: Always On not available in the container (standalone).
- Query Store on and off both exercised.

### Oracle

| | |
|---|---|
| Container image | `container-registry.oracle.com/database/express:21.3.0-xe` (official Oracle registry; CDB `XE`, PDB `XEPDB1`) |
| Database version | 21.3.0.0.0 |
| Driver | python-oracledb 26.0.1 (thin mode) |
| Accounts | monitoring `DBM_IT_MONITOR` (`CREATE SESSION`, `SELECT_CATALOG_ROLE`) and common `C##DBM_IT_MONITOR`, administration without `DBA`, Data Pump backup with `DATAPUMP_EXP/IMP_FULL_DATABASE`; not `SYS`/`SYSTEM` |
| Tests | 15 executed: 14 passed, 0 failed, 1 skipped |

Limitations:

- TLS NOT TESTED: TCPS skipped — the XE image has no TCPS listener (`DBM_IT_ORACLE_TCPS_PORT` enables the test).
- Replication PARTIAL: Data Guard not available (standalone).
- Diagnostics Pack and Tuning Pack OFF: no AWR, ASH, ADDM, SQL Monitor or SQL Tuning Advisor queries.

### Db2 LUW

| | |
|---|---|
| Container image | `icr.io/db2_community/db2:11.5.9.0` (archive logging enabled by the setup) |
| Database version | DB2 v11.5.9.0 |
| Driver | ibm_db 3.3.0 |
| Accounts | monitoring `DBMMON` (`CONNECT`, `SQLADM`, `EXPLAIN`, …), administration `DBMADM` (`ACCESSCTRL`, `WLMADM`, `SQLADM`), read-only `DBMRO`, backup operator `DBMBK` (SYSMAINT via `SYSMAINT_GROUP`) |
| Tests | 14 executed: 14 passed, 0 failed, 0 skipped |

Limitations:

- Backup validated as the SYSMAINT backup operator `DBMBK` (not SYSADM).
- Restore EXTERNAL VALIDATION REQUIRED: a Db2 restore runs with the CLP on the Db2 host; DB Manager generates the command (checked by the test) and does not execute it.
- TLS NOT TESTED: the container has no SSL listener.
- Replication PARTIAL: HADR off (standalone).

### Db2 for z/OS

| | |
|---|---|
| Container image | none — Db2 for z/OS cannot run in a container |

Limitations:

- Validation needs a z/OS subsystem reached through Db2 Connect. Db2 LUW containers are never presented as z/OS validation. Backups are external tools (z/OS utilities).

## Not exercised or pending

- Oracle TCPS (needs a TCPS listener with a wallet) — FUTURE.
- Replica topologies (PostgreSQL streaming, MySQL/MariaDB replication, Always On, Data Guard, HADR): only standalone servers are in the test environment.
- TLS certificate verification (`verify-ca` / `verify-full`) against real engines.
- Active Directory (only OpenLDAP was available).
- Db2 for z/OS — EXTERNAL VALIDATION REQUIRED.

## How to reproduce

```powershell
.\scripts\integration-test.ps1            # everything (Windows, Docker Desktop)
.\scripts\integration-test.ps1 -K "backup"  # a subset (pytest -k expression)
```

```bash
./scripts/integration-test.sh               # Linux / macOS
```

Passwords are generated into `.env.test` (never committed) and are not part of this report.
Details: [integration tests](../operations/integration-tests.md); grants:
[database permissions](../database-permissions/index.md); defects found:
[real-engine findings](../troubleshooting/real-engine-findings.md).
