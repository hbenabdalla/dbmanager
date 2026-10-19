# Release checklist

Run before tagging any `v0.x.y` / `v1.x.y` release. Commands are from the repository root.

| # | Item | How to verify | State (0.12.0) |
|---|---|---|---|
| 1 | Backend unit + API tests pass | `cd backend && pytest tests/unit tests/api` (API tests need `DBM_TEST_DATABASE_URL`, a scratch PostgreSQL) | Passing — 444 unit + API + migration tests (2026-10-19) |
| 2 | Frontend tests pass | `cd frontend && npm ci && npx vitest run` | Passing — 109 tests in 14 files |
| 3 | Frontend production build | `cd frontend && npm run build` | Passing |
| 4 | Lint | `cd backend && ruff check app tests && ruff format --check app tests`; `cd frontend && npx tsc -b --noEmit` | Passing (`ruff check .` clean, including `backend/alembic/`) |
| 5 | Real-engine integration tests | `scripts/integration-test.sh` (or `.ps1`); inspect `integration-results/real-engine-matrix.md` | Engine adapters passing 2026-10-18 (75 passed, 1 documented skip). New in 0.12: backup round trips run against PostgreSQL 16 and MariaDB 10.11, identity against OpenLDAP 2.6; SQL Server / Oracle / Db2 / MySQL backup and Keycloak / Vault targets still to run (`scripts/integration-test` on a Docker host) |
| 6 | Docker images build | `docker compose build` (the backend build fails on purpose if Microsoft ODBC Driver 18 cannot be installed, unless `DBM_MSODBC_OPTIONAL=true`) | To confirm on GitHub: the Dockerfile now also installs `postgresql-client-17` and `mariadb-client` (not buildable in this environment: no Docker) |
| 7 | Docker Compose works | `docker compose up -d`; `curl -fsS http://localhost:8080/healthz`; sign in; register a server | To confirm on the next `ci-docker.yml` run (Compose health + `scripts/restore-drill.sh`) |
| 8 | Migrations tested | `cd backend && alembic upgrade head` on an empty database **and** on the previous release's schema | Passing — `backend/tests/migrations/` (step-by-step upgrade 0001→0010, downgrade, head = models, upgrade with data from the first and previous schema) |
| 9 | Documentation builds | `pip install -r docs/requirements.txt && mkdocs build --strict` | Passing — CI and Read the Docs (https://db-manager.readthedocs.io/en/latest/) |
| 10 | No secrets committed | gitleaks (`.github/workflows/security.yml`, config `.gitleaks.toml`) or `git grep` for password/secret/token/API-key assignments, check `git log -p` for `.env`; `.env`, `.env.test` ignored | Passing — gitleaks 8.24 over the full history and the working tree: no leaks (narrow path + value allowlist in `.gitleaks.toml`) |
| 11 | Dependency review | `pip-audit -r <(pip freeze)` in the backend venv; `npm audit --omit=dev` in `frontend/` | Passing 2026-10-19 — `npm audit --omit=dev`: 0 vulnerabilities; `pip-audit`: no known vulnerabilities; new dependencies `pyrage` (age), `boto3`, dev `moto[s3,server]` |
| 12 | Security review | Review changes to authentication, RBAC, credential handling, admin actions; `SECURITY.md` current | Per release |
| 13 | Capability matrix updated | `docs/product/capability-matrix.md` reflects the release | Current (with the [roadmap audit](roadmap-audit.md)) |
| 14 | Roadmap updated | `ROADMAP.md`, `docs/product/roadmap.md` statuses | Current |
| 15 | Changelog updated | `CHANGELOG.md` has an entry for the version | Done (`CHANGELOG.md`, 0.12.0) and [release notes](../release-notes/0.12.0.md) |
| 16 | Version updated | One version across `backend/app/__init__.py`, `backend/pyproject.toml`, `frontend/package.json`; matches the tag | Passing — `0.12.0` everywhere (`tests/unit/test_version_consistency.py`) |
| 17 | License present | `LICENSE` exists and README states it | Passing — Apache-2.0 (`LICENSE`, README, package metadata, About page) |

Release steps once all rows are green: update `CHANGELOG.md`, bump the version, open a pull
request, merge to `main`, tag `vX.Y.Z`, push the tag (Read the Docs builds the tagged version).
