# Database backup

Database backups use each engine's own tooling through a provider (`app/backup/engines.py`). What
each engine supports is listed in [Engine support](engine-support.md); DBManager never claims a
capability a provider does not implement.

## Requirements

* An **administration credential** for the server (Server → Credentials). Backups run with it;
  the monitoring credential is not used.
* For restores additionally: the server must be in *Administration enabled* mode.
* For PostgreSQL, MySQL and MariaDB: the client tools on the DBManager host (the Docker image
  ships `pg_dump`/`pg_restore` 17 and the MariaDB client; see [Engine support](engine-support.md)).
* Simulated servers are never backed up.

## Scope and bulk backups

A database backup (manual or by policy) selects servers by id, environment, site and/or tag
(`key` or `key=value`). Each server contributes its default database, or the databases listed in
the scope. Every (server, database) pair is an **asset** with its own progress and result; when
some fail the job ends `PARTIAL_SUCCESS` and each failure is shown with its (redacted) error.

## Where the file goes

| Engine | File written by | Stored in the target |
|---|---|---|
| PostgreSQL, MySQL, MariaDB | DBManager (client tool) | yes — encrypted, checksummed, verified |
| SQL Server, Oracle, Db2 LUW | the database server | no — it stays on the database server (`location_kind = server`) |

Server-side files are recorded and verified with the engine's own check, but DBManager cannot
copy or delete them: include that directory in your file-level backups.

## Passwords

Passwords reach external tools only through the environment (`PGPASSWORD`) or a temporary option
file readable by the DBManager process only (mode `0600`) that is deleted afterwards. Tool output
is redacted before it is stored or shown.
