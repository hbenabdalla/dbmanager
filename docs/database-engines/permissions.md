# Least-privilege accounts

One page per engine with three least-privilege accounts:

| Account | Used for | Stored as |
|---|---|---|
| **Inventory** | metadata only: databases, schemas, objects, users and grants | monitoring credential (minimal) |
| **Monitoring** | everything KBM DBManager collects in the background: sessions, locks, metrics, query statistics, security and tuning evidence | monitoring credential |
| **DBA management** | guarded administration actions and session termination, only on servers registered as *Administration enabled* | administration credential |

An optional **read-only credential** can be added per server for the SQL workspace.

Rules that apply to every engine:

* Never use `root`, `sa`/sysadmin, `SYS`/SYSDBA or the Db2 instance owner for monitoring.
* A missing privilege never takes the server offline: the affected component shows *Insufficient
  privilege* in **Collection status** and capability discovery names the grant that is missing.
* Passwords are encrypted at rest (AES-256-GCM), write-only in the API, and never logged or audited.
* The account only sees what its scope allows, and so does KBM DBManager: a SQL Server login registered
  at server level lists the roles of the databases it can open (up to 100); an Oracle common user in
  `CDB$ROOT` needs `CONTAINER_DATA = ALL` to see PDB rows; a Db2 LUW registration covers one
  database, so each database needs its own registration (and grants).
* Db2 for z/OS identities live in RACF, ACF2 or Top Secret; the grants on this page are the
  catalog side only and have not been validated against a real subsystem.

| Engine | Page |
|---|---|
| PostgreSQL | [postgresql.md](postgresql.md) |
| MySQL | [mysql.md](mysql.md) |
| MariaDB | [mariadb.md](mariadb.md) |
| Microsoft SQL Server | [sql-server.md](sql-server.md) |
| Oracle Database | [oracle.md](oracle.md) |
| IBM Db2 LUW | [db2-luw.md](db2-luw.md) |
| IBM Db2 for z/OS | [db2-zos.md](db2-zos.md) |
