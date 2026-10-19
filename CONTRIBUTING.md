# Contributing to DBManager

Thanks for helping. This guide gets you from a fresh clone to a reviewed pull request. The full
developer documentation (architecture, adapter tutorial, metric pipeline) lives in
[`docs/development/`](docs/development/index.md) and is published with the rest of the
documentation on Read the Docs.

## Repository structure

```
backend/            FastAPI application (app/), Alembic migrations, tests (unit, api, integration)
  app/adapters/     one package per database engine + the capability model (base.py)
  app/advisors/     performance and security rules (common + per engine)
  app/timeseries/   metric catalog, normalizer, VictoriaMetrics / memory repositories
  app/data/         real-engine validation results shipped with the build
frontend/           React + TypeScript + Vite single-page app (src/features/* per area)
deployment/         Kubernetes / OpenShift manifests, PostgreSQL init scripts
docs/               MkDocs documentation (Read the Docs)
scripts/            environment bootstrap, test runners, real-engine integration tests
docker-compose.yml  the application (frontend, backend, PostgreSQL, VictoriaMetrics)
docker-compose.test.yml  real database engines for integration tests only
```

## Development environment

Requirements: Python 3.12, Node.js 22, Docker (Compose v2), PostgreSQL 16 for API tests.

### Backend

```bash
cd backend
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export DBM_DATABASE_URL=postgresql+asyncpg://dbm:dbm@localhost:5432/dbmanager
export DBM_MASTER_KEY=$(python -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')
export DBM_ENV=development DBM_COOKIE_SECURE=false DBM_BOOTSTRAP_ADMIN_PASSWORD='Choose-A-Dev-Password-1'
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm ci
npm run dev          # http://localhost:5173, proxies /api to http://127.0.0.1:8000
```

### Docker

```bash
./scripts/init-env.sh            # or scripts\init-env.ps1 on Windows: creates .env with generated secrets
docker compose up --build
```

`.env`, `.env.test` and `integration-results/` are git-ignored; never commit them.

## Testing

| Suite | Command | Needs |
|---|---|---|
| Backend lint | `cd backend && ruff check . && ruff format --check app tests` | — |
| Backend unit | `cd backend && pytest tests/unit` | — |
| Backend API | `cd backend && pytest tests/api` | scratch PostgreSQL in `DBM_TEST_DATABASE_URL` (its schema is dropped) |
| Migrations, backup/restore | `cd backend && pytest tests/migrations` | the same scratch PostgreSQL; `pg_dump`/`pg_restore` on the `PATH` |
| Restore drill (Compose) | `scripts/restore-drill.sh --i-understand-this-wipes-the-stack` | a **throw-away** Compose stack started with `docker compose up -d` (its volumes are deleted) |
| Secret scan | `scripts/secret-scan.sh` | gitleaks or Docker |
| Frontend | `cd frontend && npm run typecheck && npm test && npm run build` | — |
| Documentation | `pip install -r docs/requirements.txt && mkdocs build --strict` | — |
| Real engines | `scripts/integration-test.sh` (or `.ps1`) | Docker; pulls vendor images under their licenses |

Every pull request must keep unit, API and frontend tests green and must not add skipped tests
without an explicit reason. Changes to an adapter's SQL should be validated with the real-engine
suite for that engine; paste the resulting `integration-results/real-engine-matrix.md` in the PR.

## Adding a database adapter

Follow the tutorial in [`docs/development/adding-a-database-engine.md`](docs/development/adding-a-database-engine.md).
In short: subclass `BaseDatabaseAdapter` (or `SyncDBAPIAdapter` for blocking drivers), declare
every `Capability` honestly (never return made-up data), put vendor SQL in `sql.py`, register the
class in `app/adapters/registry.py`, add a driver entry in `app/adapters/drivers.py`, write unit
tests with `tests/unit/fakedb.py` and an integration suite with capability markers, and document
the least-privilege accounts.

## Adding metrics

Add the metric to `app/timeseries/catalog.py` (name, unit, category, description), report it from
the adapters as a gauge or a cumulative counter in the catalog's units, and let the normalizer
derive rates. Engine-only metrics use an engine prefix (`oracle.…`, `db2.…`).

## Adding recommendation and security rules

Rules live in `app/advisors/performance/<engine>.py` and `app/advisors/security/<engine>.py`
(`common.py` for every engine). Scope each rule with `engines=(...)`, base it only on evidence the
adapter actually returns (`get_tuning_context` / `get_security_context`), make it not fire when
evidence is `None`, and add a unit test in `tests/unit/test_rules.py`.

## Documentation

Docs are Markdown under `docs/`, built with MkDocs Material. Preview with `mkdocs serve`. Keep
pages task-oriented, accurate to the code, and link with relative paths; `mkdocs build --strict`
must pass. Use real screenshots only (`docs/assets/screenshots/`).

## Pull requests

1. Branch from `main` (`feat/…`, `fix/…`, `docs/…`).
2. Keep a PR focused on one change; include tests and documentation updates.
3. Fill in the pull request template (tests, docs, security, migrations, API and database
   compatibility, screenshots for UI changes).
4. CI must pass. A maintainer reviews and squash-merges.

### Branch protection

`main` is protected. Repository settings → **Branches** → rule for `main`:

* **Require a pull request before merging**, with at least one approval; dismiss stale approvals.
* **Require status checks to pass**, branch up to date, with these required checks:
  `Backend lint`, `Backend tests`, `Frontend build`, `Documentation build` and
  `Secret scan`. These workflows run on every pull request (no path filters), so a
  required check never stays pending.
* **Require linear history**; do not allow force pushes or deletions.

`Docker images` (image build, Compose start-up and the restore drill) runs on pull requests that
touch the backend, frontend, scripts or `docker-compose.yml`, and on every push to `main`; it is
not a required check because it is path-filtered. The real-engine suite
(`Real-engine integration`) is started manually.

Database schema changes need an Alembic migration (`alembic revision -m "…"`) that upgrades an
existing installation without data loss.

## Code style

* Python: ruff (lint + format, line length 120), type hints, async I/O in the API; blocking
  drivers only through `SyncDBAPIAdapter`.
* TypeScript: strict mode, functional React components, existing UI primitives in
  `src/components/ui`, design tokens from `src/index.css`.
* Never log, return or audit passwords or other secrets.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
`test:`, `ci:`, `refactor:`, `chore:` with an optional scope (`feat(oracle): …`). Write the
summary in the imperative and explain the *why* in the body when it is not obvious.

## Security issues

Do not report vulnerabilities in public issues — see [SECURITY.md](SECURITY.md).

## License of contributions

DBManager is licensed under the [Apache License 2.0](LICENSE). By submitting a pull request you
agree that your contribution is licensed under the same terms (Apache-2.0, section 5). Do not
submit code you are not entitled to license this way, such as code copied from projects under
incompatible licenses or vendor material that may not be redistributed.
