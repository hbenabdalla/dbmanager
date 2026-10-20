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
* `TLS_ERROR: tls_mode=require but the server did not negotiate TLS (enable TLS on the server, or
  choose prefer/disable)`: with `require`, `verify-ca` or `verify-full` DBManager reads
  `Ssl_cipher` after connecting, because PyMySQL silently continues in plain text when the server
  offers no TLS. Check on the server with `SHOW VARIABLES LIKE 'have_ssl'` (MySQL 8.0:
  `SHOW GLOBAL VARIABLES LIKE 'tls_version'`) and configure `ssl_cert` / `ssl_key`, or register the
  server with `prefer` (opportunistic TLS) or `disable` ([MySQL TLS](../database-engines/mysql.md#tls)).
* Workspace results come from the wrong database: unqualified names resolve in the database
  selected in the workspace (the connection switches to it for the call). Select the intended
  database, or qualify the names.

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
* AWR, ASH, ADDM and SQL Tuning features are never used by the collectors; their absence is not an
  error ([licensed features](../database-engines/oracle.md#licensed-features-diagnostics-and-tuning-pack)).

## Oracle: workspace refuses a statement ("needs the Diagnostics Pack") {#oracle-licensed-packs}

The SQL workspace answers *this statement uses Oracle Diagnostics Pack features (AWR, ASH, ADDM,
SQL Monitoring or the SQL tuning advisors), which need a separate licence: mark the server as
licensed for the Diagnostics Pack under Server → Settings to run it* (or the same for the *Tuning
Pack*). The statement references a pack object — for example `DBA_HIST_*`,
`V$ACTIVE_SESSION_HISTORY`, `V$SYSMETRIC`, `DBMS_XPLAN.DISPLAY_AWR`, `DBMS_SQLTUNE` or
`V$SQL_MONITOR` — and was not sent to the database, because using these objects is recorded as
feature usage. If the database is licensed for the pack, record it on the server: the
*Licensed features* checkboxes of the registration form, or for an existing server
`PATCH /api/v1/servers/{id}` with `{"licensed_features": ["diagnostics_pack"]}` (and/or
`"tuning_pack"`) by a user allowed to change the server. The message names *Server → Settings*,
but this release's Settings tab does not show the flags. Otherwise rewrite the query without the
pack objects.

* Oracle: "statements run in the connected container …: register … (its service name) as its own
  server" — the workspace targets another PDB; register that PDB's service.

## Db2 LUW: connection {#db2-connection}

* `SQL30082N`: authentication failed — Db2 authenticates against the operating system, LDAP or
  Kerberos, so check the OS/LDAP user and password.
* `SQL1013N` / `SQL30061N`: database not found — check the database name (not the instance name).
* TLS: `require`, `verify-ca` and `verify-full` connect with `SECURITY=SSL` to the instance's
  `SSL_SVCENAME` port; upload the CA (or set *Server certificate file*). `prefer` connects in plain
  text — the Db2 CLI has no opportunistic TLS ([Db2 LUW TLS](../database-engines/db2-luw.md#tls)).

## Db2 LUW: one database per registration {#db2-luw-one-database-per-registration}

The workspace or EXPLAIN answers *this db2 connection is to database 'SAMPLE': register 'OTHER' as
its own server to run statements there*. A Db2 LUW connection is to one database; DBManager refuses
the statement rather than run it in the registered database. Register the other database as its
own server (same host and port, its database name) and grant the monitoring account there too.

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
