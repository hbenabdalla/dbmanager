# PostgreSQL

## Supported versions

PostgreSQL 13–17 (validated against 16.15). Amazon RDS/Aurora and Azure Flexible Server work with the restricted privileges those services grant.

## Driver

`asyncpg` (native asyncio protocol; no client library). PostgreSQL is the reference adapter (`backend/app/adapters/postgres`).

## Connection parameters

Host, port (5432), database (the database the monitoring role connects to; other databases are reached with the same credential), username, password.

## TLS

TLS mode maps to `sslmode`: `disable`, `prefer`, `require`, `verify-ca`, `verify-full` (default for new servers). Upload a private CA as PEM for `verify-ca`/`verify-full`.

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

DBManager sends passwords for `CREATE ROLE` as SCRAM-SHA-256 verifiers, so the clear text never
reaches the server logs.

See [Least-privilege accounts](permissions.md) for the rules that apply to every engine.

## Supported capabilities

Everything in the matrix is FULL except *query statistics*, which needs the `pg_stat_statements` extension (**REQUIRES EXTENSION** until it is installed), and *object DDL* (PARTIAL: DDL is reconstructed from the catalog). CPU and memory are not exposed by PostgreSQL itself.

The authoritative per-capability status is in the [support matrix](index.md#implemented-capabilities) and,
for a registered server, on its **Overview → Capabilities** (runtime discovery refines every cell).

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

Plus the common performance rules `GEN-PERF-001` … `GEN-PERF-010`. Recommendations are advice with
evidence; DBManager never executes them.

## Troubleshooting

* *Query statistics require extension*: add `pg_stat_statements` to `shared_preload_libraries`, restart, `CREATE EXTENSION pg_stat_statements` in the connected database; the capability turns FULL at the next discovery.
* Other sessions' SQL shows `<insufficient privilege>`: grant `pg_monitor` (or `pg_read_all_stats`).
* `PG-SEC-012` fires: the monitoring role is a superuser — replace it with a `pg_monitor` role.
