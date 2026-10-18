# Deployment

## Docker Compose (base deployment)

Four containers: `database-manager-frontend` (nginx, SPA + reverse proxy), `database-manager-backend`
(FastAPI API + scheduler + workers), `database-manager-postgres` (application database) and
`database-manager-timeseries` (VictoriaMetrics, monitoring history; internal network only, no
published port). Start-up is ordered by health checks: PostgreSQL and the time-series database
must be healthy before the backend starts, and the frontend waits for the backend. See
[timeseries-storage.md](../monitoring/timeseries-storage.md).

```bash
./scripts/init-env.sh        # Windows: powershell -ExecutionPolicy Bypass -File scripts\init-env.ps1
docker compose up --build
```

`init-env` copies `.env.example` and fills POSTGRES_PASSWORD, DBM_MASTER_KEY,
DBM_BOOTSTRAP_ADMIN_PASSWORD and DBM_SAMPLE_MONITOR_PASSWORD with generated values.

Open <http://localhost:8080> and sign in as `admin` with `DBM_BOOTSTRAP_ADMIN_PASSWORD`.

Startup order: PostgreSQL becomes healthy → backend runs Alembic migrations, syncs system roles,
creates the first admin (only when no user exists) → backend `/readyz` turns healthy → frontend
starts. No demo data is created; the fleet starts empty (see [simulation.md](../administration/simulation.md)).

**Upgrading**: migrations are incremental and run at startup (`0002` adds the `data_source`
classification and the `simulation_runs` table without deleting anything). Back up the database
volume before upgrading, as for any release.

| Network | Members | Purpose |
|---|---|---|
| `data` (internal) | postgres, timeseries, backend | application and time-series databases are not reachable from outside |
| `app` | backend, frontend | API traffic; backend egress to target databases |

Volumes: `pgdata` (application database) and `tsdata` (monitoring history). Nothing else is persistent.

### Registering the bundled PostgreSQL as a real target

With `DBM_SAMPLE_MONITOR_PASSWORD` set before the first start, the application database gets a
`dbm_monitor` role with `pg_monitor` and `pg_stat_statements` preloaded. Register it with host
`postgres`, port `5432`, database `dbmanager`, TLS mode `prefer` (inside the compose network),
user `dbm_monitor`.

### TLS

See [Reverse proxy and TLS](reverse-proxy-tls.md) for a full example.

Terminate TLS in front of the frontend (reverse proxy, load balancer, ingress) and keep
`DBM_COOKIE_SECURE=true`, `DBM_PUBLIC_BASE_URL=https://…`. Set `DBM_HTTP_BIND=0.0.0.0` only when
the host is protected by that proxy.

## Environment variables (backend)

| Variable | Default | Description |
|---|---|---|
| `DBM_DATABASE_URL` | — | `postgresql+asyncpg://user:pass@host:5432/db` (PostgreSQL 15+) |
| `DBM_MASTER_KEY`, `DBM_MASTER_KEY_ID`, `DBM_PREVIOUS_MASTER_KEYS` | — / `k1` / — | secret encryption key ring |
| `DBM_PROCESS_ROLE` | `all` | `api`, `worker` or `all` |
| `DBM_ENV` | `production` | `development` allows a generated bootstrap password |
| `DBM_BOOTSTRAP_ADMIN_USERNAME/PASSWORD/EMAIL` | `admin` / — | first admin |
| `DBM_DEMO_MODE` | — | **deprecated, ignored** (a warning is logged). Demo data is generated under Platform → Simulation |
| `DBM_PUBLIC_BASE_URL` | `http://localhost:8080` | used for OIDC redirect URIs |
| `DBM_COOKIE_SECURE`, `DBM_COOKIE_DOMAIN` | `true` / — | session cookie attributes |
| `DBM_SESSION_TTL_MINUTES`, `DBM_SESSION_IDLE_TIMEOUT_MINUTES` | 480 / 30 | sessions |
| `DBM_LOGIN_MAX_FAILURES`, `DBM_LOGIN_LOCKOUT_MINUTES`, `DBM_LOGIN_RATE_PER_IP_PER_MINUTE` | 5 / 15 / 20 | brute-force protection |
| `DBM_TRUSTED_PROXIES` | loopback | CIDRs whose `X-Forwarded-For` is trusted for client IPs |
| `DBM_CORS_ORIGINS` | — | only needed when the UI is served from another origin |
| `DBM_COLLECTOR_CONCURRENCY`, `DBM_COLLECTOR_BATCH_SIZE` | 16 / 32 | concurrent target connections per worker |
| `DBM_TARGET_CONNECT_TIMEOUT_SECONDS`, `DBM_TARGET_STATEMENT_TIMEOUT_SECONDS` | 8 / 30 | target timeouts |
| `DBM_CIRCUIT_FAILURE_THRESHOLD` | 3 | failures before collection backs off |
| `DBM_METRICS_RETENTION_DAYS`, `DBM_JOB_HISTORY_RETENTION_DAYS` | 35 / 7 | retention |
| `DBM_WORKSPACE_MAX_ROWS`, `DBM_WORKSPACE_DEFAULT_ROWS`, `DBM_WORKSPACE_TIMEOUT_SECONDS` | 5000 / 500 / 30 | SQL workspace limits |
| `DBM_SECRET_FILE_DIRS` | `/run/secrets,/var/run/secrets,/etc/dbmanager/secrets` | allowed `file` secret locations |
| `DBM_RUN_MIGRATIONS_ON_START` | `true` | set `false` when a migration Job runs them |
| `DBM_LOG_LEVEL`, `DBM_LOG_JSON` | `INFO` / `true` | logging |

## Kubernetes / OpenShift

Reference manifests in `deployment/kubernetes/` and `deployment/openshift/`:

* `dbmanager-api` Deployment (`DBM_PROCESS_ROLE=api`, N replicas, stateless — sessions are in PostgreSQL);
* `dbmanager-worker` Deployment (`python -m app.worker`, M replicas). One worker holds the scheduler
  advisory lock; all workers claim jobs with `FOR UPDATE SKIP LOCKED`, so scaling is linear;
* `dbmanager-migrate` Job (`app.cli migrate && app.cli bootstrap`) with `DBM_RUN_MIGRATIONS_ON_START=false`;
* frontend Deployment + Service + Ingress (or an edge Route on OpenShift);
* external PostgreSQL (managed service or an operator such as CloudNativePG / Crunchy);
* secrets from your secret manager (`dbmanager-secrets`).

Images run as non-root (backend UID 10001, group 0; nginx-unprivileged UID 101) and work with
OpenShift's arbitrary UIDs. The backend needs no writable paths except `/tmp`.

## Scaling notes

* Collection opens short-lived connections per job; target load is bounded by
  `workers × DBM_COLLECTOR_CONCURRENCY`.
* Metric history is in the time-series database (see [scaling.md](scaling.md) for sizing and the
  cluster path); PostgreSQL holds only metadata.
* Fleet lists, dashboards and reports filter by RBAC in SQL (`Authorizer.server_filter`).
* Dashboards read `servers.latest_metrics` (one row per server) rather than scanning samples.

## Upgrades and backups

* Back up the application database (pg_dump / PITR) **and** the master key separately; the
  time-series volume holds only monitoring history ([backup-restore.md](../operations/backup-restore.md)).
* New versions apply Alembic migrations on start (or via the Job). `alembic downgrade` is supported
  for the initial schema.
