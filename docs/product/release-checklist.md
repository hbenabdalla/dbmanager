# Release checklist

Run before tagging any `v0.x.y` / `v1.x.y` release. Commands are from the repository root.

| # | Item | How to verify | State (0.11.0-dev) |
|---|---|---|---|
| 1 | Backend unit + API tests pass | `cd backend && pytest tests/unit tests/api` (API tests need `DBM_TEST_DATABASE_URL`, a scratch PostgreSQL) | Passing (337 unit + API + migrations) |
| 2 | Frontend tests pass | `cd frontend && npm ci && npx vitest run` | Passing (61) |
| 3 | Frontend production build | `cd frontend && npm run build` | Passing |
| 4 | Lint | `cd backend && ruff check app tests && ruff format --check app tests`; `cd frontend && npx tsc -b --noEmit` | Passing (`ruff check .` clean, including `backend/alembic/`) |
| 5 | Real-engine integration tests | `scripts/integration-test.sh` (or `.ps1`); inspect `integration-results/real-engine-matrix.md` | Passing 2026-10-18 (75 passed, 1 documented skip) |
| 6 | Docker images build | `docker compose build` (the backend build fails on purpose if Microsoft ODBC Driver 18 cannot be installed, unless `DBM_MSODBC_OPTIONAL=true`) | Passing — backend runtime and frontend images build in `ci-docker.yml` on `main` |
| 7 | Docker Compose works | `docker compose up -d`; `curl -fsS http://localhost:8080/healthz`; sign in; register a server | First CI run failed before starting the stack (`scripts/init-env.sh` was not executable); fixed — to confirm on the next `ci-docker.yml` run |
| 8 | Migrations tested | `cd backend && alembic upgrade head` on an empty database **and** on the previous release's schema | Passing — `backend/tests/migrations/` (step-by-step upgrade, downgrade to base, re-upgrade, head = models); release-to-release test after the first tag |
| 9 | Documentation builds | `pip install -r docs/requirements.txt && mkdocs build --strict` | Passing — CI and Read the Docs (https://db-manager.readthedocs.io/en/latest/) |
| 10 | No secrets committed | gitleaks (`.github/workflows/security.yml`, config `.gitleaks.toml`) or `git grep` for password/secret/token/API-key assignments, check `git log -p` for `.env`; `.env`, `.env.test` ignored | Passing locally (gitleaks over the full history: no leaks). First CI run flagged the known test fixture because its commit-pinned ignore entry no longer matched after the history was re-parented; replaced by a path + value allowlist in `.gitleaks.toml` |
| 11 | Dependency review | `pip-audit -r <(pip freeze)` in the backend venv; `npm audit --omit=dev` in `frontend/` | Passing — `npm audit --omit=dev`: 0 vulnerabilities; `pip-audit`: no known vulnerabilities |
| 12 | Security review | Review changes to authentication, RBAC, credential handling, admin actions; `SECURITY.md` current | Per release |
| 13 | Capability matrix updated | `docs/product/capability-matrix.md` reflects the release | Current |
| 14 | Roadmap updated | `ROADMAP.md`, `docs/product/roadmap.md` statuses | Current |
| 15 | Changelog updated | `CHANGELOG.md` has an entry for the version | Done (`CHANGELOG.md`, Unreleased — 0.11.0) |
| 16 | Version updated | One version across `backend/app/__init__.py`, `backend/pyproject.toml`, `frontend/package.json`; matches the tag | Passing — `0.11.0-dev` (`0.11.0.dev0` in `pyproject.toml`) everywhere; set to `0.11.0` when tagging |
| 17 | License present | `LICENSE` exists and README states it | Passing — Apache-2.0 (`LICENSE`, README, package metadata, About page) |

Release steps once all rows are green: update `CHANGELOG.md`, bump the version, open a pull
request, merge to `main`, tag `vX.Y.Z`, push the tag (Read the Docs builds the tagged version).
