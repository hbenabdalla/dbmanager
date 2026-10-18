# Adapter gap analysis: moving from demonstration to real database management

This document reviews what the platform can do **against real databases** today, engine by
engine. It does not count what the simulation adapter shows for simulated servers: the
simulation exercises the UI and rule engines, but proves nothing about real connectivity.

Status legend:

* **IMPLEMENTED**: implemented against the vendor's documented interfaces. For PostgreSQL, MySQL
  and MariaDB this is also covered by integration tests against real servers; for SQL Server,
  Oracle and Db2 by unit tests with scripted responses, with **real-server validation pending**.
* **PARTIAL**: works with stated limits.
* **NOT IMPLEMENTED**: the engine supports it, but no adapter code exists yet. The UI disables
  the feature and the API answers "not supported". No values are invented.

The engine-capability profiles that drive the UI are published in
[database-support-matrix.md](index.md) (`python -m app.cli matrix`).

## Platform-level pieces every adapter plugs into (engine-neutral)

| Area | Status | Notes |
|---|---|---|
| Adapter interface, registry, capability discovery | IMPLEMENTED | `app/adapters/base.py`; new engines only add a package and register it |
| Collection scheduler and workers, circuit breaker | IMPLEMENTED | PostgreSQL-backed queue; per-engine collectors only implement adapter methods |
| Metric normalization, health model, history, downsampling | IMPLEMENTED | normalized names (`connections.utilization`, `cache.hit_ratio`…); each adapter maps its own counters |
| Rule engine: performance and security | IMPLEMENTED | 12 engine-neutral rules plus PG 22, MySQL 19, MariaDB 17, MSSQL 20, ORA 20, DB2 16, Db2 z/OS 5, organised per engine (`advisors/performance/<engine>.py`, `advisors/security/<engine>.py`). MySQL/MariaDB rules validated on real evidence; others on scripted evidence |
| SQL classifier and workspace safeguards | IMPLEMENTED | sqlglot classifier for every dialect; read-only enforced per engine (READ ONLY transactions on PostgreSQL, MySQL/MariaDB, Oracle; classifier + privileges + rollback on SQL Server and Db2) |
| Guarded administration actions | IMPLEMENTED | adapter-specific statements, preview, confirmation, audit; refused on Monitoring-only servers |
| Normalized errors, timeouts, per-component collection status | IMPLEMENTED | `AdapterErrorCode`; worker-thread deadlines for blocking drivers; missing privileges limit one component only |
| Credentials, TLS settings, RBAC scopes, audit | IMPLEMENTED | engine-neutral |
| Host CPU / memory / disk | NOT IMPLEMENTED | no engine exposes host metrics through SQL. Needs a node-exporter / cloud-monitoring integration (CloudWatch, Azure Monitor, OCI Monitoring) |
| Server log collection | NOT IMPLEMENTED | needs log shipping or provider log APIs; the Logs tab shows the collection log only |
| Alerting / notifications | NOT IMPLEMENTED | health and findings are visible in the UI only; no e-mail, webhook or chat delivery yet |
| Backup / recovery monitoring | NOT IMPLEMENTED | no capability defined yet; add `BACKUPS` to the capability model first |
| Configuration change workflow (apply tuning) | NOT IMPLEMENTED (by design so far) | recommendations are advisory; changes go through the guarded SQL workspace |

## 1. PostgreSQL (reference adapter, `app/adapters/postgres`)

| Area | Status | Notes / next step |
|---|---|---|
| Connection, TLS (disable … verify-full, custom CA), server info, version | IMPLEMENTED | asyncpg |
| Cloud detection (RDS / Aurora) | PARTIAL | detected from settings; Azure Flexible Server and Cloud SQL not detected yet |
| Databases, schemas, tables, views, indexes, functions, sequences | IMPLEMENTED | |
| Object DDL | PARTIAL | reconstructed from the catalog; no `pg_dump`-equivalent fidelity (partitions, policies, triggers incomplete) |
| Users, roles, memberships, privileges | IMPLEMENTED | |
| Sessions, cancel, terminate, locks and blocking chains | IMPLEMENTED | |
| Performance metrics (connections, TPS, cache, temp, deadlocks, replication) | IMPLEMENTED | counters turned into rates |
| Wait events | PARTIAL | sampled from `pg_stat_activity` at collection time (no continuous sampling) |
| Query statistics | IMPLEMENTED (requires extension) | `pg_stat_statements` |
| Query history / trends | PARTIAL | derived from periodic snapshots; no per-execution history |
| EXPLAIN / EXPLAIN ANALYZE | IMPLEMENTED | ANALYZE always rolled back; generic plans for parameterized text |
| Replication | IMPLEMENTED | physical streaming; logical replication slots and subscriptions only listed |
| Configuration, storage, tablespaces | IMPLEMENTED | |
| Security scan | IMPLEMENTED | `pg_hba_file_rules` needs `pg_read_all_settings` / superuser |
| SQL workspace (read-only by default, typed confirmation for changes) | IMPLEMENTED | |
| Host CPU / memory | NOT IMPLEMENTED | platform-level item above |
| Server logs | NOT IMPLEMENTED | platform-level item above |
| Table/index bloat estimation | PARTIAL | dead-tuple based; no `pgstattuple` integration |
| Vacuum / autovacuum monitoring | PARTIAL | dead tuples and stale stats rules; no per-table autovacuum progress view |

**Next steps:** wait-event sampling (short interval, aggregated), logical replication
details, Azure/Cloud SQL detection, `pgstattuple`-based bloat, autovacuum progress.

## 2. MySQL / MariaDB (`app/adapters/mysql`) — IMPLEMENTED, validated on MySQL 8.0.46 and MariaDB 10.11.14

Connection (TLS, timeouts), discovery (version, UUID, read-only, flavour mismatch), inventory,
accounts/roles/grants (flavour-specific catalogs), sessions with blocking, InnoDB locks, status
counters, digests, EXPLAIN / EXPLAIN ANALYZE (rolled back), replication and Galera, configuration,
storage, security and tuning evidence, guarded admin actions. Remaining: error-log reading
(`performance_schema.error_log`), Group Replication details, Aurora-specific metrics.

## 3. Microsoft SQL Server (`app/adapters/mssql`) — IMPLEMENTED, real-server validation pending

pyodbc + ODBC Driver 18 (installed in the image; `INSTALL_MSODBC=false` to omit). SQL and Entra ID
authentication, Encrypt optional/mandatory/strict with certificate pinning; databases (state,
recovery model, compatibility, data/log size), schemas, objects, logins/users/roles/permissions,
sessions and blocking, locks, performance counters, CPU ring buffer / `dm_db_resource_stats`, waits,
plan-cache statistics (Query Store detected), SHOWPLAN and actual plans, Always On, configuration,
storage and log space, security and tuning evidence, admin actions. Not implemented: Windows
integrated (Kerberos) authentication, error log, log shipping / transactional replication.

## 4. Oracle Database (`app/adapters/oracle`) — IMPLEMENTED (no management packs), validation pending

python-oracledb thin; TCP/TCPS, service name or SID; CDB/PDB aware (CDB_* views from the root);
users/roles/system, object and SYSDBA-class privileges; sessions with final blockers, locks, cancel
(18c+) and kill; V$SYSSTAT counters, host CPU from V$OSSTAT, SGA/PGA, wait classes; V$SQLSTATS;
EXPLAIN PLAN and opt-in actual plans; Data Guard; parameters; tablespaces, FRA, redo. Licensing:
AWR/ASH/ADDM/SQL Monitoring/Tuning Advisor never queried (enforced by a unit test); pack flags off by
default. Not implemented: alert log, RAC per-instance (GV$) views, Autonomous Database wallets.

## 5. IBM Db2 LUW (`app/adapters/db2`) — IMPLEMENTED, validation pending

ibm_db; TLS with private CA; SYSCAT inventory; authorities (DBADM, SECADM, DATAACCESS, ACCESSCTRL, …)
and the session's instance authorities; MON_GET_* sessions, lock waits, locks, workload counters,
buffer pools, memory, transaction log, package cache, HADR; DBCFG/DBMCFG (instance vs database);
EXPLAIN via explain tables; REORG/RUNSTATS recommendations only. Not implemented: actual plans
(activity event monitor), db2diag.log, pureScale per-member breakdown.

## 6. IBM Db2 for z/OS (`app/adapters/db2zos`) — PARTIAL by design, validation pending

SUPPORTED: DRDA connection (Db2 Connect licensing), version/location/SSID/APPLCOMPAT, SYSIBM catalog
inventory, authorities and grants, real-time-statistics storage, DSNACCOX-style REORG/RUNSTATS/COPY
recommendations, EXPLAIN into PLAN_TABLE. REQUIRES EXTERNAL MONITORING: subsystem performance,
CPU/zIIP, buffer pools, waits (IFI/SMF), external security identities. NOT IMPLEMENTED: threads and
locks (-DISPLAY via ADMIN_COMMAND_DB2), dynamic statement cache, DSNZPARMs, administration actions.

## Recommended order of work

1. **Validate** the SQL Server, Oracle and Db2 adapters against real instances with the optional
   test profiles (`docker-compose.test.yml --profile mssql|db2`, your own Oracle instance) and fix
   any version-specific SQL.
2. **Platform**: alert delivery (e-mail and webhook) for health transitions and critical findings.
3. **Host metrics integration** (node exporter / cloud monitoring APIs) for CPU and memory where the
   engine does not expose them (PostgreSQL, MySQL, MariaDB).
4. Db2 for z/OS: threads and locks through `ADMIN_COMMAND_DB2`, dynamic statement cache.
5. Oracle: optional AWR/ASH analysis strictly behind the Diagnostics Pack flag.
