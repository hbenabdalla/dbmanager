# Database permissions validated on real engines

This page lists the **exact accounts the real-engine suite uses** (`backend/tests/integration/`),
so every grant below has been exercised against the engine image named in the
[real-engine validation report](../product/real-engine-validation.md). None of them is `root`,
`sa`/`sysadmin`, `SYS`, `SYSTEM`, `DBA` or `SYSADM`. The privileged account of each container
(`postgres`, `root`, `sa`, `SYSTEM`, `db2inst1`) is used **only to provision** the test objects and
these accounts.

The full per-engine reference, including the minimal inventory account and what each grant
enables, is in [Least-privilege accounts](../database-engines/permissions.md). Grants marked
*for EXPLAIN* are only needed to explain application SQL that reads those tables.

## PostgreSQL 16

| Purpose | Grants (validated) | Evidence |
|---|---|---|
| Monitoring | `LOGIN`, `pg_monitor`, `CONNECT` on the database; `USAGE` on schemas and `SELECT` on tables *for EXPLAIN* | `test_postgres_adapter.py` |
| Administration | `LOGIN CREATEDB CREATEROLE`, `pg_signal_backend`, `USAGE, CREATE` on the managed schema | `test_postgres_adapter.py` |
| Backup and restore | `LOGIN CREATEDB`, `pg_read_all_data` | `test_backup_engines.py::test_postgresql_pg_dump_round_trip` |

## MySQL 8.4 and MariaDB 11.4

| Purpose | Grants (validated) | Evidence |
|---|---|---|
| Monitoring | `PROCESS, SHOW DATABASES, SHOW VIEW` on `*.*`; `SELECT` on `performance_schema.*` and `mysql.*`; `REPLICATION CLIENT` (MySQL) / `REPLICA MONITOR` (MariaDB); `SHOW_ROUTINE` (MySQL 8.0.20+); `SELECT, TRIGGER` on the application schema *for EXPLAIN and trigger inventory* | `test_mysql_family.py` |
| Backup | `SELECT, SHOW VIEW, TRIGGER, EVENT, LOCK TABLES` on `*.*` | `test_backup_engines.py::test_mysql_family_dump_round_trip` |
| Restore (new databases) | `CREATE, DROP, ALTER, INDEX, INSERT, UPDATE, DELETE, CREATE VIEW, CREATE ROUTINE, ALTER ROUTINE, REFERENCES` on the target name pattern | same |

With binary logging on and `log_bin_trust_function_creators = OFF`, creating triggers and routines
also needs `SUPER` (error 1419). The suite validates the refusal *and* the documented
prerequisite (`log_bin_trust_function_creators = ON`), not a `SUPER` grant —
see [MySQL backups](../backup-restore/engines/mysql.md).

## SQL Server 2022

| Purpose | Grants (validated) | Evidence |
|---|---|---|
| Monitoring | `VIEW SERVER STATE`, `VIEW ANY DEFINITION`; database user with `SHOWPLAN`; `SELECT` on the application schema *for EXPLAIN* | `test_mssql.py` |
| Administration | `ALTER ANY LOGIN, ALTER ANY CONNECTION`; `db_owner` of the managed database only | `test_mssql.py` |
| Backup | database role `db_backupoperator` in the source database | `test_backup_engines.py::test_sqlserver_native_backup_verify_restore` |
| Restore | server role `dbcreator` | same |

## Oracle Database 21c (XE, PDB `XEPDB1`)

| Purpose | Grants (validated) | Evidence |
|---|---|---|
| Monitoring | `CREATE SESSION`, `SELECT_CATALOG_ROLE` (PDB-local user; a common `C##` user with `CONTAINER = ALL` for the CDB view); `READ` on application tables *for EXPLAIN* | `test_oracle.py` |
| Administration | `CREATE SESSION` (with admin option), `CREATE USER, ALTER USER, DROP USER, CREATE ROLE, GRANT ANY ROLE, ALTER SYSTEM, CREATE ANY INDEX`, `SELECT_CATALOG_ROLE` | `test_oracle.py` |
| Backup and restore (Data Pump) | `CREATE SESSION, CREATE TABLE`, tablespace quota, `DATAPUMP_EXP_FULL_DATABASE`, `DATAPUMP_IMP_FULL_DATABASE`, `READ, WRITE` on `DATA_PUMP_DIR` | `test_backup_engines.py::test_oracle_datapump_export_verify_import` |

Optional packs are never used: no AWR, ASH, ADDM, SQL Monitor or SQL Tuning Advisor queries
(Diagnostics Pack and Tuning Pack: OFF).

## Db2 LUW 11.5

| Purpose | Grants (validated) | Evidence |
|---|---|---|
| Monitoring (`DBMMON`) | `CONNECT`, `SQLADM`, `EXPLAIN`; `EXECUTE` on `SYSPROC.ENV_GET_SYSTEM_RESOURCES` and `SYSPROC.AUTH_LIST_AUTHORITIES_FOR_AUTHID`; `SELECT` on `SYSIBMADM.DBCFG` and `SYSIBMADM.DBMCFG`; DML on its own explain tables | `db2/custom/dbm-it-users.sh`, `test_db2.py` |
| Administration (`DBMADM`) | `CONNECT`, `ACCESSCTRL`, `WLMADM`, `SQLADM`; `CREATEIN, ALTERIN` on the managed schema | same |
| Read-only (`DBMRO`) | `CONNECT`, `SELECTIN` on the schema | same |
| Backup (`DBMBK`) | instance authority **SYSMAINT** through `SYSMAINT_GROUP`; `CONNECT`; `EXECUTE` on `SYSPROC.ADMIN_CMD`; `SELECT` on `SYSIBMADM.DB_HISTORY` | `test_backup_engines.py::test_db2_online_backup_and_recovery_history` |

Db2 identities are operating-system users of the Db2 host. Db2 for z/OS is not covered by these
containers: see [Db2 for z/OS](../database-engines/db2-zos.md) (*external validation required*).
