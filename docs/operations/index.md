# Operations

| Topic | Page |
|---|---|
| Health endpoints, Prometheus metrics, logs | [Health checks, metrics and logs](health-and-logs.md) |
| Upgrading and database migrations | [Upgrade](upgrade.md) |
| Backup and restore | [Backup and restore](backup-restore.md) |
| Real-engine integration tests | [Integration tests](integration-tests.md) |
| Collection jobs and intervals | [Collectors and intervals](../monitoring/collectors.md) |
| Time-series database maintenance | [Time-series storage](../monitoring/timeseries-storage.md) |
| Sizing and scaling | [Scaling](../deployment/scaling.md) |
| Problems and fixes | [Troubleshooting](../troubleshooting/index.md) |

## First steps after installation

1. Sign in as the bootstrap admin and change the password (forced when it was generated).
2. **Platform → Users**: create named accounts or configure LDAP/OIDC; avoid sharing `admin`.
3. **Fleet → Environments / Clusters**: adjust environments; production-tier servers get stricter confirmations.
4. **Fleet → All servers → Register server** with a least-privileged monitoring account:

   ```sql
   -- PostgreSQL
   CREATE ROLE dbm_monitor LOGIN PASSWORD '…';
   GRANT pg_monitor TO dbm_monitor;
   -- optional, for query statistics:
   -- shared_preload_libraries = 'pg_stat_statements' (restart), then in each database:
   CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
   ```

   Add an *administration* credential only if DBAs will run changes from DB Manager.
5. Assign roles with the narrowest scope that works ([rbac.md](../administration/rbac.md)).

## CLI (inside the backend container)

```bash
docker compose exec backend python -m app.cli migrate
docker compose exec backend python -m app.cli bootstrap
docker compose exec backend python -m app.cli simulation status|generate|cleanup [--dry-run|--yes]
docker compose exec backend python -m app.cli metrics status
docker compose exec backend python -m app.cli metrics migrate-to-timeseries [--dry-run] [--batch-size N] [--from-date D] [--to-date D] [--server-id UUID]
docker compose exec backend python -m app.cli reset-password <username>
docker compose exec backend python -m app.cli rotate-master-key
docker compose exec backend python -m app.cli gen-master-key
docker compose exec backend python -m app.cli matrix        # support matrix (markdown)
```

## Demo / simulation data

No demo data is created at startup; a new installation shows an empty fleet with an
onboarding screen. A Platform Administrator generates, regenerates or removes a simulated
fleet under **Platform → Simulation** (or with `python -m app.cli simulation …`). Simulated
servers never connect to anything, carry a *Simulated* badge, and fleet views show real
servers by default whenever any exist. See [simulation.md](../administration/simulation.md), including the
cleanup path for installations that used `DBM_DEMO_MODE`.

## Backups

Back up the PostgreSQL volume (or external database) and store `DBM_MASTER_KEY` in a separate vault.
Restoring the database without the key loses stored target credentials (they can be re-entered).
