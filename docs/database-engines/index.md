# Database engines

Generated from the adapters' capability definitions (`static_capabilities`) with
`python -m app.cli matrix`. The same data drives **Platform → Engine support** in the UI
(`GET /api/v1/platform/engine-support`); nothing in the frontend hard-codes a status.

What a matrix cell means: the adapter **implements** the capability when the account has the
documented privileges (see the engine pages and [least-privilege accounts](permissions.md)). On a real server, capability discovery
refines every cell — for example *query statistics* becomes **REQUIRES CONFIGURATION** on a
MariaDB server with `performance_schema=OFF`, or **REQUIRES PRIVILEGE** on a SQL Server login
without `VIEW SERVER STATE`. A missing privilege limits that component only; the connection stays
healthy (see "Collection status" on the server overview).

Legend: **FULL** · **PARTIAL** · `—` not supported by the engine (or deliberately not offered) ·
**PRIV** requires privilege · **CONFIG** requires configuration · **EXT** requires an extension ·
**LICENSE** requires a vendor license · **EXTERNAL** requires external (z/OS) monitoring
infrastructure · **NOT IMPL** possible on the engine but not implemented in this release.

## Engine pages

| Engine | Page |
|---|---|
| PostgreSQL | [PostgreSQL](postgresql.md) |
| MySQL | [MySQL](mysql.md) |
| MariaDB | [MariaDB](mariadb.md) |
| Microsoft SQL Server | [SQL Server](sql-server.md) |
| Oracle Database | [Oracle](oracle.md) |
| IBM Db2 LUW | [Db2 LUW](db2-luw.md) |
| IBM Db2 for z/OS | [Db2 for z/OS](db2-zos.md) |

## Implementation and validation status

| Engine | Adapter (package) | Driver | Support | Validation |
|---|---|---|---|---|
| PostgreSQL 12–17 | `PostgreSQLAdapter` (`adapters/postgres`) | asyncpg | Supported | **Validated** against PostgreSQL 16.15 — all 13 matrix capabilities |
| MySQL 8.0, 8.4 LTS, 9.x (5.7 reduced) | `MySQLAdapter` (`adapters/mysql`) | PyMySQL | Supported | **Validated** against MySQL 8.4.11 (and 8.0.46) — all 13 |
| MariaDB 10.5+ (10.6, 10.11, 11.x LTS) | `MariaDBAdapter` (`adapters/mysql`) | PyMySQL | Supported | **Validated** against MariaDB 11.4.13 (and 10.11.14, performance_schema ON and OFF) — all 13 |
| SQL Server 2016–2022, Azure SQL | `SQLServerAdapter` (`adapters/mssql`) | pyodbc + ODBC Driver 18 | Supported | **Validated** against SQL Server 2022 Developer 16.0.4305.5 (Query Store on and off) — all 13 |
| Oracle 12.2–23ai | `OracleAdapter` (`adapters/oracle`) | python-oracledb thin | Supported (no management packs) | **Validated** against Oracle Database 21c XE 21.3 (CDB root and PDB, TCP) — all 13; TCPS not exercised (XE has no TCPS listener) |
| Db2 LUW 11.1–12.1 | `Db2Adapter` (`adapters/db2`) | ibm_db (bundled CLI driver) | Supported | **Validated** against Db2 Community Edition 11.5.9.0 — all 13 |
| Db2 for z/OS 12–13 | `Db2zOSAdapter` (`adapters/db2zos`) | ibm_db (DRDA) | **Partial** | Unit-tested with scripted catalog/RTS responses; **external validation required** (no containerized z/OS) |

"Validated" means `scripts/integration-test` ran the real-engine suite (75 tests; fixtures,
workloads, blocking chains, least-privilege users from the engine pages) against that
version on 2026-10-18 and every capability column passed. The result ships in
`backend/app/data/real_engine_validation.json` and is shown on **Platform → Engine support**;
re-run the script to validate other versions. Bugs this exposed and fixed: SQL Server
`LOGINPROPERTY` sql_variant columns and per-database query statistics; Oracle ORA-00935 in the
segment query, grants hidden behind PUBLIC grants on Oracle-owned objects, listener errors
classified as "database not found"; Db2 11.5 schema privileges (SELECTIN …); PostgreSQL
column-level privileges.

## Implemented capabilities

| Capability | PostgreSQL | MySQL | MariaDB | Microsoft SQL Server | Oracle Database | IBM Db2 LUW | IBM Db2 for z/OS |
|---|---|---|---|---|---|---|---|
| connection | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| server info | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| databases | FULL | FULL | FULL | FULL | FULL | PARTIAL | FULL |
| database create | FULL | FULL | FULL | FULL | — | — | — |
| database drop | FULL | FULL | FULL | FULL | — | — | — |
| schemas | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| objects | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| object ddl | PARTIAL | FULL | FULL | PARTIAL | FULL | PARTIAL | PARTIAL |
| users | FULL | FULL | FULL | FULL | FULL | PARTIAL | PARTIAL |
| roles | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| privileges | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| sessions | FULL | FULL | FULL | FULL | FULL | FULL | NOT IMPL |
| session cancel | FULL | FULL | FULL | — | FULL | FULL | NOT IMPL |
| session terminate | FULL | FULL | FULL | FULL | FULL | FULL | NOT IMPL |
| locks | FULL | FULL | PARTIAL | FULL | FULL | FULL | NOT IMPL |
| performance metrics | FULL | FULL | FULL | FULL | FULL | FULL | EXTERNAL |
| cpu metrics | — | — | — | FULL | FULL | FULL | EXTERNAL |
| memory metrics | — | PARTIAL | — | FULL | FULL | FULL | EXTERNAL |
| wait events | FULL | PARTIAL | PARTIAL | FULL | FULL | PARTIAL | EXTERNAL |
| query statistics | EXT | FULL | FULL | FULL | FULL | FULL | NOT IMPL |
| query history | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | NOT IMPL |
| explain | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| explain analyze | FULL | FULL | FULL | FULL | PARTIAL | — | — |
| replication | FULL | FULL | FULL | PARTIAL | PARTIAL | PARTIAL | EXTERNAL |
| configuration | FULL | FULL | FULL | FULL | FULL | FULL | NOT IMPL |
| storage | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| tablespaces | FULL | PARTIAL | PARTIAL | — | FULL | FULL | FULL |
| security scan | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| logs | — | — | — | NOT IMPL | NOT IMPL | NOT IMPL | EXTERNAL |
| sql workspace | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| read only transactions | FULL | FULL | FULL | PARTIAL | FULL | PARTIAL | PARTIAL |
| administration | FULL | FULL | FULL | FULL | PARTIAL | PARTIAL | NOT IMPL |
| recommendations | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |

Rules: PostgreSQL 22 · MySQL 19 · MariaDB 17 · SQL Server 20 · Oracle 20 · Db2 LUW 16 ·
Db2 for z/OS 5 engine-specific rules, plus 14 engine-independent rules (12 performance:
connections, cache, blocking, deadlocks, replication lag, long transactions, expensive queries,
spills, CPU, storage, latency regressions, storage forecasts; 2 security: connection without TLS,
dormant privileged accounts) that apply to every engine. In total 61 performance and 55 security
rules (`backend/app/advisors/{performance,security}/`), each with a firing and a quiet test.

## TLS modes per engine

Every server has one TLS mode from the same vocabulary — `disable`, `prefer`, `require`,
`verify-ca`, `verify-full` (`adapters/tls.py`) — and an optional CA certificate (PEM). Each adapter
translates the mode into its driver's options, and the drivers differ:

| Engine | `prefer` | `require` | `verify-ca` | `verify-full` |
|---|---|---|---|---|
| PostgreSQL (asyncpg) | TLS if offered, else plain; not validated. With a CA: TLS required, chain validated | TLS, not validated (chain validated with a CA) | chain validated | chain + host name |
| MySQL / MariaDB (PyMySQL) | opportunistic TLS, not validated (validated with a CA); plain if the server has no TLS | TLS, not validated (validated with a CA); `Ssl_cipher` checked, plain refused | chain validated; `Ssl_cipher` checked | chain + host name; `Ssl_cipher` checked |
| SQL Server (ODBC 18) | `Encrypt=Optional`, server certificate trusted | `Encrypt=yes`, server certificate trusted | `Encrypt=yes`, chain **and host name** validated (the driver always checks it) | same as `verify-ca` |
| Oracle (thin) | plain TCP unless *protocol* `tcps` | TCPS, not validated (validated with a CA) | TCPS, chain validated, DN not matched | TCPS, chain + host name, DN matched |
| Db2 LUW and z/OS (CLI) | plain TCP unless a server certificate file is set | `SECURITY=SSL`; the CLI always validates, so like `verify-ca` | `SECURITY=SSL`, chain validated | adds `SSLClientHostnameValidation=Basic` |

"Validated" without an uploaded CA means the system trust store (or, for Db2, the CLI key store).
Details per engine: [PostgreSQL](postgresql.md#tls), [MySQL](mysql.md#tls), [MariaDB](mariadb.md#tls),
[SQL Server](sql-server.md#tls), [Oracle](oracle.md#tls), [Db2 LUW](db2-luw.md#tls),
[Db2 for z/OS](db2-zos.md#tls). The engine-independent rule `GEN-SEC-001` reports servers
registered with `disable` or `prefer`.

## Storage contract

`get_storage_metrics` returns the largest tables in one shape on every engine except Db2 for z/OS:
`{schema, name, row_estimate, table_bytes, index_bytes, total_bytes}` plus engine-specific keys; a
size the engine cannot attribute is `null`. Sources: PostgreSQL `pg_class`; MySQL/MariaDB
`information_schema.TABLES`; SQL Server `sys.dm_db_partition_stats` (registered default database
only); Oracle `DBA_SEGMENTS`; Db2 LUW `SYSCAT` catalog statistics (as of the last `RUNSTATS`).
Db2 for z/OS reports table-space sizes from real-time statistics instead and has no per-table
list.

## Notes per engine

### MySQL and MariaDB (`adapters/mysql`)

One family package: shared SQL and behaviour in `MySQLFamilyBase`, flavour differences in
`MySQLAdapter` / `MariaDBAdapter` (never "MySQL with a flag"). The server's reported flavour is
checked at discovery; registering a MariaDB server as MySQL is reported as a capability warning.

- **Sessions / blocking**: `information_schema.PROCESSLIST` + `INNODB_TRX`; MySQL 8.0+ lock waits
  from `performance_schema.data_lock_waits` (plus metadata locks), MySQL 5.7 and MariaDB from
  `information_schema.INNODB_LOCK_WAITS` / `INNODB_LOCKS` (locks involved in waits only).
- **Workspace and EXPLAIN** switch the server's connection to the selected database for the call
  and back afterwards, so unqualified names resolve where the user chose.
- **TLS**: `prefer` is opportunistic TLS; `require` and stricter check `Ssl_cipher` after
  connecting and refuse a plain-text session.
- **Query statistics**: `performance_schema.events_statements_summary_by_digest` (digests, never full
  SQL as labels). MariaDB ships with `performance_schema=OFF` → *REQUIRES CONFIGURATION*.
- **Explain**: MySQL `EXPLAIN FORMAT=TREE` / `EXPLAIN ANALYZE` (8.0.18+), MariaDB `EXPLAIN FORMAT=JSON`
  / `ANALYZE FORMAT=JSON`; analyze runs inside a rolled-back transaction and is never automatic.
- **Timeouts**: `MAX_EXECUTION_TIME` (MySQL) / `max_statement_time` (MariaDB) per session.
- **Replication**: `SHOW REPLICA STATUS`; Galera metrics on MariaDB clusters.
- **Cloud**: RDS/Aurora use `mysql.rds_kill` to terminate sessions; restricted privileges are discovered.

### Microsoft SQL Server (`adapters/mssql`)

- SQL Server authentication and Microsoft Entra ID (password / service principal) through ODBC
  Driver 18. **Windows integrated (Kerberos) authentication is not implemented** — it needs a
  keytab, krb5 configuration and domain connectivity in the backend container.
- TLS: `Encrypt=Optional|Mandatory|Strict` (TDS 8.0), certificate pinning via `ServerCertificate`.
- Constraints are listed per schema; at server level (`master` or no default database) *Roles*
  covers the server roles and the roles of every accessible database (up to 100).
- Query statistics from `sys.dm_exec_query_stats` grouped by `query_hash`; Query Store is detected per
  database and never required.
- Plans: `SET SHOWPLAN_XML ON` (compiled, not executed); actual plans with `SET STATISTICS XML ON`
  inside a rolled-back transaction. Missing-index hints are shown as **evidence**, not as guaranteed
  recommendations.
- Session cancel is **not supported** (T-SQL has no "cancel another request"); terminate uses `KILL`.

### Oracle Database (`adapters/oracle`)

- python-oracledb **thin** mode: no Oracle Client is installed or redistributed. Thin mode cannot
  negotiate Native Network Encryption; use TCPS. A server that *requires* NNE reports DPY-3001 with
  that explanation.
- CDB/PDB aware: from `CDB$ROOT` the PDBs are listed and inventoried through `CDB_*` views, and
  *Users* includes the local users of every open PDB; connected to a PDB service, the inventory is
  that PDB. Constraints are listed per schema.
- The SQL workspace refuses statements on Diagnostics/Tuning Pack objects unless the server is
  marked as licensed for that pack.
- Query statistics from `V$SQLSTATS`; plans with `EXPLAIN PLAN` (not executed). Actual plans are an
  explicit action: `STATISTICS_LEVEL=ALL` + `V$SQL_PLAN_STATISTICS_ALL`, read-only transaction for
  queries and rollback for DML.
- Host CPU is derived from `V$OSSTAT` busy/idle time between two samples.

### IBM Db2 LUW (`adapters/db2`)

- Monitoring: `MON_GET_CONNECTION`, `MON_GET_WORKLOAD`, `MON_GET_BUFFERPOOL`, `MON_GET_TABLESPACE`,
  `MON_GET_TRANSACTION_LOG`, `MON_GET_PKG_CACHE_STMT`, `MON_GET_LOCKS`, `MON_GET_HADR`,
  `MON_GET_MEMORY_SET`, `SYSIBMADM.MON_CURRENT_SQL` / `MON_LOCKWAITS`.
- Authorities are reported as Db2 authorities (DBADM, SECADM, DATAACCESS, ACCESSCTRL, SQLADM,
  WLMADM, EXPLAIN, …), not mapped onto PostgreSQL-style roles. Instance authorities (SYSADM,
  SYSCTRL, SYSMAINT, SYSMON) are OS groups; the current session's are read with
  `AUTH_LIST_AUTHORITIES_FOR_AUTHID`, and the group names from the DBM configuration.
- Users are authenticated outside Db2: "users" are the authorization IDs found in the grant catalog
  (database, role, schema, table, column, routine, package, sequence, index, tablespace and module
  grants), and "create user" is not offered.
- One database per registration: workspace statements and EXPLAIN for another database are
  refused.
- RUNSTATS / REORG: recommendations from catalog statistics (REORGCHK-style F1/F2/F3), the
  REORG-pending state and stale statistics. **KBM DBManager never runs them.**
- EXPLAIN needs the explain tables (`SYSPROC.SYSINSTALLOBJECTS`); actual row counts need an activity
  event monitor and are not supported.

### IBM Db2 for z/OS (`adapters/db2zos`)

A separate adapter on the SYSIBM catalog. Classification (z/OS vocabulary):

| Area | Status |
|---|---|
| Connection (DRDA, ibm_db) | SUPPORTED — needs a Db2 Connect license file or server-side activation |
| Version, location, SSID, data sharing group, APPLCOMPAT | SUPPORTED |
| Databases, schemas (qualifiers), tables, views, indexes, routines, triggers, sequences | SUPPORTED |
| Tablespaces and sizes (real-time statistics) | SUPPORTED |
| Authorities and grants (SYSUSERAUTH, SYSDBAUTH, SYSTABAUTH) | SUPPORTED |
| Users: authorization IDs holding catalog grants | PARTIAL — the identities (RACF, ACF2, Top Secret) are not visible to Db2 |
| EXPLAIN (PLAN_TABLE under CURRENT SQLID) | PARTIAL — access path only |
| REORG / RUNSTATS / COPY recommendations (DSNACCOX-style, from RTS) | PARTIAL — recommendations only |
| Subsystem performance, CPU/zIIP, buffer pools, waits (IFI/IFCID, SMF, accounting traces) | REQUIRES EXTERNAL MONITORING |
| Threads, locks (-DISPLAY THREAD / DATABASE via ADMIN_COMMAND_DB2) | NOT IMPLEMENTED |
| Dynamic statement cache (EXPLAIN STMTCACHE ALL), DSNZPARMs (ADMIN_INFO_SYSPARM) | NOT IMPLEMENTED |
| Administration actions | NOT IMPLEMENTED (z/OS changes go through change management) |

## Licensing implications

* **Oracle** — The adapter's collectors query only views available without a management pack. They
  never query AWR (`DBA_HIST_*`), ASH (`V$ACTIVE_SESSION_HISTORY`), ADDM, SQL Monitoring
  (`V$SQL_MONITOR`), the SQL Tuning Advisor (`DBMS_SQLTUNE`) or the metric views (`V$SYSMETRIC`…).
  A unit test fails the build if one of them appears in the adapter's SQL. Each server has
  **Oracle Diagnostics Pack** and **Oracle Tuning Pack** flags, **disabled by default**. The SQL
  workspace refuses statements that reference pack objects unless the matching flag is set
  ([Oracle](oracle.md#licensed-features-diagnostics-and-tuning-pack)); the collectors ignore the
  flags. Active Data Guard (read-only open standby with apply) is a separate license; KBM DBManager only
  reads `V$DATAGUARD_STATS`.
* **SQL Server** — The ODBC driver is Microsoft's (msodbcsql18, installed in the backend image after
  accepting the Microsoft EULA; build with `INSTALL_MSODBC=false` to omit it, or
  `DBM_MSODBC_OPTIONAL=true` in Compose to continue when the repository is unreachable). Query Store is free.
* **Db2 for z/OS** — DRDA access from distributed clients needs **Db2 Connect**. Detailed performance
  data comes from z/OS monitors (e.g. OMEGAMON), not from SQL.
* **Db2 LUW** — The ibm_db wheel bundles IBM's Data Server Driver (its license applies).
* **PostgreSQL** — `pg_stat_statements` (contrib) needs `shared_preload_libraries`; without it query
  statistics report *REQUIRES EXTENSION*, never estimates.
