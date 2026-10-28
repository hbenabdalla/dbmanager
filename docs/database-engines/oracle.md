# Oracle Database

## Supported versions

Oracle Database 12.2 – 23ai (real-engine validated against Oracle Database 21c XE 21.3, from the
CDB root with a common user and from the PDB `XEPDB1`, over TCP; TCPS was not exercised).

## Driver

`python-oracledb` in **thin mode**: pure Python, no Oracle Client libraries are installed or redistributed. Thin mode cannot negotiate Oracle Native Network Encryption (NNE); servers that *require* NNE fail with `DPY-3001`, and the message explains the TCPS alternative.

## Connection parameters

Host, port (1521), and either a *service name* (recommended; e.g. the PDB service) or a *SID*, *protocol* (`tcp` or `tcps`), *SSL server certificate DN* (TCPS DN matching) and *expire time* (dead-connection detection), username, password.

## TLS

Oracle Net has no opportunistic TLS: a connection is either plain TCP or TCPS. The protocol is the
*protocol* option when set, otherwise `tcps` for `require`, `verify-ca` and `verify-full` and `tcp`
for `disable` and `prefer` (`connect_params` in `adapters/oracle/base.py`).

| Mode | Protocol (default) | TCPS behaviour |
|---|---|---|
| `disable` | `tcp` | — |
| `prefer` | `tcp` (plain) | only with *protocol* `tcps`: encrypted, certificate not validated (validated when a CA is uploaded) |
| `require` | `tcps` | encrypted; certificate not validated, or chain validated when a CA is uploaded; DN not matched |
| `verify-ca` | `tcps` | chain validated against the uploaded CA (or the system trust store); DN not matched |
| `verify-full` | `tcps` | chain validated, host name checked and `ssl_server_dn_match` on; the *SSL server certificate DN* option, when set, must match exactly |

Thin mode needs no wallet on the KBM DBManager side: the CA is uploaded with the server. Plain TCP is
reported by security rule `ORA-SEC-005` as unencrypted. TCPS was not exercised by the real-engine
suite because the XE image has no TCPS listener.

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
Its collectors never query AWR, ASH, ADDM, SQL Monitoring or the SQL Tuning Advisor, whatever the
account's privileges, so `SELECT_CATALOG_ROLE` does not lead to Diagnostics/Tuning Pack usage by
KBM DBManager. The SQL workspace refuses statements that reference pack objects unless the server is
marked as licensed. The server's *Diagnostics Pack* and *Tuning Pack* flags stay **disabled**
unless an administrator records that the database is licensed.

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

KBM DBManager **does not include, assume or enable** any Oracle option or management pack.

* The adapter never queries AWR (`DBA_HIST_*`), ASH (`V$ACTIVE_SESSION_HISTORY`), ADDM, SQL
  Monitoring (`V$SQL_MONITOR`), the SQL Tuning Advisor (`DBMS_SQLTUNE`) or the metric views
  (`V$SYSMETRIC`…). A unit test fails the build if one of them appears in the adapter's SQL, and the
  real-engine suite checks `V$SQL` to prove the monitoring user never ran such a statement.
* Each Oracle server has **Diagnostics Pack** and **Tuning Pack** flags (`licensed_features`:
  `diagnostics_pack`, `tuning_pack`), **disabled by default**. They are set on the registration
  form or with `PATCH /api/v1/servers/{id}`, and shown on the server overview. The collectors
  ignore them: no pack view is queried even when a flag is set.
* The **SQL workspace** checks every statement (`licensed_pack_needed` in
  `adapters/oracle/adapter.py`) for pack objects, matched as whole identifiers:
    * Diagnostics Pack: `DBA_HIST_*` / `CDB_HIST_*`, `V$ACTIVE_SESSION_HISTORY` (and `GV$`),
      `DBA_ADDM_*` / `CDB_ADDM_*`, `DBMS_WORKLOAD_REPOSITORY`, `DBMS_ADDM`, `DBMS_ADVISOR`,
      `DBMS_XPLAN.DISPLAY_AWR`, the metric views (`V$METRIC…`, `V$SYSMETRIC…`, `V$CONMETRIC…`) and
      `AWR_ROOT_*` / `AWR_PDB_*` / `AWR_CDB_*`;
    * Tuning Pack: `DBMS_SQLTUNE`, `DBMS_SQL_MONITOR`, `V$SQL_MONITOR…`, `V$SQL_PLAN_MONITOR`,
      `DBA_SQLTUNE_*`, `DBA_ADVISOR_SQL*`.

    Such a statement is refused before it reaches the database ("this statement uses Oracle
    Diagnostics Pack features … mark the server as licensed for the Diagnostics Pack under
    Server → Settings to run it"), because using these objects records feature usage in the
    database. With the matching flag set, the statement runs like any other.
* An Enterprise Edition database does not change this policy: the edition is never taken as
  evidence of a pack licence.
* Active Data Guard is a separate licence; KBM DBManager only reads `V$DATAGUARD_STATS`.

## Inventory, workspace and storage notes

* **Constraints**: the object browser lists the constraints of a whole schema (primary key,
  unique, foreign key and user-named check constraints, with status, validation and columns);
  from `CDB$ROOT` through the `CDB_*` views.
* **Users from the root**: connected to `CDB$ROOT`, *Users* lists the common users and also the
  local users of every open PDB (`PDB_LOCAL_USERS`: `CDB_USERS` joined with `V$PDBS`, Oracle-
  maintained accounts excluded, at most 5,000 rows), named `PDB:USER`. Their roles and privileges
  are read on the PDB's own registration.
* **Workspace**: statements run in the connected container. Selecting another PDB is refused
  ("statements run in the connected container …: register … (its service name) as its own
  server"); Diagnostics/Tuning Pack objects are refused unless licensed (above).
* **Largest tables** have the common shape (`schema`, `name`, `row_estimate`, `table_bytes`,
  `index_bytes`, `total_bytes`), from `DBA_SEGMENTS` (LOB segments counted with their table) and
  `DBA_TABLES.NUM_ROWS`.

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

Plus the 12 engine-independent performance rules `GEN-PERF-001` … `GEN-PERF-012` (connections,
cache, blocking, deadlocks, replication lag, long transactions, expensive queries, spills, CPU,
storage, query latency regressions and storage forecasts). Recommendations are advice with
evidence; KBM DBManager never executes them.

**Rule count:** 20 engine-specific rules (11 security, 9 performance) plus the 14
engine-independent rules, which apply to every engine. Across all engines KBM DBManager ships 61
performance and 55 security rules; the full catalog is served by `GET /api/v1/advisor/rules`.

## Troubleshooting

* `DPY-3001`: the listener requires NNE; offer a TCPS endpoint and use protocol `tcps` ([Oracle TCPS](../troubleshooting/engines.md#oracle-tcps)).
* Only `CDB$ROOT` is listed from the root: `ALTER USER c##… SET CONTAINER_DATA = ALL CONTAINER = CURRENT`.
* Unknown service: reported as *database not found* (`ORA-12514` / `DPY-6001`); check the service name with `lsnrctl services`.
* "Create user" fails with ORA-01031: the admin account needs `CREATE SESSION … WITH ADMIN OPTION`.
* Workspace: "this statement uses Oracle Diagnostics Pack features …": the statement touches AWR,
  ASH, ADDM, the metric views or a tuning advisor. Mark the pack as licensed on the server only if
  the database really is licensed
  ([Troubleshooting](../troubleshooting/engines.md#oracle-licensed-packs)).
