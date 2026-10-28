# PostgreSQL

## Supported versions

PostgreSQL 12–17 (the adapter's declared range; real-engine validated against 16.15). Amazon
RDS/Aurora, Azure Database for PostgreSQL and Google Cloud SQL are detected automatically and work
with the restricted privileges those services grant. Version-dependent queries are selected at run
time (for example the lock query on 14+, checkpoint statistics from `pg_stat_checkpointer` on 17+).

## Driver

`asyncpg` (native asyncio protocol; no client library). PostgreSQL is the reference adapter (`backend/app/adapters/postgres`).

## Connection parameters

Host, port (5432), database (the database the monitoring role connects to; other databases are reached with the same credential), username, password.

## TLS

The TLS mode is translated for asyncpg (`_ssl_arg` in `adapters/postgres/adapter.py`):

| Mode | Without an uploaded CA | With an uploaded CA |
|---|---|---|
| `disable` | plain TCP | plain TCP |
| `prefer` | asyncpg `prefer`: TLS when the server offers it, otherwise plain; certificate not validated | TLS **required**, certificate chain validated against the CA, host name not checked |
| `require` | TLS required, certificate not validated | TLS required, chain validated against the CA, host name not checked |
| `verify-ca` | TLS required, chain validated against the system trust store | chain validated against the CA |
| `verify-full` | chain validated against the system trust store **and** host name checked | chain validated against the CA and host name checked |

`verify-full` is the default for new servers. Upload a private CA (PEM) with the server's TLS
settings. Rule `GEN-SEC-001` reports servers registered with `disable` or `prefer`.

## Privileges

### Minimum inventory account

```sql
CREATE ROLE dbm_inventory LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE appdb TO dbm_inventory;   -- every database to inventory
```

The system catalogs (`pg_class`, `pg_namespace`, `pg_roles`, …) are readable by every role. Object
definitions of other owners are visible; table contents are not.

### Monitoring account

```sql
CREATE ROLE dbm_monitor LOGIN PASSWORD '...';
GRANT pg_monitor TO dbm_monitor;                    -- pg_read_all_settings, pg_read_all_stats, pg_stat_scan_tables
GRANT CONNECT ON DATABASE appdb TO dbm_monitor;
-- query statistics need the extension (shared_preload_libraries = 'pg_stat_statements', restart):
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;  -- in the database the monitoring account connects to
```

Without `pg_monitor`, other sessions' queries and wait events are hidden and query statistics show
*Insufficient privilege*. Managed services: on Amazon RDS/Aurora and Azure Flexible Server
`pg_monitor` is grantable; superuser is not available (and not needed).

### DBA management account

Guarded actions (create database/schema/role/user, grant, revoke, create index) and session
termination need:

```sql
CREATE ROLE dbm_admin LOGIN PASSWORD '...' CREATEDB CREATEROLE;
GRANT pg_signal_backend TO dbm_admin;               -- cancel / terminate sessions of non-superusers
-- object-level changes (CREATE INDEX, GRANT on tables) need ownership or the owner's role:
GRANT app_owner TO dbm_admin;
```

KBM DBManager sends passwords for `CREATE ROLE` as SCRAM-SHA-256 verifiers, so the clear text never
reaches the server logs.

See [Least-privilege accounts](permissions.md) for the rules that apply to every engine.

## Supported capabilities

Everything in the matrix is FULL except *query statistics*, which needs the `pg_stat_statements` extension (**REQUIRES EXTENSION** until it is installed), and *object DDL* (PARTIAL: DDL is reconstructed from the catalog). CPU and memory are not exposed by PostgreSQL itself.

The authoritative per-capability status is in the [support matrix](index.md#implemented-capabilities) and,
for a registered server, on its **Overview → Capabilities** (runtime discovery refines every cell).

## SQL workspace and storage

* Workspace statements and EXPLAIN run on a connection to the database selected in the workspace
  (same credential), inside a `READ ONLY` transaction unless a change was confirmed.
* **Storage → Largest tables** returns, like every engine, `schema`, `name`, `row_estimate`,
  `table_bytes`, `index_bytes` and `total_bytes` (from `pg_class` / `pg_total_relation_size`).

## Known limitations

* No host CPU/memory metrics (PostgreSQL does not expose them over SQL).
* `EXPLAIN ANALYZE` runs only on explicit request, inside a transaction that is always rolled back.
* Logs are not read.

## Engine-specific metrics

Besides the [common metric catalog](../monitoring/metric-catalog.md), this adapter reports:
`pg.checkpoints_requested`, `pg.checkpoints_timed`, `pg.conflicts`, `pg.idle_in_transaction_longest_seconds`

## Security checks

| Rule | Checks |
|---|---|
| `PG-SEC-001` | Superuser accounts |
| `PG-SEC-002` | SSL disabled on server |
| `PG-SEC-003` | Unencrypted client connections |
| `PG-SEC-004` | MD5 password hashing |
| `PG-SEC-005` | trust authentication in pg_hba.conf |
| `PG-SEC-006` | PUBLIC can create objects in public schema |
| `PG-SEC-007` | Roles with powerful attributes |
| `PG-SEC-008` | Unsupported PostgreSQL version |
| `PG-SEC-009` | Connection logging disabled |
| `PG-SEC-010` | Direct table grants to login users |
| `PG-SEC-011` | SECURITY DEFINER functions without search_path |
| `PG-SEC-012` | Monitoring account is a superuser |
| `PG-SEC-013` | Login roles without password |

The common rules `GEN-SEC-001` (platform connects without TLS) and `GEN-SEC-002` (dormant
privileged accounts) also apply.

## Recommendations

| Rule | Checks |
|---|---|
| `PG-PERF-001` | shared_buffers at default size |
| `PG-PERF-002` | Frequent requested checkpoints |
| `PG-PERF-003` | Table bloat from dead tuples |
| `PG-PERF-004` | Large table read by sequential scans |
| `PG-PERF-005` | Unused indexes |
| `PG-PERF-006` | Duplicate indexes |
| `PG-PERF-007` | Idle-in-transaction sessions |
| `PG-PERF-008` | pg_stat_statements not available |
| `PG-PERF-009` | Stale planner statistics |

Plus the 12 engine-independent performance rules `GEN-PERF-001` … `GEN-PERF-012` (connections,
cache, blocking, deadlocks, replication lag, long transactions, expensive queries, spills, CPU,
storage, query latency regressions and storage forecasts). Recommendations are advice with
evidence; KBM DBManager never executes them.

**Rule count:** 22 engine-specific rules (13 security, 9 performance) plus the 14
engine-independent rules, which apply to every engine. Across all engines KBM DBManager ships 61
performance and 55 security rules; the full catalog is served by `GET /api/v1/advisor/rules`.

## Troubleshooting

* *Query statistics require extension*: add `pg_stat_statements` to `shared_preload_libraries`, restart, `CREATE EXTENSION pg_stat_statements` in the connected database; the capability turns FULL at the next discovery.
* Other sessions' SQL shows `<insufficient privilege>`: grant `pg_monitor` (or `pg_read_all_stats`).
* `PG-SEC-012` fires: the monitoring role is a superuser — replace it with a `pg_monitor` role.
