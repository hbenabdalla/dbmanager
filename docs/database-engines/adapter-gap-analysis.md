# Adapter gap analysis

What each engine adapter still lacks against **real** databases, taken from the adapters'
declared capabilities (`static_capabilities` in `backend/app/adapters/<engine>/adapter.py`, listed
with `python -m app.cli matrix`) and their code. The simulation adapter is not considered: it
exercises the UI and the rule engines, but proves nothing about real connectivity.

The current per-capability status is the [support matrix](index.md#implemented-capabilities); the
real-engine validation status is in the [implementation and validation table](index.md#implementation-and-validation-status).
Everything that is not listed below as missing is implemented.

Vocabulary used here:

* **Not implemented** — the engine offers it, DBManager does not read it yet. The UI shows the
  capability as unavailable; no value is invented.
* **Not supported** — the engine does not offer it through SQL, or DBManager deliberately does not
  offer it (for example creating Oracle PDBs).
* **Partial** — implemented with a stated limitation.
* **External** — needs infrastructure outside the database (z/OS monitors, host agents).

## Engine-independent gaps

These apply to every adapter, because no engine exposes the data through SQL or because the
platform does not have the integration yet.

| Area | Status | Notes |
|---|---|---|
| Host CPU / memory / disk for PostgreSQL, MySQL, MariaDB | Not supported over SQL | SQL Server, Oracle and Db2 LUW report CPU from their own views. The others need a host or cloud-monitoring integration (node exporter, CloudWatch, Azure Monitor), which DBManager does not have |
| Server log collection | Not implemented on any engine | PostgreSQL and MySQL/MariaDB logs need file access; SQL Server `xp_readerrorlog`, Oracle `V$DIAG_ALERT_EXT` and Db2 `PD_GET_DIAG_HIST` are reachable but not read. The Logs tab shows the collection log only |
| Query history | Partial on every engine | built from periodic snapshots of the engine's statement statistics in the time-series database; no per-execution history |
| Applying recommendations | Not offered, by design | rules are advisory; changes go through guarded administration actions or the SQL workspace with confirmation |
| Real-engine validation of Db2 for z/OS | External | needs a z/OS subsystem with Db2 Connect |

Implemented since the previous version of this page: alerting and notifications, backup and
restore of managed databases ([Backup & Restore](../backup-restore/index.md)), real-engine
validation of SQL Server, Oracle and Db2 LUW, workspace and EXPLAIN in the selected database on
MySQL/MariaDB, schema-wide constraints on SQL Server and Oracle, roles across SQL Server databases,
PDB local users from `CDB$ROOT`, the full Db2 LUW grant catalog and one storage contract for the
largest tables.

## PostgreSQL (`adapters/postgres`)

| Gap | Status | Notes |
|---|---|---|
| Object DDL | Partial | table DDL is reconstructed from the catalog; partitions, policies and some trigger details are not reproduced with `pg_dump` fidelity |
| Query statistics | Requires extension | `pg_stat_statements` in `shared_preload_libraries` and the extension in the connected database |
| CPU and memory metrics | Not supported | not exposed over SQL |
| Wait events | Sampled | taken from `pg_stat_activity` at collection time, not continuously |
| Bloat | Partial | estimated from dead tuples; `pgstattuple` is not used |
| Autovacuum | Partial | dead-tuple and stale-statistics rules; no per-table autovacuum progress view |
| Logical replication | Partial | slots and subscriptions are listed, not analysed |
| Server logs | Not supported | file-system access required |

## MySQL (`adapters/mysql`, `MySQLAdapter`)

| Gap | Status | Notes |
|---|---|---|
| CPU metrics | Not supported | not exposed over SQL |
| Memory metrics | Partial | memory instrumented by `performance_schema` only |
| Wait events | Partial | row-lock waits from status counters; per-event waits need `performance_schema` consumers that DBManager does not enable |
| Tablespaces | Partial | InnoDB tablespaces (needs `PROCESS`) |
| MySQL 5.7 | Partial | no roles, no `EXPLAIN ANALYZE`, locks only when involved in waits, no metadata locks |
| Error log, Group Replication details, Aurora-specific metrics | Not implemented | `performance_schema.error_log` is not read |

## MariaDB (`adapters/mysql`, `MariaDBAdapter`)

| Gap | Status | Notes |
|---|---|---|
| Locks | Partial | locks involved in waits (`INNODB_LOCKS` / `INNODB_LOCK_WAITS`); held-only locks are not listed |
| Query statistics | Requires configuration | `performance_schema=ON` (off by default on MariaDB) |
| CPU and memory metrics | Not supported | memory instrumentation is off by default |
| Wait events, tablespaces | Partial | as on MySQL |
| Unused-index detection | Requires configuration | `userstat=1` |
| Error log | Not implemented | |

## Microsoft SQL Server (`adapters/mssql`)

| Gap | Status | Notes |
|---|---|---|
| Windows integrated (Kerberos) authentication | Not implemented | needs a keytab, krb5 configuration and domain connectivity in the backend container; SQL logins and Microsoft Entra ID work |
| Session cancel | Not supported | T-SQL cannot cancel another session's request; terminate uses `KILL` |
| Object DDL | Partial | module definitions via `OBJECT_DEFINITION`; table DDL reconstructed from `sys.columns` |
| Replication | Partial | Always On availability groups only; log shipping and transactional replication are not monitored |
| Read-only workspace | Partial | SQL Server has no read-only transaction: classifier, permissions and rollback |
| Largest tables | Partial | read for the registered default database only |
| Roles across databases | Partial | at server level, the first 100 accessible databases (`ROLE_DATABASES_MAX`) |
| Error log | Not implemented | `xp_readerrorlog` is not read |
| Tablespaces | Not applicable | filegroups and files are shown under storage |

## Oracle Database (`adapters/oracle`)

| Gap | Status | Notes |
|---|---|---|
| Diagnostics / Tuning Pack data (AWR, ASH, ADDM, SQL Monitoring, advisors) | Not used, by licensing policy | collectors never query them; the workspace runs such statements only when the server is marked licensed |
| Native Network Encryption | Not supported | python-oracledb thin mode; use TCPS |
| TCPS | Implemented, not real-engine validated | the XE validation image has no TCPS listener |
| PDB create / drop | Not supported | use DBCA or SQL\*Plus |
| Administration | Partial | create user/role, grant, revoke, create index; no PDB lifecycle |
| Actual plans | Partial | `STATISTICS_LEVEL=ALL` run and `V$SQL_PLAN_STATISTICS_ALL`, on explicit request; DML rolled back |
| Replication | Partial | Data Guard role, destinations and `V$DATAGUARD_STATS` lag; GoldenGate not monitored |
| Query history | Partial | snapshots of `V$SQLSTATS`; AWR is not read |
| PDB local users from the root | Partial | listed (up to 5,000), but their grants are read only on the PDB's own registration |
| RAC per-instance (`GV$`) views, Autonomous Database wallets | Not implemented | |
| Alert log | Not implemented | `V$DIAG_ALERT_EXT` is not read |

## IBM Db2 LUW (`adapters/db2`)

| Gap | Status | Notes |
|---|---|---|
| Databases | Partial | one database per connection; other databases are refused in the workspace — register each one |
| Users | Partial | authorization IDs from the grant catalog; authentication is external (OS, LDAP, Kerberos) |
| Create / drop database, create user | Not supported | CLP/instance commands and external identities |
| Object DDL | Partial | view, routine and trigger text from `SYSCAT`; table DDL reconstructed (`db2look` for full DDL) |
| Wait events | Partial | lock waits and activity states; no time-spent breakdown |
| Actual plans | Not supported | section actuals need an activity event monitor, which DBManager does not create |
| Replication | Partial | HADR via `MON_GET_HADR`; Q Replication and CDC are not monitored |
| Largest tables | Partial | catalog statistics, as current as the last `RUNSTATS` |
| Read-only workspace | Partial | classifier, privileges and rollback |
| Restore | External tool | DBManager shows the `RESTORE` command; it does not run it |
| pureScale per-member breakdown, `db2diag.log` | Not implemented | |

## IBM Db2 for z/OS (`adapters/db2zos`)

Partial by design and **not validated against a real subsystem**. Only what the z/OS adapter's own
code does is listed; nothing is inferred from Db2 LUW.

| Gap | Status | Notes |
|---|---|---|
| Connection | Partial | DRDA through `ibm_db`; needs a Db2 Connect license file or server-side activation |
| Threads (sessions), cancel, locks | Not implemented | reachable through `ADMIN_COMMAND_DB2` (`-DISPLAY THREAD`, `-DISPLAY DATABASE LOCKS`, `-CANCEL THREAD`), not built |
| Dynamic statement cache (query statistics, history) | Not implemented | `EXPLAIN STMTCACHE ALL` is not used |
| DSNZPARMs (configuration) | Not implemented | `ADMIN_INFO_SYSPARM` is not read |
| Subsystem performance, CPU/zIIP, memory, waits, replication, logs | External | IFI/IFCID traces, SMF/RMF, OMEGAMON, SYSLOG |
| Users | Partial | authorization IDs holding catalog grants; RACF/ACF2/Top Secret identities are not visible |
| Roles | Partial | trusted-context roles (`SYSIBM.SYSROLES`) |
| Object DDL | Partial | column list from `SYSIBM.SYSCOLUMNS`; full DDL needs ADB2GEN or an admin tool |
| EXPLAIN | Partial | access path into `PLAN_TABLE` under `CURRENT SQLID`; no actuals |
| Storage | Partial | table-space sizes from real-time statistics; no per-table *Largest tables* list; DASD capacity is external |
| Security scan | Partial | catalog authorities only |
| Recommendations | Partial | REORG / RUNSTATS / COPY from real-time statistics (5 rules) |
| Administration, backup, restore | Not offered | z/OS changes go through the site's change management and tools |

## Suggested order of work

1. Db2 for z/OS: validate against a real subsystem through Db2 Connect; then threads and locks
   through `ADMIN_COMMAND_DB2` and the dynamic statement cache.
2. Oracle: exercise TCPS against a real TCPS listener.
3. Server log reading where SQL reaches it (SQL Server error log, Oracle alert log, Db2
   `PD_GET_DIAG_HIST`, MySQL `performance_schema.error_log`).
4. A host-metrics integration for engines that do not expose CPU and memory.
5. SQL Server Windows (Kerberos) authentication, if deployments need it.
