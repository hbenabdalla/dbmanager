# Engine support

**Backup & Restore → Engine support** shows the same table live, including which client tools the
DBManager host has. Statuses: `AVAILABLE`, `PARTIAL`, `TOOL_MISSING`, `REQUIRES_CONFIGURATION`,
`EXTERNAL_TOOL_REQUIRED`, `NOT_SUPPORTED`.

| Engine | Backup | Restore by DBManager | Physical | PITR |
|---|---|---|---|---|
| [PostgreSQL](engines/postgresql.md) | logical, `pg_dump -Fc`, copied to the target | yes, `pg_restore` into a new database | no | no |
| [MySQL](engines/mysql.md) | logical, `mysqldump --single-transaction` | yes, into a new database | no | no |
| [MariaDB](engines/mariadb.md) | logical, `mariadb-dump --single-transaction` | yes, into a new database | no | no |
| [SQL Server](engines/sqlserver.md) | native `BACKUP DATABASE … COPY_ONLY, CHECKSUM` on the server | yes, `RESTORE … WITH MOVE` as a new database | — | no |
| [Oracle](engines/oracle.md) | Data Pump schema export on the server | yes, Data Pump import with `REMAP_SCHEMA` | RMAN: external | no |
| [Db2 LUW](engines/db2-luw.md) | online `BACKUP DATABASE` via `ADMIN_CMD` (needs archive logging) | external tool required | yes (database image) | no |
| [Db2 for z/OS](engines/db2-zos.md) | external tool required | external tool required | — | — |

Point-in-time recovery is not offered for any engine: logical dumps and copy-only backups do not
form a log chain that DBManager manages. Use the engine's own backup solution for PITR.

## Client tools in the Docker image

The backend image installs `pg_dump`/`pg_restore` 17 from the PostgreSQL project's repository
(dumps PostgreSQL 9.2–17; falls back to Debian's client 15 when the repository is unreachable at
build time) and the MariaDB 10.11 client (`mariadb-dump`, `mariadb`, also as `mysqldump`/`mysql`).
MySQL's own `mysqldump` is not redistributed: for full fidelity with MySQL-specific objects, mount
it and set `DBM_BACKUP_MYSQL_BIN_DIR` (MySQL servers are reported `PARTIAL` while only the MariaDB
client is installed). `INSTALL_BACKUP_TOOLS=false` builds the image without the tools
(`TOOL_MISSING`). `DBM_BACKUP_PG_BIN_DIR` selects another PostgreSQL client directory.
`DBM_BACKUP_TOOL_TIMEOUT_SECONDS` (default 6 h) stops a tool that runs too long.

## Validation status

Real-engine validation is recorded by the integration suite
(`backend/tests/integration/test_backup_engines.py`, capabilities `backup` / `restore`) run with
`scripts/integration-test` against the vendors' container images. The recorded runs: PostgreSQL
16.15, MySQL 8.4.11 (binary logging on), MariaDB 11.4.13 and 10.11, SQL Server 16.0.4305.5, Oracle
21.3 XE and Db2 LUW 11.5.9.0 (backup with a SYSMAINT operator; restore is external). Db2 for z/OS
needs external validation. **Platform → Engine support** shows what the validation matrix of your
build actually records ([engine matrix](../product/engine-matrix.md)).
