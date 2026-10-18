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
| Supported versions | 13–17 | 8.0, 8.4 | 10.6–11.x | 2016–2022, Azure SQL | 12.2–23ai | 11.1–12.1 | 12–13 |
| Driver | asyncpg | PyMySQL | PyMySQL | pyodbc + ODBC Driver 18 | python-oracledb (thin) | ibm_db | ibm_db (DRDA, Db2 Connect) |
| Connection | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| TLS | FULL | FULL | FULL | FULL | FULL (TCPS) | FULL | FULL |
| Inventory | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Databases | FULL | FULL | FULL | FULL | FULL (CDB/PDB) | PARTIAL (one database per connection) | FULL |
| Schemas | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Tables | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Views | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Indexes | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Procedures | FULL | FULL | FULL | FULL | FULL (incl. packages) | FULL | FULL |
| Functions | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Users | FULL | FULL | FULL | FULL | FULL | PARTIAL (authorization IDs; authentication is external) | EXTERNAL MONITORING REQUIRED (RACF/ACF2/Top Secret) |
| Roles | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| Privileges | FULL | FULL | FULL | FULL | FULL | FULL | FULL |
| Sessions | FULL | FULL | FULL | FULL (REQUIRES PRIVILEGE: VIEW SERVER STATE) | FULL (REQUIRES PRIVILEGE: SELECT_CATALOG_ROLE) | FULL (REQUIRES PRIVILEGE: SQLADM or EXECUTE on MON_GET_*) | NOT IMPLEMENTED |
| Locks | FULL | FULL | PARTIAL | FULL | FULL | FULL | NOT IMPLEMENTED |
| Blocking | FULL | FULL | PARTIAL | FULL | FULL | FULL | NOT IMPLEMENTED |
| Transactions | FULL | FULL | FULL | FULL | FULL | FULL | EXTERNAL MONITORING REQUIRED |
| Metrics | FULL (no CPU/memory) | FULL (memory PARTIAL, no CPU) | FULL (no CPU/memory) | FULL | FULL | FULL | EXTERNAL MONITORING REQUIRED |
| Storage | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| Queries | REQUIRES CONFIGURATION (pg_stat_statements) | FULL (performance_schema) | FULL / REQUIRES CONFIGURATION when performance_schema=OFF | FULL | FULL (V$SQLSTATS) | FULL (MON_GET_PKG_CACHE_STMT) | NOT IMPLEMENTED |
| Execution plans | FULL (estimate + analyze) | FULL (estimate + analyze on 8.0.18+) | FULL | FULL (showplan XML, actual plan) | FULL estimate, PARTIAL actual | REQUIRES CONFIGURATION (explain tables); estimates only | PARTIAL (PLAN_TABLE) |
| Replication | FULL | FULL | FULL | PARTIAL (Always On AGs only) | PARTIAL (Data Guard destinations) | PARTIAL (HADR) | EXTERNAL MONITORING REQUIRED |
| Configuration | FULL | FULL | FULL | FULL | FULL | FULL | NOT IMPLEMENTED |
| Security | FULL | FULL | FULL | FULL | FULL | FULL | PARTIAL |
| Recommendations | FULL (22 rules) | FULL (19) | FULL (19, MySQL family) | FULL (20) | FULL (20) | FULL (16) | PARTIAL (5) |
| Administration | FULL | FULL | FULL | FULL | PARTIAL (no PDB lifecycle) | PARTIAL (no users/databases: external) | NOT IMPLEMENTED |
| Licensing notes | — | — | — | — | Diagnostics/Tuning Pack (AWR, ASH, ADDM, SQL Monitor, SQL Tuning Advisor) never queried; flags default off | — | Db2 Connect license required |
| Real-engine validation | VALIDATED 16.15 | VALIDATED 8.4.11 (and 8.0.46) | VALIDATED 11.4.13 (and 10.11.14) | VALIDATED 16.0.4305.5 | VALIDATED 21.3 XE (TCPS not exercised) | VALIDATED 11.5.9.0 | BLOCKED BY EXTERNAL DEPENDENCY (needs a z/OS subsystem) |
| Documentation | Permissions guide + adapter docs | yes | yes | yes | yes | yes | yes (partial-support notes) |
| Tests | unit + 12 integration | unit + 12 integration (family suite, run per flavor) | same family suite | unit + 13 integration | unit + 14 integration | unit + 13 integration | unit only |

Rule counts are engine-specific rules (`backend/app/advisors/{performance,security}/<engine>.py`);
12 engine-independent rules (`common.py`) apply in addition to every engine.

## Notes per engine

- **PostgreSQL** — the reference adapter (native asyncio). Query statistics need
  `shared_preload_libraries = 'pg_stat_statements'` and the extension in the monitored database.
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

Back to the [capability matrix](capability-matrix.md) · [gap analysis](gap-analysis.md).
