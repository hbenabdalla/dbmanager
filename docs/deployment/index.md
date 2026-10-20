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

Volumes: `pgdata` (application database), `tsdata` (monitoring history), `backups` (the default
`local` backup storage target, or the host directory in `DBM_BACKUP_HOST_DIR`) and `backupwork`
(scratch space for building archives and dumps). Nothing else is persistent.

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

## Environment variables

All backend settings are environment variables with the prefix `DBM_` (`backend/app/core/config.py`,
class `Settings`; unknown variables are ignored). Lists are comma separated. In Docker Compose only
the variables that `docker-compose.yml` passes to a container reach it: the file passes the common
ones from `.env` (see `.env.example`); add any other variable below to the service's
`environment:` block. Secrets can also come from your secret manager (Kubernetes Secret,
External Secrets, …). Engine credentials are never configured here.

### Runtime and process

| Variable | Default | Description |
|---|---|---|
| `DBM_ENV` | `production` | `production`, `development` or `test`; `development` allows a generated bootstrap password |
| `DBM_PROCESS_ROLE` | `all` | `all` (API + scheduler + workers), `api` or `worker` ([scaling](scaling.md)) |
| `DBM_LOG_LEVEL`, `DBM_LOG_JSON` | `INFO` / `true` | logging |
| `DBM_PUBLIC_BASE_URL` | `http://localhost:8080` | external URL: OIDC redirect URIs, links in notifications |
| `DBM_RUN_MIGRATIONS_ON_START` | `true` | set `false` when a migration Job runs them |
| `DBM_WORKER_PORT` | `8001` | port of the probe endpoint (`/healthz`, `/readyz`, `/metrics`) of a stand-alone worker (`python -m app.worker`) |
| `DBM_DEMO_MODE` | `false` | **deprecated, ignored** (a warning is logged). Demo data is generated under Platform → Simulation |

### Application database and secrets

| Variable | Default | Description |
|---|---|---|
| `DBM_DATABASE_URL` | — | `postgresql+asyncpg://user:pass@host:5432/db` (PostgreSQL 15+). Compose builds it from `POSTGRES_USER`, `POSTGRES_PASSWORD` and `POSTGRES_DB` |
| `DBM_DATABASE_POOL_SIZE`, `DBM_DATABASE_MAX_OVERFLOW` | 10 / 10 | connection pool of each backend process |
| `DBM_MASTER_KEY`, `DBM_MASTER_KEY_ID`, `DBM_PREVIOUS_MASTER_KEYS` | — / `k1` / — | secret encryption key ring (base64 32-byte key; previous keys as `id:base64` for decryption only) |
| `DBM_SECRET_FILE_DIRS` | `/run/secrets,/var/run/secrets,/etc/dbmanager/secrets` | allowed locations of `file:` secret references |
| `DBM_BOOTSTRAP_ADMIN_USERNAME`, `DBM_BOOTSTRAP_ADMIN_PASSWORD`, `DBM_BOOTSTRAP_ADMIN_EMAIL` | `admin` / — / `admin@example.local` | first administrator, created only when no user exists |

### Web, sessions and client address

| Variable | Default | Description |
|---|---|---|
| `DBM_COOKIE_SECURE`, `DBM_COOKIE_DOMAIN` | `true` / — | session and CSRF cookie attributes |
| `DBM_SESSION_TTL_MINUTES`, `DBM_SESSION_IDLE_TIMEOUT_MINUTES` | 480 / 30 | session lifetime and idle timeout |
| `DBM_CORS_ORIGINS` | — | only needed when the UI is served from another origin |
| `DBM_TRUSTED_PROXIES` | `127.0.0.1,::1` (Compose: `172.16.0.0/12,127.0.0.1`) | CIDRs whose `X-Forwarded-For` the backend trusts, read right to left ([reverse proxy](reverse-proxy-tls.md#how-the-client-address-is-determined)) |
| `DBM_OUTER_PROXY` | `127.0.0.1/32` | **frontend container**: the one CIDR (your reverse proxy, as nginx sees it) allowed to set `X-Forwarded-For` |

The backend container runs uvicorn with `--no-proxy-headers`. uvicorn's proxy-header mode would
replace the peer address with the leftmost `X-Forwarded-For` entry, which the client chooses,
before DBManager's own right-to-left check runs. `FORWARDED_ALLOW_IPS` is therefore no longer used.
Absolute links are built from `DBM_PUBLIC_BASE_URL`, not from forwarded headers.

### Authentication, MFA and API tokens

| Variable | Default | Description |
|---|---|---|
| `DBM_LOGIN_MAX_FAILURES`, `DBM_LOGIN_LOCKOUT_MINUTES` | 5 / 15 | account lockout |
| `DBM_MFA_REQUIRED_FOR`, `DBM_MFA_ISSUER` | `none` / `DBManager` | TOTP for local accounts: `none`, `admins` or `all` ([MFA](../administration/mfa.md)) |
| `DBM_API_TOKEN_MAX_DAYS` | 365 | longest allowed API-token lifetime (1–3650) ([API tokens](../administration/api-tokens.md)) |
| `DBM_API_TOKEN_AUTH_FAILURES_PER_MINUTE` | 30 | failed Bearer authentications per source address and minute before `429` |

### Rate limits

| Variable | Default | Description |
|---|---|---|
| `DBM_RATE_LIMIT_BACKEND`, `DBM_RATE_LIMIT_POOL_SIZE` | `database` / 5 | counters shared by all replicas in PostgreSQL (own connection pool), or `memory` per process |
| `DBM_LOGIN_RATE_PER_IP_PER_MINUTE` | 20 | sign-in attempts per client address |
| `DBM_API_RATE_PER_USER_PER_MINUTE`, `DBM_API_RATE_TARGET_PER_MINUTE`, `DBM_API_RATE_EXPENSIVE_PER_MINUTE` | 600 / 120 / 30 | [rate limits](../administration/rate-limiting.md) per principal; `0` = off |

### HashiCorp Vault

| Variable | Default | Description |
|---|---|---|
| `DBM_VAULT_ADDR`, `DBM_VAULT_NAMESPACE` | — | Vault address (empty = provider unavailable) and namespace |
| `DBM_VAULT_AUTH` | `token` | `token`, `approle` or `kubernetes` |
| `DBM_VAULT_TOKEN`, `DBM_VAULT_TOKEN_FILE` | — | token authentication |
| `DBM_VAULT_ROLE_ID`, `DBM_VAULT_SECRET_ID`, `DBM_VAULT_SECRET_ID_FILE` | — | AppRole authentication |
| `DBM_VAULT_KUBERNETES_ROLE`, `DBM_VAULT_KUBERNETES_JWT_FILE` | — / service-account token path | Kubernetes authentication |
| `DBM_VAULT_AUTH_MOUNT`, `DBM_VAULT_KV_MOUNT` | `approle`/`kubernetes` / `secret` | auth mount and default KV v2 mount |
| `DBM_VAULT_CA_FILE`, `DBM_VAULT_TIMEOUT_SECONDS`, `DBM_VAULT_CACHE_SECONDS` | — / 10 / 60 | TLS CA bundle, request timeout, in-memory cache of resolved secrets (0 = no cache) |

See [Secret providers](../administration/secret-providers.md).

### Collection

| Variable | Default | Description |
|---|---|---|
| `DBM_COLLECTOR_CONCURRENCY`, `DBM_COLLECTOR_BATCH_SIZE` | 16 / 32 | concurrent target connections per worker, jobs claimed per poll |
| `DBM_SCHEDULER_TICK_SECONDS`, `DBM_WORKER_POLL_SECONDS` | 15 / 2 | scheduler leader tick, worker queue poll |
| `DBM_TARGET_CONNECT_TIMEOUT_SECONDS`, `DBM_TARGET_STATEMENT_TIMEOUT_SECONDS` | 8 / 30 | target timeouts |
| `DBM_CIRCUIT_FAILURE_THRESHOLD` | 3 | consecutive failures before collection backs off |
| `DBM_INTERVAL_FAST_SECONDS`, `DBM_INTERVAL_NORMAL_SECONDS`, `DBM_INTERVAL_SLOW_SECONDS`, `DBM_INTERVAL_VERY_SLOW_SECONDS` | 30 / 60 / 300 / 3600 | collection interval classes ([collectors](../monitoring/collectors.md)) |
| `DBM_COLLECTION_JITTER_RATIO` | 0.2 | spread of scheduled collections over their interval (0 = none) |
| `DBM_QUERY_STATS_TOP_N`, `DBM_QUERY_FINGERPRINTS_MAX_PER_SERVER` | 50 / 2000 | query-telemetry cardinality guards |
| `DBM_METRICS_RETENTION_DAYS`, `DBM_JOB_HISTORY_RETENTION_DAYS` | 35 / 7 | retention of the legacy PostgreSQL metric tables and of job history |

### Time-series database

| Variable | Default | Description |
|---|---|---|
| `DBM_TIMESERIES_ENABLED`, `DBM_TIMESERIES_BACKEND` | `true` / `victoriametrics` | `memory` only for tests and development |
| `DBM_TIMESERIES_URL` | `http://timeseries:8428` | VictoriaMetrics (single node, or a gateway in front of a cluster) |
| `DBM_TIMESERIES_USERNAME`, `DBM_TIMESERIES_PASSWORD` | — | basic authentication (Compose: also the container's `httpAuth`) |
| `DBM_TIMESERIES_TIMEOUT_SECONDS` | 10 | request timeout |
| `DBM_TIMESERIES_RETENTION` | `90d` | Compose: VictoriaMetrics `-retentionPeriod`; the backend only displays it |
| `DBM_METRIC_BATCH_SIZE`, `DBM_METRIC_FLUSH_INTERVAL` | 2000 / 5 | batch writer: samples per request, seconds between writes |
| `DBM_METRIC_BUFFER_MAX`, `DBM_METRIC_WRITE_MAX_BACKOFF_SECONDS` | 200000 / 60 | in-memory buffer while the database is unreachable (oldest dropped first), longest retry backoff |
| `DBM_METRICS_PG_DUAL_WRITE` | `false` | also write the legacy PostgreSQL tables (migration aid) |

### SQL workspace

| Variable | Default | Description |
|---|---|---|
| `DBM_WORKSPACE_MAX_ROWS`, `DBM_WORKSPACE_DEFAULT_ROWS`, `DBM_WORKSPACE_TIMEOUT_SECONDS` | 5000 / 500 / 30 | SQL workspace limits |

### Alerting and notifications

| Variable | Default | Description |
|---|---|---|
| `DBM_ALERT_EVALUATION_SECONDS` | 60 | alert evaluation interval ([alerting](../architecture/alerting.md)) |
| `DBM_WEBHOOK_ALLOW_PRIVATE`, `DBM_WEBHOOK_TIMEOUT_SECONDS` | `false` / 10 | allow webhook (and SIEM webhook) targets on private/loopback addresses; request timeout |
| `DBM_SMTP_HOST`, `DBM_SMTP_PORT`, `DBM_SMTP_USER`, `DBM_SMTP_PASSWORD`, `DBM_SMTP_FROM` | — / 587 / — / — / — | default SMTP server for e-mail channels |
| `DBM_SMTP_SECURITY` | derived from `DBM_SMTP_STARTTLS` (Compose: `starttls`) | `starttls`, `tls` (implicit, port 465) or `none` |
| `DBM_SMTP_STARTTLS` | `true` | deprecated: `true` = `starttls`, `false` = `none` (used only while `DBM_SMTP_SECURITY` is empty) |
| `DBM_SMTP_CA_FILE`, `DBM_SMTP_TIMEOUT_SECONDS` | — / 15 | CA bundle for the SMTP certificate, timeout |
| `DBM_NOTIFY_MAX_ATTEMPTS`, `DBM_NOTIFY_RETRY_BACKOFF_SECONDS` | 5 / `60,300,900,3600` | delivery attempts and backoff steps |
| `DBM_REPORT_MAX_ATTACHMENT_MB` | 10 | largest file a [scheduled report](../user-guide/reports.md#scheduled-reports) e-mails |

### Backup and restore

| Variable | Default | Description |
|---|---|---|
| `DBM_BACKUP_ALLOWED_ROOTS` | `/var/lib/dbmanager/backups` | directories file-system storage targets must be in |
| `DBM_BACKUP_WORK_DIR` | system temp (Compose: `/var/lib/dbmanager/work`, fixed) | scratch space for archives and dumps |
| `DBM_BACKUP_CONCURRENCY`, `DBM_BACKUP_STALE_MINUTES` | 1 / 30 | backup jobs at a time per worker; heartbeat timeout |
| `DBM_BACKUP_IDENTITY_FILE` | — | age identity for verification and restore of recipient-encrypted backups |
| `DBM_BACKUP_PG_BIN_DIR`, `DBM_BACKUP_MYSQL_BIN_DIR` | PATH | client tool directories |
| `DBM_BACKUP_TOOL_TIMEOUT_SECONDS` | 21600 | longest external tool run |

See [Backup & Restore → Security](../backup-restore/security.md#settings).

### Product information and branding

| Variable | Default | Description |
|---|---|---|
| `DBM_DOCUMENTATION_URL`, `DBM_GITHUB_URL`, `DBM_ROADMAP_URL`, `DBM_LICENSE` | published docs / project repository / `ROADMAP.md` / Apache-2.0 | links and license on Platform → About and in the Help menu; `DBM_DOCUMENTATION_URL=off` hides the documentation link |
| `DBM_BUILD_VERSION`, `DBM_GIT_COMMIT`, `DBM_BUILD_DATE` | — | build metadata (Compose passes them as image build arguments) |
| `DBM_BRANDING_DIR` | — | optional licensed engine logos ([brand assets](../development/brand-assets.md#operator-supplied-licensed-logos)) |

### Docker Compose only

These are read by `docker-compose.yml` (from `.env`), not by the backend's `Settings`:

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `dbmanager` / `dbmanager` / — | application database container; also form `DBM_DATABASE_URL` |
| `DBM_SAMPLE_MONITOR_PASSWORD` | — | creates the `dbm_monitor` role in the application database at first start |
| `DBM_HTTP_BIND`, `DBM_HTTP_PORT` | `127.0.0.1` / `8080` | published address and port of the frontend |
| `DBM_OUTER_PROXY` | `127.0.0.1/32` | passed to the frontend container (see above) |
| `DBM_TIMESERIES_IMAGE_TAG` | `v1.122.0` | VictoriaMetrics image tag |
| `DBM_TIMESERIES_CPUS`, `DBM_TIMESERIES_MEMORY` | `2` / `2g` | resource limits of the time-series container |
| `DBM_TIMESERIES_MAX_UNIQUE_SERIES` | 1000000 | VictoriaMetrics `-search.maxUniqueTimeseries` |
| `DBM_MSODBC_OPTIONAL` | `false` | build the backend without Microsoft ODBC Driver 18 when its repository is unreachable (SQL Server then reports the driver as missing) |
| `DBM_BACKUP_HOST_DIR` | volume `backups` | host directory or share mounted at `/var/lib/dbmanager/backups` |

### Not read by DBManager

`DBM_APP_NAME` exists in `Settings` but is not used. The backend's `/metrics` endpoint has no
setting: it is always on and unauthenticated, served by the backend on port 8000 (and by a
stand-alone worker on `DBM_WORKER_PORT`), and the frontend nginx does not proxy it
([Diagnostics](../administration/diagnostics.md)).

## Kubernetes / OpenShift

Reference manifests in `deployment/kubernetes/` and `deployment/openshift/`:

* `dbmanager-api` Deployment (`DBM_PROCESS_ROLE=api`, N replicas, stateless — sessions are in PostgreSQL);
* `dbmanager-worker` Deployment (`python -m app.worker`, M replicas). One worker holds the scheduler
  advisory lock; all workers claim jobs with `FOR UPDATE SKIP LOCKED`, so scaling is linear;
* `dbmanager-migrate` Job (`app.cli migrate && app.cli bootstrap`) with `DBM_RUN_MIGRATIONS_ON_START=false`;
* frontend Deployment + Service + Ingress (or an edge Route on OpenShift);
* external PostgreSQL (managed service or an operator such as CloudNativePG / Crunchy);
* secrets from your secret manager (`dbmanager-secrets`).

Client address on Kubernetes: the reference frontend Deployment does not set `DBM_OUTER_PROXY`,
so the image default `127.0.0.1/32` applies and the audit log and per-IP limits see the ingress
controller's pod address. Set `DBM_OUTER_PROXY` on the frontend container to the ingress
controller's pod network (one CIDR) and keep `DBM_TRUSTED_PROXIES` (`10.0.0.0/8` in
`00-config.yaml`) limited to the cluster network the frontend pods use
([reverse proxy](reverse-proxy-tls.md#how-the-client-address-is-determined)).

`/metrics` is not proxied by the frontend: scrape the backend pods on port 8000 (and stand-alone
workers on `DBM_WORKER_PORT`, default 8001) from inside the cluster; it needs no credentials, so
keep it off the ingress ([Diagnostics](../administration/diagnostics.md)).

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
