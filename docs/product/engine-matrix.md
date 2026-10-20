# Engine capability matrix

Per-engine assessment. Levels come from each adapter's declared capabilities
(`backend/app/adapters/<engine>/adapter.py`, `static_capabilities`) and are refined at runtime by
`discover_capabilities()` on each registered server (privileges, configuration, licensing). The
**Real-engine validation** row comes from `backend/app/data/real_engine_validation.json`, written
by `scripts/integration-test` against the vendors' official container images.

| Level | Meaning |
|---|---|
| FULL | Implemented with the engine's native interface |
| PARTIAL | Implemented with a documented limitation |
| REQUIRES PRIVILEGE | Works when the monitoring account holds a specific grant |
| REQUIRES CONFIGURATION | Works when the engine is configured for it (extension, setting, explain tables) |
| REQUIRES LICENSE | Needs a separately licensed vendor option |
| NOT IMPLEMENTED | Possible on the engine, not built in this release |
| EXTERNAL MONITORING REQUIRED | Needs infrastructure outside the database (z/OS RMF/SMF, OMEGAMON, …) |

## Matrix

| Dimension | PostgreSQL | MySQL | MariaDB | SQL Server | Oracle | Db2 LUW | Db2 z/OS |
|---|---|---|---|---|---|---|---|
| Supported versions (adapter) | 12–17 | 8.0, 8.4 LTS, 9.x (5.7 reduced) | 10.5+ (10.6, 10.11, 11.x LTS) | 2016–2022, Azure SQL | 12.2–23ai | 11.1–12.1 | 12–13 |
| Driver | asyncpg | PyMySQL | PyMySQL | pyodbc + ODBC Driver 18 | python-oracledb (thin) | ibm_db | ibm_db (DRDA, Db2 Connect) |
| Connection | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| TLS | FULL | FULL (`prefer` negotiates TLS when offered; `require` checks `Ssl_cipher`) | FULL (same; real-validated: verify-ca, wrong CA, require, prefer) | FULL | FULL (TCPS; not exercised against a real listener) | FULL | FULL |
| Inventory | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Databases | FULL | FULL | FULL | FULL | FULL (CDB/PDB) | PARTIAL (one database per connection) | FULL |
| Schemas | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Tables | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Views | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Indexes | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Procedures | FULL | FULL | FULL | FULL | FULL (incl. packages) | FULL | FULL |
| Functions | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Constraints (per schema and per table) | FULL | FULL | FULL | FULL | FULL (CDB views from root) | FULL | FULL |
| Users | FULL | FULL | FULL | FULL | FULL (from CDB$ROOT also the local users of every open PDB) | PARTIAL (authorization IDs; authentication is external) | PARTIAL (authorization IDs holding catalog grants; identities live in RACF/ACF2/Top Secret) |
| Roles | FULL | FULL | FULL | FULL (server roles and the roles of every accessible database, up to 100) | FULL | FULL | PARTIAL |
| Privileges | FULL | FULL | FULL | FULL | FULL | FULL (database, role, schema, table, column, routine, package, sequence, index, tablespace and module grants) | FULL |
| Sessions | FULL | FULL | FULL | FULL (REQUIRES PRIVILEGE: VIEW SERVER STATE) | FULL (REQUIRES PRIVILEGE: SELECT_CATALOG_ROLE) | FULL (REQUIRES PRIVILEGE: SQLADM or EXECUTE on MON_GET_*) | NOT IMPLEMENTED |
| Locks | FULL | FULL (5.7: `INFORMATION_SCHEMA.INNODB_LOCK_WAITS`, PARTIAL) | PARTIAL | FULL | FULL | FULL | NOT IMPLEMENTED |
| Blocking | FULL | FULL | PARTIAL | FULL | FULL | FULL | NOT IMPLEMENTED |
| Read-only SQL transactions (workspace) | FULL | FULL (`START TRANSACTION READ ONLY`, in the selected database) | FULL (in the selected database) | PARTIAL (no read-only mode: classifier + permissions + rollback) | FULL (`SET TRANSACTION READ ONLY`) | PARTIAL (classifier + privileges + rollback; only the connected database — another one is refused) | PARTIAL (classifier + privileges + rollback) |
| Metrics | FULL (no CPU/memory) | FULL (memory PARTIAL, no CPU) | FULL (no CPU/memory) | FULL | FULL | FULL | EXTERNAL MONITORING REQUIRED |
| Storage | FULL | FULL (no tablespaces) | FULL (no tablespaces) | PARTIAL (largest tables only for the registered database; no tablespaces) | FULL | PARTIAL (largest tables from catalog statistics, as of the last RUNSTATS) | PARTIAL |
| Queries | REQUIRES CONFIGURATION (pg_stat_statements) | FULL (performance_schema) | FULL / REQUIRES CONFIGURATION when performance_schema=OFF | FULL | FULL (V$SQLSTATS) | FULL (MON_GET_PKG_CACHE_STMT) | NOT IMPLEMENTED |
| Execution plans | FULL (estimate + analyze) | FULL (estimate + analyze on 8.0.18+) | FULL | FULL (showplan XML, actual plan) | FULL estimate, PARTIAL actual | REQUIRES CONFIGURATION (explain tables); estimates only | PARTIAL (PLAN_TABLE) |
| Replication | FULL | FULL | FULL | PARTIAL (Always On AGs only) | PARTIAL (Data Guard destinations) | PARTIAL (HADR) | EXTERNAL MONITORING REQUIRED |
| Configuration | FULL | FULL | FULL | FULL | FULL | FULL | NOT IMPLEMENTED |
| Security | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| Recommendations | FULL (22 rules) | FULL (19) | FULL (17) | FULL (20) | FULL (20) | FULL (16) | PARTIAL (5) |
| Administration | FULL | FULL | FULL | FULL | PARTIAL (no PDB lifecycle) | PARTIAL (no users/databases: external) | NOT IMPLEMENTED |
| Backup (DBManager-run) | Logical: `pg_dump -Fc` | Logical: `mysqldump` | Logical: `mariadb-dump` | Native: `BACKUP … COPY_ONLY, CHECKSUM` (server-side file) | Logical: Data Pump schema export (server-side); RMAN external | Native: online `BACKUP` (needs archive logging) | EXTERNAL TOOL REQUIRED |
| Restore (DBManager-run) | `pg_restore` into a new database | client into a new database | client into a new database | `RESTORE … WITH MOVE` as a new database | Data Pump import with `REMAP_SCHEMA` | EXTERNAL TOOL REQUIRED (command shown) | EXTERNAL TOOL REQUIRED |
| Backup real-engine run | VALIDATED (16.15) | VALIDATED (8.4.11, binary logging on) | VALIDATED (11.4.13, 10.11) | VALIDATED (16.0.4305.5) | VALIDATED (21.3 XE) | VALIDATED (11.5.9.0, SYSMAINT operator; restore external) | EXTERNAL VALIDATION REQUIRED |
| Licensing notes | — | — | — | — | Diagnostics/Tuning Pack (AWR, ASH, ADDM, SQL Monitor, SQL Tuning Advisor) never queried by collectors; the SQL workspace refuses pack objects unless the pack is declared licensed; flags default off | — | Db2 Connect license required |
| Real-engine validation | VALIDATED 16.15 | VALIDATED 8.4.11 (and 8.0.46) | VALIDATED 11.4.13 (and 10.11.14) | VALIDATED 16.0.4305.5 | VALIDATED 21.3 XE (TCPS not exercised) | VALIDATED 11.5.9.0 | EXTERNAL VALIDATION REQUIRED (needs a z/OS subsystem) — see the [validation report](real-engine-validation.md) |
| Documentation | Permissions guide + adapter docs | yes | yes | yes | yes | yes | yes (partial-support notes) |
| Tests | unit + 12 integration | unit + 12 integration (family suite, run per flavor) | same family suite | unit + 13 integration | unit + 14 integration | unit + 13 integration | unit only |

Rule counts are engine-specific rules (MySQL and MariaDB share most of the family rules; 2 are MySQL-only) (`backend/app/advisors/{performance,security}/<engine>.py`);
14 engine-independent rules (`common.py`) apply in addition to every engine. In total: 61 performance and 55 security rules, each with a firing and a quiet test.

## Notes per engine

- **PostgreSQL** — the reference adapter (native asyncio). Query statistics need
  `shared_preload_libraries = 'pg_stat_statements'` and the extension in the monitored database.
  Creating and dropping databases is done through the SQL workspace with a typed confirmation, not
  through a guarded administration action.
- **MySQL / MariaDB** — one family adapter; MariaDB lock detail depends on the InnoDB lock
  instrumentation available in the version (`PARTIAL`).
- **SQL Server** — `SESSION_CANCEL` is NOT SUPPORTED by T-SQL (only `KILL`, i.e. terminate);
  DMV-backed features degrade to REQUIRES PRIVILEGE without `VIEW SERVER STATE`.
- **Oracle** — thin mode, no Instant Client. CDB root needs a common user with
  `CONTAINER_DATA = ALL` to see PDB rows. Native Network Encryption is not supported by thin
  mode: use TCPS.
- **Db2 LUW** — SQLADM also permits `WLM_CANCEL_ACTIVITY`; creating roles needs SECADM; REORG and
  RUNSTATS are only ever recommended.
- **Db2 for z/OS** — partial adapter (catalog, real-time statistics, PLAN_TABLE). Threads, locks,
  subsystem performance and IFCID data require external z/OS monitoring and are never fabricated.

Backup and restore per engine: [engine support](../backup-restore/engine-support.md).

Back to the [capability matrix](capability-matrix.md) · [gap analysis](gap-analysis.md).
