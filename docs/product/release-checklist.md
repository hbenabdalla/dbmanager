# Release checklist

Run before tagging any `v0.x.y` / `v1.x.y` release. Commands are from the repository root.

| # | Item | How to verify | State (0.14.0) |
|---|---|---|---|
| 1 | Backend unit + API tests pass | `cd backend && pytest tests/unit tests/api` (API tests need `DBM_TEST_DATABASE_URL`, a scratch PostgreSQL) | Passing — 492 backend tests (unit, API, migration) against PostgreSQL 16 (2026-10-19) |
| 2 | Frontend tests pass | `cd frontend && npm ci && npx vitest run` | Passing — 123 tests in 17 files |
| 3 | Frontend production build | `cd frontend && npm run build` | Passing |
| 4 | Lint | `cd backend && ruff check app tests alembic && ruff format --check app tests`; `cd frontend && npx tsc -b --noEmit` | Passing |
| 5 | Real-engine integration tests | `scripts/integration-test.sh` (or `.ps1`); inspect `integration-results/real-engine-matrix.md` | 0.14.0 changes no adapter; PostgreSQL 16 adapter (12) and backup round trip re-run locally, passed. Full Docker matrix: run `scripts/integration-test` on the 0.14.0 commit before tagging (last full run: 0.13.0, 86 tests, 85 passed, 1 documented skip) |
| 6 | Docker images build | `docker compose build` | To run on the owner's Docker host (`docker compose build`); the release-preparation environment cannot pull base images. New backend dependency: ReportLab (pure Python wheel) |
| 7 | Docker Compose works | `docker compose up -d`; `curl -fsS http://localhost:8080/healthz`; sign in; register a server | **Correction:** `ci-docker.yml` failed from 0.11 to 0.13.0 in the restore drill (`.env` sourced as shell code); fixed in 0.14.0 — confirm on the CI run of the 0.14.0 push |
| 8 | Migrations tested | `cd backend && alembic upgrade head` on an empty database **and** on the previous release's schema | Passing — step-by-step 0001→0012, downgrade, head = models, upgrade with data from the first and the 0.13.0 schema |
| 9 | Documentation builds | `pip install -r docs/requirements.txt && mkdocs build --strict` | Passing (`mkdocs build --strict`) |
| 10 | No secrets committed | gitleaks with `.gitleaks.toml` over the history and the working tree; `.env`, `.env.test` ignored | Passing — gitleaks: no leaks in the working tree; history scan before tagging |
| 11 | Dependency review | `pip-audit`; `npm audit --omit=dev` | Passing 2026-10-19 — pip-audit: no known vulnerabilities (incl. reportlab); npm audit: 0 vulnerabilities |
| 12 | Security review | Review changes to authentication, RBAC, credential handling, admin actions; `SECURITY.md` current | Done — report RBAC (scope per caller, owner's current access for schedules, `users.manage` to run as someone else), CSV formula guard, licensed-logo endpoint (whitelist, nosniff, sandbox CSP), nosniff on static assets |
| 13 | Capability matrix updated | `docs/product/capability-matrix.md` reflects the release | Current (reports COMPLETE) |
| 14 | Roadmap updated | `ROADMAP.md`, `docs/product/roadmap.md`, `docs/product/current-milestone.md` | Current (0.14 DONE, 0.15 NEXT) |
| 15 | Changelog updated | `CHANGELOG.md` has an entry for the version | Done (0.14.0) and [release notes](../release-notes/0.14.0.md) |
| 16 | Version updated | One version across `backend/app/__init__.py`, `backend/pyproject.toml`, `frontend/package.json`; matches the tag | `0.14.0` everywhere (`tests/unit/test_version_consistency.py`) |
| 17 | License present | `LICENSE` exists and README states it | Passing — Apache-2.0 |

Release steps once all rows are green: update `CHANGELOG.md`, bump the version, open a pull
request, merge to `main`, tag `vX.Y.Z`, push the tag (Read the Docs builds the tagged version).
