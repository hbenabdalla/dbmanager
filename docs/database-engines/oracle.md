# Oracle Database

## Supported versions

Oracle Database 12.2 – 23ai (validated against Oracle Database 21c XE 21.3, from the CDB root with a common user and from the PDB `XEPDB1`).

## Driver

`python-oracledb` in **thin mode**: pure Python, no Oracle Client libraries are installed or redistributed. Thin mode cannot negotiate Oracle Native Network Encryption (NNE); servers that *require* NNE fail with `DPY-3001`, and the message explains the TCPS alternative.

## Connection parameters

Host, port (1521), and either a *service name* (recommended; e.g. the PDB service) or a *SID*, *protocol* (`tcp` or `tcps`), *SSL server certificate DN* (TCPS DN matching) and *expire time* (dead-connection detection), username, password.

## TLS

Use protocol `tcps` against a TCPS listener with a wallet; set the DN to match the server certificate. Plain TCP is reported by security rule `ORA-SEC-005` as unencrypted. TCPS was not exercised by the real-engine suite because the XE image has no TCPS listener.

## Privileges

Verified on Oracle Database 21c XE 21.3 (CDB root with a common user, PDB XEPDB1) (2026-10-18) by `scripts/integration-test oracle`: the suite creates exactly these accounts
and checks that monitoring works with them and that administration is refused without the admin grants.

Connect through the **PDB service** for application databases. A common user (`C##…`) connected to
`CDB$ROOT` additionally inventories every open PDB through the `CDB_*` views.

### Minimum inventory account

```sql
CREATE USER dbm_inventory IDENTIFIED BY "...";
GRANT CREATE SESSION TO dbm_inventory;
GRANT SELECT_CATALOG_ROLE TO dbm_inventory;     -- DBA_* views; DBMS_METADATA for other schemas
```

### Monitoring account

```sql
CREATE USER dbm_monitor IDENTIFIED BY "..." PROFILE monitoring_profile;
GRANT CREATE SESSION TO dbm_monitor;
GRANT SELECT_CATALOG_ROLE TO dbm_monitor;       -- V$ and DBA_ views used by the adapter
-- alternative to the role (12c+): GRANT SELECT ANY DICTIONARY TO dbm_monitor;
-- EXPLAIN PLAN uses the session's PLAN_TABLE (public synonym); plans for application SQL
-- need SELECT on the referenced tables:
GRANT READ ON app.orders TO dbm_monitor;        -- only for tables you want to explain
```

From the CDB root (common user):

```sql
CREATE USER c##dbm_monitor IDENTIFIED BY "..." CONTAINER = ALL;
GRANT CREATE SESSION, SELECT_CATALOG_ROLE TO c##dbm_monitor CONTAINER = ALL;
ALTER USER c##dbm_monitor SET CONTAINER_DATA = ALL CONTAINER = CURRENT;   -- see PDB rows in V$/CDB_ views
```

**Never** grant `SYSDBA`, `DBA` or `SELECT ANY TABLE` to the monitoring user.

### Licensing

The adapter reads only views that need no management pack (see [Licensed features](#licensed-features-diagnostics-and-tuning-pack)).
It never queries AWR, ASH, ADDM, SQL Monitoring or the SQL Tuning Advisor, whatever the account's
privileges, so `SELECT_CATALOG_ROLE` does not lead to Diagnostics/Tuning Pack usage by DB Manager.
The server's *Diagnostics Pack* and *Tuning Pack* flags stay **disabled** unless an administrator
records that the database is licensed.

### DBA management account

```sql
CREATE USER dbm_admin IDENTIFIED BY "...";
GRANT CREATE USER, ALTER USER, DROP USER, CREATE ROLE TO dbm_admin;
GRANT CREATE SESSION TO dbm_admin WITH ADMIN OPTION;  -- "Create user" grants CREATE SESSION to the new user
GRANT GRANT ANY ROLE TO dbm_admin;              -- or grant specific roles WITH ADMIN OPTION
GRANT ALTER SYSTEM TO dbm_admin;                -- KILL SESSION / CANCEL SQL
GRANT CREATE ANY INDEX TO dbm_admin;            -- or make it the schema owner's proxy
```

`CREATE USER` is executed through an anonymous block with the password as a bind variable, so
the password never appears in SQL text, `V$SQL` or the audit trail. PDB creation/removal is not
offered.

Thin-mode note: python-oracledb thin cannot use Native Network Encryption; offer a TCPS listener
for encrypted connections. Accounts need an 11G or 12C password verifier.

See [Least-privilege accounts](permissions.md) for the rules that apply to every engine.

## Supported capabilities

All matrix rows are FULL except *replication* (PARTIAL: Data Guard status from `V$DATAGUARD_STATS` / archive destinations), *explain analyze* (PARTIAL: actual row counts from `V$SQL_PLAN_STATISTICS_ALL` on explicit request), *administration* (PARTIAL: no PDB creation/removal) and *logs* (not implemented). Query statistics come from `V$SQLSTATS`.

The authoritative per-capability status is in the [support matrix](index.md#implemented-capabilities) and,
for a registered server, on its **Overview → Capabilities** (runtime discovery refines every cell).

## Licensed features (Diagnostics and Tuning Pack)

DB Manager **does not include, assume or enable** any Oracle option or management pack.

* The adapter never queries AWR (`DBA_HIST_*`), ASH (`V$ACTIVE_SESSION_HISTORY`), ADDM, SQL
  Monitoring (`V$SQL_MONITOR`), the SQL Tuning Advisor (`DBMS_SQLTUNE`) or the metric views
  (`V$SYSMETRIC`…). A unit test fails the build if one of them appears in the adapter's SQL, and the
  real-engine suite checks `V$SQL` to prove the monitoring user never ran such a statement.
* Each Oracle server has **Diagnostics Pack** and **Tuning Pack** flags, **disabled by default**.
  An administrator may record that the database is licensed; the flags are displayed on the server
  and in the support matrix, but this release does not unlock pack-licensed views even then.
* An Enterprise Edition database does not change this policy: the edition is never taken as
  evidence of a pack licence.
* Active Data Guard is a separate licence; DB Manager only reads `V$DATAGUARD_STATS`.

## Known limitations

* **CDB/PDB**: connected to `CDB$ROOT`, a common user inventories every open PDB through `CDB_*` views (it needs `CONTAINER_DATA=ALL`); connected to a PDB service, the inventory is that PDB.
* Host CPU is derived from `V$OSSTAT` busy/idle deltas.
* Native Network Encryption is not supported in thin mode — use TCPS.

## Engine-specific metrics

Besides the [common metric catalog](../monitoring/metric-catalog.md), this adapter reports:
`oracle.fra_used_percent`, `oracle.hard_parses`, `oracle.logons`, `oracle.long_table_scans`, `oracle.parse_count`, `oracle.pga_used_bytes`, `oracle.physical_writes`, `oracle.processes_utilization`, `oracle.read_bytes`, `oracle.redo_bytes`, `oracle.sga_used_bytes`, `oracle.sorts_disk`, `oracle.sorts_memory`, `oracle.tablespace_used_percent_max`, `oracle.user_calls`, `oracle.write_bytes`

## Security checks

| Rule | Checks |
|---|---|
| `ORA-SEC-001` | Default / sample accounts open |
| `ORA-SEC-002` | PUBLIC EXECUTE on network/file packages |
| `ORA-SEC-003` | Auditing disabled |
| `ORA-SEC-004` | Password profiles without lifetime |
| `ORA-SEC-005` | Unencrypted Oracle Net connections accepted |
| `ORA-SEC-006` | DBA role granted to application accounts |
| `ORA-SEC-007` | Powerful ANY / system privileges granted directly |
| `ORA-SEC-008` | Accounts with only legacy 10G password hashes |
| `ORA-SEC-009` | Oracle release out of support |
| `ORA-SEC-010` | System privileges granted to PUBLIC |
| `ORA-SEC-011` | Profiles without failed-login lockout |

The common rules `GEN-SEC-001` (platform connects without TLS) and `GEN-SEC-002` (dormant
privileged accounts) also apply.

## Recommendations

| Rule | Checks |
|---|---|
| `ORA-PERF-001` | Tablespace nearly full |
| `ORA-PERF-002` | User I/O dominates waits |
| `ORA-PERF-003` | PGA over-allocation |
| `ORA-PERF-004` | Stale optimizer statistics |
| `ORA-PERF-005` | High hard-parse ratio |
| `ORA-PERF-006` | Frequent redo log switches |
| `ORA-PERF-007` | Fast recovery area nearly full |
| `ORA-PERF-008` | Invalid objects / unusable indexes |
| `ORA-PERF-009` | Database in NOARCHIVELOG mode |

Plus the common performance rules `GEN-PERF-001` … `GEN-PERF-010`. Recommendations are advice with
evidence; DB Manager never executes them.

## Troubleshooting

* `DPY-3001`: the listener requires NNE; offer a TCPS endpoint and use protocol `tcps` ([Oracle TCPS](../troubleshooting/engines.md#oracle-tcps)).
* Only `CDB$ROOT` is listed from the root: `ALTER USER c##… SET CONTAINER_DATA = ALL CONTAINER = CURRENT`.
* Unknown service: reported as *database not found* (`ORA-12514` / `DPY-6001`); check the service name with `lsnrctl services`.
* "Create user" fails with ORA-01031: the admin account needs `CREATE SESSION … WITH ADMIN OPTION`.
