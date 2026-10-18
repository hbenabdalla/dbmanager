# Least-privilege accounts

One page per engine with three least-privilege accounts:

| Account | Used for | Stored as |
|---|---|---|
| **Inventory** | metadata only: databases, schemas, objects, users and grants | monitoring credential (minimal) |
| **Monitoring** | everything DB Manager collects in the background: sessions, locks, metrics, query statistics, security and tuning evidence | monitoring credential |
| **DBA management** | guarded administration actions and session termination, only on servers registered as *Administration enabled* | administration credential |

An optional **read-only credential** can be added per server for the SQL workspace.

Rules that apply to every engine:

* Never use `root`, `sa`/sysadmin, `SYS`/SYSDBA or the Db2 instance owner for monitoring.
* A missing privilege never takes the server offline: the affected component shows *Insufficient
  privilege* in **Collection status** and capability discovery names the grant that is missing.
* Passwords are encrypted at rest (AES-256-GCM), write-only in the API, and never logged or audited.

| Engine | Page |
|---|---|
| PostgreSQL | [postgresql.md](postgresql.md) |
| MySQL | [mysql.md](mysql.md) |
| MariaDB | [mariadb.md](mariadb.md) |
| Microsoft SQL Server | [sqlserver.md](sql-server.md) |
| Oracle Database | [oracle.md](oracle.md) |
| IBM Db2 LUW | [db2-luw.md](db2-luw.md) |
| IBM Db2 for z/OS | [db2-zos.md](db2-zos.md) |
