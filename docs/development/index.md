# Development

| Topic | Page |
|---|---|
| Unit, API and real-engine tests | [Testing](testing.md) |
| Adding an engine end to end | [Adding a new database engine](adding-a-database-engine.md) |
| New metrics and recommendation/security rules | [Adding metrics and rules](adding-metrics-and-rules.md) |
| Building this documentation | [Documentation](documentation.md) |
| Branches, versions, tags, documentation versions | [Release process](release-process.md) |

Contribution rules (code of conduct, commit style, pull requests) are in `CONTRIBUTING.md` at the
repository root.

## Repository structure

```text
backend/            FastAPI application (Python 3.12+)
  app/
    adapters/       one package per engine + base contract, drivers, TLS, admin catalogue
    advisors/       rule engine; performance/ and security/ rules per engine
    api/            REST routes (app/api/v1/*), dependencies, error handling
    core/           settings (DBM_*), security, crypto, logging
    db/ models/ repositories/ schemas/   persistence and DTOs
    domain/         SQL classifier, risk model
    jobs/           scheduler and worker runner
    services/       business logic (collection, metrics, advisor, alerting, onboarding …)
    simulation/     simulated fleet generator
    timeseries/     VictoriaMetrics client, metric catalog, migration
    cli.py worker.py main.py
  alembic/          schema migrations
  tests/            unit/, api/, integration/
frontend/           React + TypeScript + Vite SPA, nginx config
  src/features/     one folder per area (dashboard, fleet, server, queries, security, platform …)
  src/lib/ src/components/ src/api/ src/test/
deployment/         Kubernetes and OpenShift manifests, PostgreSQL init scripts
scripts/            init-env, dev-up, test-all, integration-test
docs/               this documentation (MkDocs)
docker-compose.yml  application stack · docker-compose.test.yml  real-engine test engines
```

## Development environment

=== "Docker Compose"

    ```bash
    ./scripts/init-env.sh
    docker compose up --build
    ```

    Rebuild the backend or frontend image after code changes (`docker compose up --build backend`).

=== "Local processes"

    Requirements: Python 3.12+, Node.js 24 LTS (as in CI and the image build; 22.12+ also works), a PostgreSQL database for the application.

    ```bash
    cd backend && pip install -e ".[dev]" && cd ..
    cd frontend && npm ci && cd ..
    export DBM_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dbmanager
    ./scripts/dev-up.sh
    ```

    `dev-up.sh` generates a throw-away `DBM_MASTER_KEY` if none is set, sets
    `DBM_ENV=development` and `DBM_COOKIE_SECURE=false`, starts `uvicorn --reload` on port 8000 and
    the Vite dev server, which proxies `/api` to `VITE_API_PROXY` (default `http://127.0.0.1:8000`).
    Without a time-series database, metric history is unavailable; start one with
    `docker compose up -d timeseries` and publish its port for local use.

## Code quality

| Part | Commands |
|---|---|
| Backend | `ruff check app tests`, `pytest` |
| Frontend | `npm run typecheck`, `npm test`, `npm run build` |
| Everything | `scripts/test-all.sh` |

CI runs the same checks (`.github/workflows/ci-backend.yml`, `ci-frontend.yml`, `ci-docker.yml`,
`ci-docs.yml`, `security.yml`), plus the opt-in real-engine workflow `integration-engines.yml`.
