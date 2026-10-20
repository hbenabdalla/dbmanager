# IBM Db2 for Linux, UNIX and Windows

## Supported versions

Db2 LUW 11.1 – 12.1 (real-engine validated against Db2 Community Edition 11.5.9.0).

## Driver

`ibm_db` with IBM's bundled Data Server Driver for ODBC and CLI (`clidriver`, IBM license). Db2 LUW and Db2 for z/OS share only the connection and error mapping; everything else is separate.

## Connection parameters

Host, port (50000), database name, username, password, plus *current schema*, *SSL server certificate* and *authentication* type.

## TLS

The TLS mode becomes Db2 CLI keywords (`connection_string` in `adapters/db2/base.py`, shared with
the z/OS adapter):

| Mode | Keywords | Effect |
|---|---|---|
| `disable` | — | plain TCP |
| `prefer` | — | **plain TCP**: the CLI has no opportunistic TLS. TLS is used only when the *Server certificate file* option is set |
| `require` | `SECURITY=SSL` | TLS; the CLI validates the server certificate against the uploaded CA (`SSLServerCertificate`, a private temporary file), the *Server certificate file* option, or its own key store. There is no "encrypt without validation" mode, so `require` behaves like `verify-ca` |
| `verify-ca` | `SECURITY=SSL` (+ `SSLServerCertificate`) | certificate chain validated; host name not checked |
| `verify-full` | as `verify-ca` + `SSLClientHostnameValidation=Basic` | chain validated and host name checked (Db2 CLI 11.5.6 or later) |

Connect to the instance's TLS port (`SSL_SVCENAME`), not to `SVCENAME`. Rule `DB2-SEC-004` flags
instances without a TLS port.

## Privileges

Verified on Db2 Community Edition 11.5.9.0 (2026-10-18) by `scripts/integration-test db2`: the monitoring,
administration and read-only identities below are exactly those the real-engine suite uses.

Db2 authenticates users outside the database (operating system, LDAP or Kerberos). Create the OS
or LDAP user first, then grant database authorities and privileges to it.

### Minimum inventory account

```sql
GRANT CONNECT ON DATABASE TO USER DBMINV;
-- SYSCAT views are readable by PUBLIC unless the database was created RESTRICTIVE:
GRANT SELECT ON SYSCAT.TABLES TO USER DBMINV;   -- only needed for RESTRICTIVE databases (repeat per view)
```

### Monitoring account

```sql
GRANT CONNECT ON DATABASE TO USER DBMMON;
GRANT SQLADM ON DATABASE TO USER DBMMON;        -- EXECUTE on the MON_GET_* table functions, event monitors
GRANT EXECUTE ON FUNCTION SYSPROC.ENV_GET_SYSTEM_RESOURCES TO USER DBMMON;
GRANT EXECUTE ON FUNCTION SYSPROC.AUTH_LIST_AUTHORITIES_FOR_AUTHID TO USER DBMMON;
-- configuration views (the views call DB_GET_CFG / DBM_GET_CFG with the definer's rights, so no
-- EXECUTE grant on the functions is needed; GRANT EXECUTE ON FUNCTION SYSPROC.DB_GET_CFG fails with
-- SQL0476N anyway because the name is overloaded):
GRANT SELECT ON SYSIBMADM.DBCFG TO USER DBMMON;
GRANT SELECT ON SYSIBMADM.DBMCFG TO USER DBMMON;
```

**SQLADM also permits `WLM_CANCEL_ACTIVITY`** (verified on Db2 11.5.9): a monitoring account with
SQLADM can cancel running statements. For a strictly read-only monitoring identity use the
least-privilege alternative below instead of SQLADM.

Least-privilege alternative to SQLADM: `GRANT EXECUTE ON FUNCTION SYSPROC.MON_GET_<name>` for each
function listed under [Supported capabilities](#supported-capabilities) / the [support matrix](index.md). Instance authorities (SYSADM, SYSCTRL,
SYSMAINT, SYSMON) are **not** needed for monitoring.

Configuration: `MON_ACT_METRICS` must not be `NONE` (default `BASE`) for package-cache statistics.

EXPLAIN (optional) needs the explain tables in the monitoring user's schema (EXPLAIN writes to
`CURRENT USER`'s tables) and DML on them. Created by the instance owner:

```sql
CALL SYSPROC.SYSINSTALLOBJECTS('EXPLAIN', 'C', CAST(NULL AS VARCHAR(128)), 'DBMMON');
-- then, for every table in schema DBMMON (EXPLAIN_STATEMENT, EXPLAIN_OPERATOR, …):
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE DBMMON.EXPLAIN_STATEMENT TO USER DBMMON;
GRANT EXPLAIN ON DATABASE TO USER DBMMON;
-- plus SELECT on the application tables whose statements you want to explain
```

`backend/tests/integration/db2/custom/dbm-it-users.sh` performs exactly these steps.

### DBA management account

```sql
GRANT CONNECT ON DATABASE TO USER DBMADM;
GRANT ACCESSCTRL ON DATABASE TO USER DBMADM;    -- GRANT/REVOKE object privileges (roles need SECADM)
GRANT CREATEIN, ALTERIN ON SCHEMA APP TO USER DBMADM;
GRANT WLMADM ON DATABASE TO USER DBMADM;        -- WLM_CANCEL_ACTIVITY (cancel a statement)
```

`ACCESSCTRL` grants and revokes object privileges and database authorities (except ACCESSCTRL,
DATAACCESS, DBADM and SECADM). Creating roles and granting role membership need **SECADM** (or
`WITH ADMIN OPTION` on the role); DBManager reports SQL0552N as a permission error if the admin
account lacks it. Grant SECADM only if you want role management from DBManager.

Forcing applications (`FORCE APPLICATION`) needs SYSMAINT (an OS group in the DBM configuration);
leave it out unless you want DBManager to terminate sessions. REORG and RUNSTATS are only ever
recommended by DBManager, so no authority for them is required.

See [Least-privilege accounts](permissions.md) for the rules that apply to every engine.

## Supported capabilities

All matrix rows are FULL except *databases* and *users* (PARTIAL: Db2 users are authorization IDs found in the grant catalog), *wait events*, *replication* (HADR, PARTIAL), *read-only transactions* (PARTIAL), *administration* (PARTIAL: no CREATE USER — identities are external) and *explain analyze* (not supported: needs an activity event monitor). Monitoring uses the `MON_GET_*` table functions.

The authoritative per-capability status is in the [support matrix](index.md#implemented-capabilities) and,
for a registered server, on its **Overview → Capabilities** (runtime discovery refines every cell).

## Inventory, workspace and storage notes

* **One database per registration.** A Db2 LUW connection is to one database. Workspace statements
  and EXPLAIN for another database are refused with "this db2 connection is to database 'SAMPLE':
  register 'OTHER' as its own server to run statements there" — register each database as its own
  server ([Troubleshooting](../troubleshooting/engines.md#db2-luw-one-database-per-registration)).
* **Privileges** are read from `SYSCAT.DBAUTH`, `ROLEAUTH`, `SCHEMAAUTH`, `TABAUTH`, `COLAUTH`,
  `ROUTINEAUTH`, `PACKAGEAUTH`, `SEQUENCEAUTH`, `INDEXAUTH`, `TBSPACEAUTH` and `MODULEAUTH` (system
  schemas and `NULLID` packages excluded), so *who has access* covers column, routine, package,
  sequence, index, tablespace and module grants as well as tables.
* **Largest tables** come from catalog statistics (`SYSCAT.TABLES.CARD` and `FPAGES` × page size,
  index leaf pages from `SYSCAT.INDEXES.NLEAF`): they are as current as the last `RUNSTATS`
  (`stats_time` is returned with each row) and tables without statistics are not listed. The shape
  is the common one (`schema`, `name`, `row_estimate`, `table_bytes`, `index_bytes`,
  `total_bytes`).

## Known limitations

* Users are authenticated outside Db2 (OS/LDAP/Kerberos); "create user" is not offered.
* **REORG and RUNSTATS are only recommended, never run.**
* EXPLAIN needs explain tables in the monitoring user's schema.
* `SQLADM` also permits `WLM_CANCEL_ACTIVITY` — use EXECUTE grants on `MON_GET_*` for a strictly read-only identity.

## Engine-specific metrics

Besides the [common metric catalog](../monitoring/metric-catalog.md), this adapter reports:
`db2.bufferpool_hit_ratio`, `db2.cpu_time_us`, `db2.lock_escalations`, `db2.lock_escalations_total`, `db2.lock_timeouts`, `db2.lock_wait_ms`, `db2.lock_waits`, `db2.log_used_bytes`, `db2.log_used_percent`, `db2.pkg_cache_inserts`, `db2.pkg_cache_lookups`, `db2.rows_returned_to_application`, `db2.sort_overflows`, `db2.sorts`, `db2.temp_physical_reads`

## Security checks

| Rule | Checks |
|---|---|
| `DB2-SEC-001` | Authorities granted to PUBLIC |
| `DB2-SEC-002` | DBADM held by application/batch users |
| `DB2-SEC-003` | db2audit not active |
| `DB2-SEC-004` | Unencrypted authentication / no TLS port |
| `DB2-SEC-005` | Data access / access control authorities |
| `DB2-SEC-006` | Db2 release out of support |
| `DB2-SEC-007` | Tables readable by PUBLIC |

The common rules `GEN-SEC-001` (platform connects without TLS) and `GEN-SEC-002` (dormant
privileged accounts) also apply.

## Recommendations

| Rule | Checks |
|---|---|
| `DB2-PERF-001` | Low buffer pool hit ratio |
| `DB2-PERF-002` | Tables need REORG |
| `DB2-PERF-003` | RUNSTATS overdue |
| `DB2-PERF-004` | Sort and package cache overflows |
| `DB2-PERF-005` | Lock escalations |
| `DB2-PERF-006` | Active transaction log nearly full |
| `DB2-PERF-007` | Tables in REORG-pending state |
| `DB2-PERF-008` | Low package cache hit ratio |
| `DB2-PERF-009` | Deadlocks and lock timeouts |

Plus the 12 engine-independent performance rules `GEN-PERF-001` … `GEN-PERF-012` (connections,
cache, blocking, deadlocks, replication lag, long transactions, expensive queries, spills, CPU,
storage, query latency regressions and storage forecasts). Recommendations are advice with
evidence; DBManager never executes them.

**Rule count:** 16 engine-specific rules (7 security, 9 performance) plus the 14
engine-independent rules, which apply to every engine. Across all engines DBManager ships 61
performance and 55 security rules; the full catalog is served by `GET /api/v1/advisor/rules`.

## Troubleshooting

* `SQL30082N`: authentication failed (OS/LDAP password).
* `SQL1013N` / `SQL30061N`: database not found.
* "this db2 connection is to database …: register … as its own server": the workspace targets a
  database other than the registered one; register that database separately.
* Explain fails with *explain tables are missing* or `SQL0551N … EXPLAIN_INSTANCE`: create the tables and grant DML on them (below).
* Monitoring shows *Privilege required*: grant `SQLADM` or EXECUTE on the `MON_GET_*` functions ([Db2 monitoring privileges](../troubleshooting/engines.md#db2-monitoring-privileges)).
