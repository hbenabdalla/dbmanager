# MySQL

## Supported versions

MySQL 8.0, 8.4 LTS and 9.x (real-engine validated against 8.4.11 and 8.0.46). MySQL 5.7 works
with reduced support: no roles (`ROLES` not supported), no `EXPLAIN ANALYZE` (needs 8.0.18+), and
lock detail from `INFORMATION_SCHEMA.INNODB_LOCKS` / `INNODB_LOCK_WAITS` (locks involved in waits
only, no metadata locks; PARTIAL). Amazon RDS/Aurora MySQL is supported with its restricted
privilege model.

## Driver

`PyMySQL` (pure Python, TLS and `caching_sha2_password`). Shared MySQL-family code lives in `backend/app/adapters/mysql`.

## Connection parameters

Host, port (3306), default database (optional), username, password.

## TLS

The TLS mode becomes a PyMySQL SSL context (`_open` in `adapters/mysql/base.py`; shared with
MariaDB):

| Mode | Behaviour |
|---|---|
| `disable` | plain TCP (`ssl_disabled`) |
| `prefer` | **opportunistic TLS**, as the `mysql` client does: TLS when the server offers it, without certificate validation (`CERT_NONE`); plain TCP when the server has no TLS. With an uploaded CA the certificate is validated when TLS is used |
| `require` | TLS without certificate validation (validated against the CA when one is uploaded). After connecting, KBM DBManager reads `SHOW SESSION STATUS LIKE 'Ssl_cipher'`: PyMySQL silently stays in plain text when the server offers no TLS, so an empty cipher fails the connection with `TLS_ERROR` ("the server did not negotiate TLS") |
| `verify-ca` | TLS, chain validated against the uploaded CA (or the system trust store), host name not checked; `Ssl_cipher` checked |
| `verify-full` | as `verify-ca`, plus host name check; `Ssl_cipher` checked |

Combine with `REQUIRE SSL` on the accounts (see below) so the server refuses plain connections too.

## Privileges

### Minimum inventory account

```sql
CREATE USER 'dbm_inventory'@'10.0.%' IDENTIFIED BY '...' REQUIRE SSL;
GRANT SHOW DATABASES, SHOW VIEW ON *.* TO 'dbm_inventory'@'10.0.%';
GRANT SELECT ON mysql.user TO 'dbm_inventory'@'10.0.%';          -- accounts and their attributes
GRANT SELECT ON mysql.role_edges TO 'dbm_inventory'@'10.0.%';
GRANT SELECT ON mysql.global_grants TO 'dbm_inventory'@'10.0.%'; -- dynamic privileges
```

`information_schema` shows metadata of objects the account has a privilege on; `SHOW VIEW` adds view
definitions. Without the `mysql.*` grants, users and roles are reported from `information_schema`
privilege views only.

### Monitoring account

```sql
CREATE USER 'dbm_monitor'@'10.0.%' IDENTIFIED BY '...' REQUIRE SSL;
GRANT PROCESS, REPLICATION CLIENT, SHOW DATABASES, SHOW VIEW ON *.* TO 'dbm_monitor'@'10.0.%';
GRANT SELECT ON performance_schema.* TO 'dbm_monitor'@'10.0.%';
GRANT SELECT ON sys.* TO 'dbm_monitor'@'10.0.%';
GRANT SELECT ON mysql.user TO 'dbm_monitor'@'10.0.%';
GRANT SELECT ON mysql.role_edges TO 'dbm_monitor'@'10.0.%';
GRANT SELECT ON mysql.global_grants TO 'dbm_monitor'@'10.0.%';
```

* `PROCESS` — every session in the process list and InnoDB transactions/locks.
* `REPLICATION CLIENT` — `SHOW REPLICA STATUS` (8.0.22+ also accepts `REPLICATION_SLAVE_ADMIN`; not needed).
* `performance_schema` must be ON (default in 8.0) for query digests and lock waits.

No write privilege is needed. Amazon RDS/Aurora: the same grants work for the master user's
delegates; `SUPER` is not available and not needed.

### DBA management account

```sql
CREATE USER 'dbm_admin'@'10.0.%' IDENTIFIED BY '...' REQUIRE SSL;
GRANT CREATE, DROP, INDEX, ALTER, CREATE USER, ROLE_ADMIN ON *.* TO 'dbm_admin'@'10.0.%';
GRANT SELECT, INSERT, UPDATE, DELETE ON appdb.* TO 'dbm_admin'@'10.0.%' WITH GRANT OPTION;  -- to grant them
GRANT CONNECTION_ADMIN ON *.* TO 'dbm_admin'@'10.0.%';   -- KILL other accounts' sessions
```

Grant only the database privileges you want KBM DBManager to be able to delegate (`WITH GRANT OPTION`
on the schemas concerned). Passwords in `CREATE USER` are sent as bound parameters.

See [Least-privilege accounts](permissions.md) for the rules that apply to every engine.

## Supported capabilities

All matrix rows are FULL except memory metrics and wait events (PARTIAL: derived from status counters and `performance_schema` waits). Query statistics come from `performance_schema.events_statements_summary_by_digest` and need `performance_schema=ON` (default in 8.0).

The authoritative per-capability status is in the [support matrix](index.md#implemented-capabilities) and,
for a registered server, on its **Overview → Capabilities** (runtime discovery refines every cell).

## SQL workspace and EXPLAIN

MySQL keeps one connection per registered server. A workspace statement or EXPLAIN for a database
other than the registered default switches the connection to the selected database
(`COM_INIT_DB`, i.e. `USE`) for that call and switches back afterwards, so unqualified names
resolve in the database chosen in the workspace, never silently in the registered default. When
the server has no default database to return to, the connection is closed and the next call opens
a fresh one. Read-only runs use `START TRANSACTION READ ONLY` in the selected database.

**Storage → Largest tables** reports `schema`, `name`, `row_estimate`, `table_bytes`, `index_bytes`
and `total_bytes` from `information_schema.TABLES` (estimates maintained by InnoDB).

## Known limitations

* Statement digests, never full SQL, are used as statistics labels.
* `EXPLAIN ANALYZE` (8.0.18+) only on explicit request, rolled back.
* On RDS/Aurora sessions are terminated with `mysql.rds_kill`.

## Engine-specific metrics

Besides the [common metric catalog](../monitoring/metric-catalog.md), this adapter reports:
`mysql.aborted_clients`, `mysql.aborted_connects`, `mysql.bytes_received`, `mysql.bytes_sent`, `mysql.handler_commit`, `mysql.innodb_buffer_pool_bytes`, `mysql.innodb_buffer_pool_dirty_percent`, `mysql.innodb_buffer_pool_used_percent`, `mysql.innodb_row_lock_waits`, `mysql.max_used_connections`, `mysql.questions`, `mysql.replica_threads_running`, `mysql.select_full_join`, `mysql.slow_queries`, `mysql.sort_merge_passes`, `mysql.table_open_cache_misses`, `mysql.threads_cached`, `mysql.threads_running`, `mysql.tmp_disk_tables`, `mysql.tmp_disk_tables_per_sec`, `mysql.tmp_tables`

## Security checks

| Rule | Checks |
|---|---|
| `MYSQL-SEC-001` | Administrative accounts reachable from any host |
| `MYSQL-SEC-002` | Anonymous accounts |
| `MYSQL-SEC-003` | Secure transport not required |
| `MYSQL-SEC-004` | LOAD DATA LOCAL enabled |
| `MYSQL-SEC-005` | Accounts use the deprecated mysql_native_password plugin |
| `MYSQL-SEC-006` | FILE privilege with unrestricted secure_file_priv |
| `MYSQL-SEC-007` | No audit plugin active |
| `MYSQL-SEC-008` | Server version past vendor end of life |
| `MYSQL-SEC-009` | Accounts without a password |
| `MYSQL-SEC-010` | No password validation |

The common rules `GEN-SEC-001` (platform connects without TLS) and `GEN-SEC-002` (dormant
privileged accounts) also apply.

## Recommendations

| Rule | Checks |
|---|---|
| `MYSQL-PERF-001` | InnoDB buffer pool undersized |
| `MYSQL-PERF-002` | Temporary tables created on disk |
| `MYSQL-PERF-003` | Slow query log disabled |
| `MYSQL-PERF-004` | Tables without primary key |
| `MYSQL-PERF-005` | Unused secondary indexes |
| `MYSQL-PERF-006` | Redundant indexes |
| `MYSQL-PERF-007` | Statements reading without an index |
| `MYSQL-PERF-008` | Connection limit nearly reached historically |
| `MYSQL-PERF-009` | Query cache enabled |

Plus the 12 engine-independent performance rules `GEN-PERF-001` … `GEN-PERF-012` (connections,
cache, blocking, deadlocks, replication lag, long transactions, expensive queries, spills, CPU,
storage, query latency regressions and storage forecasts). Recommendations are advice with
evidence; KBM DBManager never executes them.

**Rule count:** 19 engine-specific rules (10 security, 9 performance) plus the 14
engine-independent rules, which apply to every engine. Across all engines KBM DBManager ships 61
performance and 55 security rules; the full catalog is served by `GET /api/v1/advisor/rules`.

## Troubleshooting

* Query statistics empty: check `performance_schema` is ON and `SELECT` on `performance_schema.*` is granted.
* Replication status missing: grant `REPLICATION CLIENT`.
* `TLS_ERROR: tls_mode=require but the server did not negotiate TLS`: the server has no TLS
  configured (or it is disabled). Enable TLS on the server, or register the server with `prefer`
  or `disable` ([Troubleshooting](../troubleshooting/engines.md#mysql-and-mariadb)).
* `MYSQL-SEC-005`: accounts still use `mysql_native_password`, deprecated in 8.0 and removed in 9.0.
