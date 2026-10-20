# Release checklist

Run before tagging any `v0.x.y` / `v1.x.y` release. Commands are from the repository root.

| # | Item | How to verify | State (0.14.0) |
|---|---|---|---|
| 1 | Backend unit + API tests pass | `cd backend && pytest tests/unit tests/api` (API tests need `DBM_TEST_DATABASE_URL`, a scratch PostgreSQL) | Passing: 839 backend tests (unit, API, migration) against PostgreSQL 16 (2026-10-20) |
| 2 | Frontend tests pass | `cd frontend && npm ci && npx vitest run` | Passing: 180 tests in 22 files |
| 3 | Frontend production build | `cd frontend && npm run build` | Passing |
| 4 | Lint | `cd backend && ruff check app tests alembic && ruff format --check app tests`; `cd frontend && npx tsc -b --noEmit` | Passing |
| 5 | Real-engine integration tests | `scripts/integration-test.sh` (or `.ps1`); inspect `integration-results/real-engine-matrix.md` | **To run before the tag.** The reconciliation changed adapters. Local runs on 2026-10-20 passed: PostgreSQL 16 (12 + backup), MariaDB 10.11 (14 incl. TLS + backup), VictoriaMetrics (5). MySQL, SQL Server, Oracle and Db2 LUW need the Docker matrix: `scripts/integration-test` on the release commit |
| 6 | Docker images build | `docker compose build` | To run on the owner's Docker host; the release-preparation environment cannot pull base images. Backend CMD changed to `--no-proxy-headers` |
| 7 | Docker Compose works | `docker compose up -d`; `curl -fsS http://localhost:8080/healthz`; sign in; register a server | The "Compose and restore drill" CI job runs on every pull request and is required. The full stack (API + worker, PostgreSQL 16, VictoriaMetrics, frontend) ran here without Docker on a fresh database, with real PostgreSQL and MariaDB servers registered. Confirm on the CI run of the push |
| 8 | Migrations tested | `cd backend && alembic upgrade head` on an empty database **and** on the previous release's schema | Passing: step by step 0001 → 0013, downgrade, head = models, upgrade with data from the first schema and from the published 0.12.0 (`0010`) and 0.13.0 (`0011`) schemas; fresh install at `0013` on the screenshot stack |
| 9 | Documentation builds | `pip install -r docs/requirements.txt && mkdocs build --strict` | Passing (`mkdocs build --strict`) |
| 10 | No secrets committed | gitleaks with `.gitleaks.toml` over the history and the working tree; `.env`, `.env.test` ignored | Passing: `gitleaks dir` and `gitleaks git` found no leaks (2026-10-20); screenshots contain no credentials |
| 11 | Dependency review | `pip-audit`; `npm audit --omit=dev` | Passing 2026-10-20: pip-audit found no known vulnerabilities; npm audit found 0 |
| 12 | Security review | Review changes to authentication, RBAC, credential handling, admin actions; `SECURITY.md` current | Done: the reconciliation fixed 2 P0 and the P1 security findings ([roadmap audit](roadmap-audit.md)) |
| 13 | Capability matrix updated | `docs/product/capability-matrix.md` reflects the release | Reconciled |
| 14 | Roadmap updated | `ROADMAP.md`, `docs/product/roadmap.md`, `docs/product/current-milestone.md` | Reconciled (R1 – R7 DONE, 0.15 NEXT and not started) |
| 15 | Changelog updated | `CHANGELOG.md` has an entry for the version | Done (0.14.0) and [release notes](../release-notes/0.14.0.md) |
| 16 | Version updated | One version across `backend/app/__init__.py`, `backend/pyproject.toml`, `frontend/package.json`; matches the tag | `0.14.0` everywhere (`tests/unit/test_version_consistency.py`) |
| 17 | License present | `LICENSE` exists and README states it | Passing — Apache-2.0 |

Release steps once all rows are green: update `CHANGELOG.md`, bump the version, open a pull
request, merge to `main`, tag `vX.Y.Z`, push the tag (Read the Docs builds the tagged version).
