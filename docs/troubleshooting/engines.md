# Engine-specific problems

## PostgreSQL

* *Query statistics require extension*: add `pg_stat_statements` to `shared_preload_libraries`,
  restart, run `CREATE EXTENSION IF NOT EXISTS pg_stat_statements;`; the capability turns FULL at
  the next discovery.
* Other users' SQL shows `<insufficient privilege>`: grant `pg_monitor` (or `pg_read_all_stats`).

## MySQL and MariaDB

* Query statistics empty or *Configuration required*: `performance_schema` must be `ON` (restart
  required) and the account needs `SELECT` on `performance_schema.*`.
* Replication status missing: grant `REPLICATION CLIENT` (MariaDB 10.5.9+: `REPLICA MONITOR`).

## SQL Server: ODBC driver missing {#sql-server-odbc-driver-missing}

Connection tests return `DRIVER_NOT_INSTALLED` and **Platform → Database drivers** shows pyodbc /
Microsoft ODBC Driver 18 as missing. The backend image installs `msodbcsql18` from
`packages.microsoft.com` at build time (`INSTALL_MSODBC=true`, the default). Rebuild the image with
network access to that repository:

```bash
docker compose build --build-arg INSTALL_MSODBC=true backend
docker compose up -d backend
```

When the image is built outside Docker, install `msodbcsql18` and unixODBC on the backend host.

Other SQL Server problems:

* Certificate error at login: TLS mode `require` for self-signed certificates, or upload the CA /
  set *Host name in certificate*.
* Locks, CPU or query statistics show *Privilege required*: grant `VIEW SERVER STATE`.

## Oracle: TCPS and Native Network Encryption {#oracle-tcps}

`DPY-3001` means the listener requires Oracle Native Network Encryption, which python-oracledb thin
mode cannot negotiate. Offer a TCPS (TLS) endpoint on the listener and register the server with
protocol `tcps`, the TCPS port and, where needed, the *SSL server certificate DN*
([Oracle TLS](../database-engines/oracle.md#tls)). Note that TCPS has not been exercised by the
real-engine suite (the XE image has no TCPS listener).

* Unknown service: reported as *database not found* (`ORA-12514` / `DPY-6001`); check the service
  name with `lsnrctl services`.

## Oracle: privileges {#oracle-privileges}

* Monitoring features show *Privilege required*: the account needs `CREATE SESSION` and
  `SELECT_CATALOG_ROLE` (or `SELECT ANY DICTIONARY`) — see [Oracle](../database-engines/oracle.md#privileges).
* Only `CDB$ROOT` is listed from the root: `ALTER USER c##… SET CONTAINER_DATA = ALL CONTAINER = CURRENT`.
* "Create user" fails with `ORA-01031`: the admin account needs `CREATE SESSION … WITH ADMIN OPTION`.
* AWR, ASH, ADDM and SQL Tuning features are never used; their absence is not an error
  ([licensed features](../database-engines/oracle.md#licensed-features-diagnostics-and-tuning-pack)).

## Db2 LUW: connection {#db2-connection}

* `SQL30082N`: authentication failed — Db2 authenticates against the operating system, LDAP or
  Kerberos, so check the OS/LDAP user and password.
* `SQL1013N` / `SQL30061N`: database not found — check the database name (not the instance name).
* TLS: a TLS mode other than `disable` connects with `SECURITY=SSL` to the instance's
  `SSL_SVCENAME` port; supply the server certificate or CA.

## Db2 LUW: monitoring privileges {#db2-monitoring-privileges}

Monitoring shows *Privilege required* when the account cannot execute the `MON_GET_*` table
functions. Grant:

```sql
GRANT SQLADM ON DATABASE TO USER DBMMON;
GRANT EXECUTE ON FUNCTION SYSPROC.ENV_GET_SYSTEM_RESOURCES TO USER DBMMON;
GRANT SELECT ON SYSIBMADM.DBCFG TO USER DBMMON;
GRANT SELECT ON SYSIBMADM.DBMCFG TO USER DBMMON;
```

Explain fails with *explain tables are missing* or `SQL0551N … EXPLAIN_INSTANCE`: create the explain
tables and grant DML on them ([Db2 LUW](../database-engines/db2-luw.md#privileges)).

## Db2 for z/OS

* `SQL1598N` / `SQL8002N`: Db2 Connect license missing — install the license file in the client or
  activate Db2 Connect on the subsystem.
* Connection refused by RACF: the ID needs DDF access (`DSNR` class, `ssid.DIST`).
