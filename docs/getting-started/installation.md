# Installation

DB Manager ships as four containers started with Docker Compose: frontend (nginx), backend
(FastAPI), PostgreSQL (control plane) and VictoriaMetrics (metric history).

## 1. Get the source

```bash
git clone https://github.com/hbenabdalla/dbmanager.git
cd dbmanager
```

## 2. Create the configuration

`init-env` copies `.env.example` to `.env` and replaces every `CHANGE_ME` value with a generated secret
(`POSTGRES_PASSWORD`, `DBM_MASTER_KEY`, `DBM_TIMESERIES_PASSWORD`, `DBM_BOOTSTRAP_ADMIN_PASSWORD`,
`DBM_SAMPLE_MONITOR_PASSWORD`):

=== "Linux / macOS"

    ```bash
    ./scripts/init-env.sh
    ```

=== "Windows (PowerShell)"

    ```powershell
    powershell -ExecutionPolicy Bypass -File scripts\init-env.ps1
    ```

Never commit `.env`. Store `DBM_MASTER_KEY` in a password vault: without it, stored database
credentials cannot be decrypted (see [Backup and restore](../operations/backup-restore.md)).

## 3. Build and start

```bash
docker compose up -d --build
docker compose ps        # all four containers become "healthy"
```

Start-up is ordered by health checks: PostgreSQL and VictoriaMetrics first, then the backend
(which applies database migrations and creates the first administrator), then the frontend.

## 4. Open the application

Browse to <http://localhost:8080> (the frontend binds to `127.0.0.1:8080` by default:
`DBM_HTTP_BIND`, `DBM_HTTP_PORT`). Continue with [First login](bootstrap.md).

!!! note "SQL Server driver"
    The backend image installs Microsoft ODBC Driver 18 during the build and the build **fails** if
    it cannot (for example without access to `packages.microsoft.com`). To build without SQL Server
    support set `DBM_MSODBC_OPTIONAL=true` in `.env`; SQL Server then shows *driver MISSING*.

Production deployments (TLS, reverse proxy, Kubernetes): see [Deployment](../deployment/index.md).
