# Release checklist

Run before tagging any `v0.x.y` / `v1.x.y` release. Commands are from the repository root.

| # | Item | How to verify | State (0.13.0) |
|---|---|---|---|
| 1 | Backend unit + API tests pass | `cd backend && pytest tests/unit tests/api` (API tests need `DBM_TEST_DATABASE_URL`, a scratch PostgreSQL) | Passing — 476 backend tests: 471 unit + API, 5 migration (2026-10-19) |
| 2 | Frontend tests pass | `cd frontend && npm ci && npx vitest run` | Passing — 117 tests in 15 files |
| 3 | Frontend production build | `cd frontend && npm run build` | Passing |
| 4 | Lint | `cd backend && ruff check app tests alembic && ruff format --check app tests`; `cd frontend && npx tsc -b --noEmit` | Passing |
| 5 | Real-engine integration tests | `scripts/integration-test.sh` (or `.ps1`); inspect `integration-results/real-engine-matrix.md` | Docker run 2026-10-19 20:31 UTC: 86 tests, 84 passed, 1 failed (MySQL backup, error 1419 — fixed), 1 documented skip (Oracle TCPS). **Re-run required before tagging** for the MySQL fix, the Db2 SYSMAINT backup operator and the 0.13.0 plan-identity / size-series checks ([report](real-engine-validation.md)) |
| 6 | Docker images build | `docker compose build` | Built on the owner's Docker host for the integration run (`database-manager-backend:it` with ODBC Driver 18.7, PostgreSQL 17 and MariaDB clients); no Docker in the release-preparation environment |
| 7 | Docker Compose works | `docker compose up -d`; `curl -fsS http://localhost:8080/healthz`; sign in; register a server | Confirmed by `ci-docker.yml` on push (Compose health + `scripts/restore-drill.sh`) |
| 8 | Migrations tested | `cd backend && alembic upgrade head` on an empty database **and** on the previous release's schema | Passing — step-by-step 0001→0011, downgrade, head = models, upgrade with data from the first and the 0.12.0 schema (users, role assignments, servers, credentials, alert rules, channels, findings, backup targets and policies, settings) |
| 9 | Documentation builds | `pip install -r docs/requirements.txt && mkdocs build --strict` | Passing |
| 10 | No secrets committed | gitleaks with `.gitleaks.toml` over the history and the working tree; `.env`, `.env.test` ignored | Passing — gitleaks 8.24: no leaks (41 commits, working tree) |
| 11 | Dependency review | `pip-audit`; `npm audit --omit=dev` | Passing 2026-10-19 — no known vulnerabilities, 0 npm vulnerabilities; no new dependencies |
| 12 | Security review | Review changes to authentication, RBAC, credential handling, admin actions; `SECURITY.md` current | Done for the new endpoints (RBAC per endpoint, masked statement text, audited plan save/delete, no stored statements unless saved explicitly) |
| 13 | Capability matrix updated | `docs/product/capability-matrix.md` reflects the release | Current |
| 14 | Roadmap updated | `ROADMAP.md`, `docs/product/roadmap.md`, `docs/product/current-milestone.md` | Current (0.13 DONE, 0.14 NEXT) |
| 15 | Changelog updated | `CHANGELOG.md` has an entry for the version | Done (0.13.0) and [release notes](../release-notes/0.13.0.md) |
| 16 | Version updated | One version across `backend/app/__init__.py`, `backend/pyproject.toml`, `frontend/package.json`; matches the tag | `0.13.0` everywhere (`tests/unit/test_version_consistency.py`) |
| 17 | License present | `LICENSE` exists and README states it | Passing — Apache-2.0 |

Release steps once all rows are green: update `CHANGELOG.md`, bump the version, open a pull
request, merge to `main`, tag `vX.Y.Z`, push the tag (Read the Docs builds the tagged version).
