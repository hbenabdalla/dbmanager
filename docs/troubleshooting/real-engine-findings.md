# Findings from real-engine validation

Problems found while running the real-engine suite against official vendor images, how they
showed up, and what DBManager does now. Each one is covered by a regression test. The general
engine troubleshooting is in [Engine-specific problems](engines.md).

## SQL Server

| Symptom | Cause | Resolution |
|---|---|---|
| `DRIVER_NOT_INSTALLED`, Database drivers page shows ODBC missing | Microsoft ODBC Driver 18 not in the image (blocked `packages.microsoft.com` at build time) | The image build fails instead of shipping without the driver unless `MSODBC_OPTIONAL=true`; see [ODBC driver](engines.md#sql-server-odbc-driver-missing). Validated with ODBC Driver 18.7 / pyodbc 5.3. |
| Locks, CPU, query statistics show *Privilege required* | DMVs need `VIEW SERVER STATE` | Grant `VIEW SERVER STATE`; capability discovery names it. |
| Login fails on a self-signed certificate | TLS mode `verify-full` against the container's generated certificate | Use `require` with *trust server certificate* (test setting) or upload the CA. |
| Backup succeeded on the server but DBManager reported a failure (0.12.0) | Size and checksum were read from `msdb.dbo.backupset`, which a `db_backupoperator` cannot read | Read from `RESTORE HEADERONLY` of the written file (fixed in 0.13.0). |
| Backup error shown as an adapter error (SQL Server, Oracle, Db2 in 0.12.0) | Engine errors were not converted into backup errors | Converted, with the engine message kept. |

## Oracle

| Symptom | Cause | Resolution |
|---|---|---|
| Only `CDB$ROOT` objects and tablespaces | Connected to the CDB service (`XE`) instead of the PDB (`XEPDB1`) | Register the PDB service; the Storage tab notes when it shows root tablespaces. Common `C##` users need `CONTAINER_DATA`. |
| *Privilege required* on monitoring pages | No `SELECT_CATALOG_ROLE` | Grant it (plus `CREATE SESSION`); no `DBA` needed. |
| TCPS not tested | The XE image has no TCPS listener | Recorded as a skip, not a pass (*external validation required* for TCPS). |

AWR, ASH, ADDM, SQL Monitor and SQL Tuning Advisor are never queried (Diagnostics and Tuning Packs off).

## Db2 LUW

| Symptom | Cause | Resolution |
|---|---|---|
| Integration setup hangs at `SYSINSTALLOBJECTS` | An interrupted earlier setup still held catalog locks and Db2's default `LOCKTIMEOUT` is -1 (wait forever) | The setup stops earlier runs and CLP back-ends, sets `CURRENT LOCK TIMEOUT 60`, skips work already done, and runs under a 15-minute timeout. |
| First start takes several minutes | The image creates the instance and database on first start | The scripts wait for the health check; see [Integration tests](../operations/integration-tests.md). |
| Monitoring *Privilege required* | `MON_GET_*` table functions need `SQLADM` (or `DATAACCESS`/`DBADM`) and `EXECUTE` on the `SYSPROC` functions used | Grants in [Db2 monitoring privileges](engines.md#db2-monitoring-privileges). |
| Online backup fails `SQL2413N` | The database uses circular logging | Online backups need archive logging (`LOGARCHMETH1`); DBManager reports `REQUIRES_CONFIGURATION`. |
| Backup ran as the instance owner | `BACKUP` needs an instance authority | Validated with **SYSMAINT** through `SYSMAINT_GROUP` (backup operator `DBMBK`), not SYSADM. |
| Generated restore command | The roll-forward needs the logs contained in the online image | The command extracts them with `LOGTARGET <dir>` and rolls forward with `OVERFLOW LOG PATH (<dir>)`. |

Db2 LUW containers do not validate Db2 for z/OS (*external validation required*).

## MySQL

| Symptom | Cause | Resolution |
|---|---|---|
| Query statistics empty / *Configuration required* | `performance_schema` off, or no `SELECT` on it | Enable it (restart) and grant `SELECT ON performance_schema.*`. |
| Restore fails `ERROR 1419 … SUPER privilege and binary logging is enabled` | MySQL 8 enables binary logging by default; creating triggers and routines without `SUPER` needs `log_bin_trust_function_creators = ON` | DBManager checks before creating anything and refuses with the fix (0.13.0); the DBA decides to set the variable. |
| Restore fails creating a view or trigger owned by another account | `DEFINER` of another account needs `SET_USER_ID` / `SUPER` | `DEFINER` clauses are removed by default (objects belong to the restoring account). |
| A trigger created as `` `src`.trg `` | MySQL stores trigger bodies unqualified, so the dump can be restored under another name | Allowed for MySQL; refused when the dump text still names the source database (MariaDB). |

## MariaDB

| Difference from MySQL | Handling |
|---|---|
| `REPLICA MONITOR` instead of `REPLICATION CLIENT` (10.5.9+) | The monitoring grants differ per flavor. |
| `INFORMATION_SCHEMA.INNODB_SYS_TABLESPACES` instead of `INNODB_TABLESPACES` | Queried per flavor. |
| Trigger definitions keep the schema qualifier as written | Cross-name restores of such dumps are refused instead of changing the source database. |
| `ANALYZE FORMAT=JSON` instead of `EXPLAIN ANALYZE` | Separate plan parser. |

## PostgreSQL

| Symptom | Cause | Resolution |
|---|---|---|
| Restore fails `unrecognized configuration parameter "transaction_timeout"` | `pg_restore` 17 (image client) against a PostgreSQL 16 server | Older servers are restored through a rendered script without the unknown settings, run by `psql` with `ON_ERROR_STOP`. |
| Other users' SQL shows `<insufficient privilege>` | Missing `pg_monitor` / `pg_read_all_stats` | Grant `pg_monitor`. |

## OpenLDAP

| Symptom | Cause | Resolution |
|---|---|---|
| Group search returns nothing for the service account | The directory's ACLs allowed only `self` reads | The service account needs read access to users and groups ([LDAP](../administration/ldap.md)). |
| A user's groups listed twice | Groups found both through `memberOf` and the group search | Distinguished names are de-duplicated (case- and space-insensitive). |
