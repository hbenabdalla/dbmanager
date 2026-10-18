# MySQL

## Supported versions

MySQL 8.0 and 8.4 (validated against 8.4.11 and 8.0.46). Amazon RDS/Aurora MySQL is supported with its restricted privilege model.

## Driver

`PyMySQL` (pure Python, TLS and `caching_sha2_password`). Shared MySQL-family code lives in `backend/app/adapters/mysql`.

## Connection parameters

Host, port (3306), default database (optional), username, password.

## TLS

TLS modes `disable`/`prefer`/`require`/`verify-ca`/`verify-full` build an SSL context; combine with `REQUIRE SSL` on the accounts (see below).

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

Grant only the database privileges you want DB Manager to be able to delegate (`WITH GRANT OPTION`
on the schemas concerned). Passwords in `CREATE USER` are sent as bound parameters.

See [Least-privilege accounts](permissions.md) for the rules that apply to every engine.

## Supported capabilities

All matrix rows are FULL except memory metrics and wait events (PARTIAL: derived from status counters and `performance_schema` waits). Query statistics come from `performance_schema.events_statements_summary_by_digest` and need `performance_schema=ON` (default in 8.0).

The authoritative per-capability status is in the [support matrix](index.md#implemented-capabilities) and,
for a registered server, on its **Overview → Capabilities** (runtime discovery refines every cell).

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

Plus the common performance rules `GEN-PERF-001` … `GEN-PERF-010`. Recommendations are advice with
evidence; DB Manager never executes them.

## Troubleshooting

* Query statistics empty: check `performance_schema` is ON and `SELECT` on `performance_schema.*` is granted.
* Replication status missing: grant `REPLICATION CLIENT`.
* `MYSQL-SEC-005`: accounts still use `mysql_native_password`, deprecated in 8.0 and removed in 9.0.
