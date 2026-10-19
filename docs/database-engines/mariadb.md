# MariaDB

## Supported versions

MariaDB 10.6–11.x (validated against 11.4.13 and 10.11.14, with `performance_schema` ON and OFF). Galera clusters are recognised.

## Driver

`PyMySQL`. MariaDB has its own adapter class (`MariaDBAdapter`) on the shared MySQL-family base; registering a MariaDB server as MySQL is reported at discovery.

## Connection parameters

Host, port (3306), default database (optional), username, password.

## TLS

Same TLS modes as MySQL.

## Privileges

MariaDB differs from MySQL: there is no `mysql.global_grants` or `mysql.role_edges`
(roles are in `mysql.roles_mapping`; accounts in `mysql.global_priv`), `performance_schema` is
**OFF by default**, and the privilege for replica status is `REPLICA MONITOR` (10.5.9+).

### Minimum inventory account

```sql
CREATE USER 'dbm_inventory'@'10.0.%' IDENTIFIED BY '...' REQUIRE SSL;
GRANT SHOW DATABASES, SHOW VIEW ON *.* TO 'dbm_inventory'@'10.0.%';
GRANT SELECT ON mysql.user TO 'dbm_inventory'@'10.0.%';
GRANT SELECT ON mysql.global_priv TO 'dbm_inventory'@'10.0.%';
GRANT SELECT ON mysql.roles_mapping TO 'dbm_inventory'@'10.0.%';
```

### Monitoring account

```sql
CREATE USER 'dbm_monitor'@'10.0.%' IDENTIFIED BY '...' REQUIRE SSL;
GRANT PROCESS, REPLICA MONITOR, SHOW DATABASES, SHOW VIEW ON *.* TO 'dbm_monitor'@'10.0.%';  -- 10.5.9+
-- older releases: GRANT REPLICATION CLIENT ON *.* ...
GRANT SELECT ON performance_schema.* TO 'dbm_monitor'@'10.0.%';
GRANT SELECT ON mysql.user TO 'dbm_monitor'@'10.0.%';
GRANT SELECT ON mysql.global_priv TO 'dbm_monitor'@'10.0.%';
GRANT SELECT ON mysql.roles_mapping TO 'dbm_monitor'@'10.0.%';
```

Query statistics need `performance_schema = ON` in the server configuration (restart). Without it
DBManager reports query statistics as *Configuration required* and keeps sessions, locks and
metrics working. Unused-index detection uses `information_schema.INDEX_STATISTICS` when
`userstat = 1`.

Galera clusters: the `wsrep_%` status variables are readable by every account.

### DBA management account

```sql
CREATE USER 'dbm_admin'@'10.0.%' IDENTIFIED BY '...' REQUIRE SSL;
GRANT CREATE, DROP, INDEX, ALTER, CREATE USER ON *.* TO 'dbm_admin'@'10.0.%';
GRANT CONNECTION ADMIN ON *.* TO 'dbm_admin'@'10.0.%';       -- KILL other sessions (10.5.2+)
GRANT SELECT, INSERT, UPDATE, DELETE ON appdb.* TO 'dbm_admin'@'10.0.%' WITH GRANT OPTION;
```

MariaDB roles have no host part; DBManager generates `CREATE ROLE name` accordingly.

See [Least-privilege accounts](permissions.md) for the rules that apply to every engine.

## Supported capabilities

Like MySQL, except *locks* (PARTIAL: lock waits from `information_schema.INNODB_LOCK_WAITS`) and *query statistics*, which are **REQUIRES CONFIGURATION** while `performance_schema=OFF` (the MariaDB default). Galera metrics are collected on clusters.

The authoritative per-capability status is in the [support matrix](index.md#implemented-capabilities) and,
for a registered server, on its **Overview → Capabilities** (runtime discovery refines every cell).

## Known limitations

* `performance_schema` is OFF by default: sessions, locks and metrics still work, query statistics do not.
* Unused-index detection needs `userstat=1`.
* Plans use `EXPLAIN FORMAT=JSON` / `ANALYZE FORMAT=JSON` (rolled back).

## Engine-specific metrics

Besides the [common metric catalog](../monitoring/metric-catalog.md), this adapter reports:
`mariadb.galera_cluster_size`, `mariadb.galera_flow_control_paused`, `mariadb.galera_ready`

## Security checks

| Rule | Checks |
|---|---|
| `MYSQL-SEC-001` | Administrative accounts reachable from any host |
| `MYSQL-SEC-002` | Anonymous accounts |
| `MYSQL-SEC-003` | Secure transport not required |
| `MYSQL-SEC-004` | LOAD DATA LOCAL enabled |
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
| `MYSQL-PERF-007` | Statements reading without an index |
| `MYSQL-PERF-008` | Connection limit nearly reached historically |
| `MYSQL-PERF-009` | Query cache enabled |

Plus the common performance rules `GEN-PERF-001` … `GEN-PERF-010`. Recommendations are advice with
evidence; DBManager never executes them.

## Troubleshooting

* *Configuration required* for query statistics: set `performance_schema=ON` in the server configuration and restart.
* Replica status needs `REPLICA MONITOR` (10.5.9+) or `REPLICATION CLIENT` on older releases.
