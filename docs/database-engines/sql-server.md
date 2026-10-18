# Microsoft SQL Server

## Supported versions

SQL Server 2016–2022 and Azure SQL (validated against SQL Server 2022 Developer 16.0.4305.5, Query Store on and off).

## Driver

`pyodbc` with **Microsoft ODBC Driver 18 for SQL Server** (`msodbcsql18`), installed in the backend image at build time after accepting the Microsoft EULA. If the driver is missing, **Platform → Database drivers** shows *SQL Server driver MISSING* and connection tests fail with `DRIVER_NOT_INSTALLED` ("Microsoft ODBC Driver 18 is required").

## Connection parameters

Host, port (1433), instance name (optional), default database, username, password, plus engine options: *Encrypt*, *Trust server certificate*, *Host name in certificate*, *Application intent* (read-only routing), *Multi-subnet failover*, *Authentication* (SQL login or Microsoft Entra ID password / service principal) and *ODBC driver* override.

## TLS

`Encrypt=Optional|Mandatory|Strict` (TDS 8.0) with certificate validation; for self-signed test instances use TLS mode `require` or *Trust server certificate*. A pinned certificate can be supplied.

## Privileges

Verified on SQL Server 2022 Developer 16.0.4305.5 (2026-10-18) by `scripts/integration-test mssql`: the suite creates exactly these accounts
and checks that monitoring works with them and that administration is refused without the admin grants.

### Minimum inventory account

```sql
CREATE LOGIN dbm_inventory WITH PASSWORD = '...', CHECK_POLICY = ON;
GRANT VIEW ANY DATABASE TO dbm_inventory;      -- granted to public by default
GRANT VIEW ANY DEFINITION TO dbm_inventory;    -- object definitions, principals, permissions
-- the login needs a user in each database it inventories (no data access is granted):
USE appdb; CREATE USER dbm_inventory FOR LOGIN dbm_inventory;
```

### Monitoring account

```sql
CREATE LOGIN dbm_monitor WITH PASSWORD = '...', CHECK_POLICY = ON;
GRANT VIEW SERVER STATE TO dbm_monitor;        -- DMVs: sessions, requests, locks, waits, query stats
-- SQL Server 2022 also has the narrower VIEW SERVER PERFORMANCE STATE; capability discovery checks
-- VIEW SERVER STATE, so grant that one (it includes the 2022 performance/security state permissions)
GRANT VIEW ANY DEFINITION TO dbm_monitor;
GRANT VIEW ANY DATABASE TO dbm_monitor;
USE appdb; CREATE USER dbm_monitor FOR LOGIN dbm_monitor;   -- per database
GRANT VIEW DATABASE STATE TO dbm_monitor;                 -- per database: index usage, file stats
```

Without `VIEW SERVER STATE` the user sees only its own session; locks, CPU and query statistics then
show *Privilege required*. `DBCC SQLPERF(LOGSPACE)` (log utilisation) also needs `VIEW SERVER STATE`.

Azure SQL Database (no server-level grants): create a contained user in the database and grant
`VIEW DATABASE STATE`; server DMVs that do not exist there are reported as not supported.

### DBA management account

```sql
CREATE LOGIN dbm_admin WITH PASSWORD = '...', CHECK_POLICY = ON;
ALTER SERVER ROLE dbcreator ADD MEMBER dbm_admin;       -- CREATE/DROP DATABASE
GRANT ALTER ANY LOGIN TO dbm_admin;                     -- CREATE/DROP LOGIN
GRANT ALTER ANY CONNECTION TO dbm_admin;                -- KILL
USE appdb;
CREATE USER dbm_admin FOR LOGIN dbm_admin;
GRANT ALTER ANY USER, ALTER ANY ROLE, ALTER ANY SCHEMA, CREATE SCHEMA TO dbm_admin;
GRANT CONTROL ON SCHEMA::dbo TO dbm_admin;              -- index creation and grants on dbo objects
```

`sysadmin` is never required. `CREATE LOGIN … WITH PASSWORD` is masked by SQL Server in traces and
Extended Events.

Authentication: SQL logins and Microsoft Entra ID (password or service principal) work from the
Linux backend. Windows integrated authentication is not implemented.

See [Least-privilege accounts](permissions.md) for the rules that apply to every engine.

## Supported capabilities

All matrix rows are FULL except *session cancel* (not possible from T-SQL — terminate uses `KILL`), *replication* (PARTIAL: Always On availability groups), *read-only transactions* (PARTIAL: SQL Server has no read-only transaction; workspace runs are always rolled back) and *logs* (not implemented). Query statistics come from `sys.dm_exec_query_stats`, grouped per statement hash and database; Query Store is detected per database and never required.

The authoritative per-capability status is in the [support matrix](index.md#implemented-capabilities) and,
for a registered server, on its **Overview → Capabilities** (runtime discovery refines every cell).

## Known limitations

* Windows integrated (Kerberos) authentication is not implemented.
* Missing-index DMV hints are shown as evidence, not guaranteed recommendations.
* Without `VIEW SERVER STATE` only the login's own session is visible.

## Engine-specific metrics

Besides the [common metric catalog](../monitoring/metric-catalog.md), this adapter reports:
`mssql.ag_unhealthy_databases`, `mssql.batch_requests`, `mssql.batch_requests_per_sec`, `mssql.checkpoint_pages`, `mssql.compilations`, `mssql.data_io_percent`, `mssql.full_scans`, `mssql.index_searches`, `mssql.lazy_writes`, `mssql.lock_timeouts`, `mssql.lock_waits`, `mssql.log_bytes_flushed`, `mssql.log_flushes`, `mssql.log_used_percent_max`, `mssql.log_write_percent`, `mssql.logins`, `mssql.memory_grants_pending`, `mssql.page_life_expectancy`, `mssql.page_splits`, `mssql.page_writes`, `mssql.processes_blocked`, `mssql.recompilations`, `mssql.sql_cpu_percent`, `mssql.unencrypted_sessions`

## Security checks

| Rule | Checks |
|---|---|
| `MSSQL-SEC-001` | sa login enabled |
| `MSSQL-SEC-002` | xp_cmdshell enabled |
| `MSSQL-SEC-003` | sysadmin membership |
| `MSSQL-SEC-004` | TRUSTWORTHY / guest enabled databases |
| `MSSQL-SEC-005` | No server audit |
| `MSSQL-SEC-006` | Risky server features enabled |
| `MSSQL-SEC-007` | SQL Server version out of support |
| `MSSQL-SEC-008` | Orphaned database users |
| `MSSQL-SEC-009` | SQL logins without password policy |
| `MSSQL-SEC-010` | Encryption not forced |

The common rules `GEN-SEC-001` (platform connects without TLS) and `GEN-SEC-002` (dormant
privileged accounts) also apply.

## Recommendations

| Rule | Checks |
|---|---|
| `MSSQL-PERF-001` | max server memory not configured |
| `MSSQL-PERF-002` | Page I/O latch waits dominate |
| `MSSQL-PERF-003` | High-impact missing index |
| `MSSQL-PERF-004` | Single tempdb data file |
| `MSSQL-PERF-005` | Query Store disabled |
| `MSSQL-PERF-006` | AUTO_SHRINK / AUTO_CLOSE enabled |
| `MSSQL-PERF-007` | Unused nonclustered indexes |
| `MSSQL-PERF-008` | Stale statistics |
| `MSSQL-PERF-009` | Transaction log waiting for a log backup |
| `MSSQL-PERF-010` | Parallelism settings at defaults |

Plus the common performance rules `GEN-PERF-001` … `GEN-PERF-010`. Recommendations are advice with
evidence; DB Manager never executes them.

## Troubleshooting

* `DRIVER_NOT_INSTALLED`: rebuild the backend image with network access to `packages.microsoft.com` (`INSTALL_MSODBC=true`, the default); see [ODBC driver missing](../troubleshooting/engines.md#sql-server-odbc-driver-missing).
* Certificate error at login: use TLS mode `require` for self-signed certificates, or upload the CA / set *Host name in certificate*.
* Locks, CPU or query statistics show *Privilege required*: grant `VIEW SERVER STATE`.
