# Release checklist

Run before tagging any `v0.x.y` / `v1.x.y` release. Commands are from the repository root.

| # | Item | How to verify | State (0.15.0) |
|---|---|---|---|
| 1 | Backend unit + API tests pass | `cd backend && pytest tests/unit tests/api tests/migrations tests/deploy` (API tests need `DBM_TEST_DATABASE_URL`; control-plane TLS tests `DBM_TEST_DATABASE_TLS_CA`) | Passing: 869 (684 unit, 164 API, 8 migration, 13 chart), none skipped, against PostgreSQL 16 with TLS (2026-10-20) |
| 2 | Frontend tests pass | `cd frontend && npm ci && npx vitest run` | Passing: 183 tests in 23 files (Vitest 5) |
| 3 | Frontend production build | `cd frontend && npm run build` | Passing on Node 22 and Node 24 (Vite 8) |
| 4 | Lint | `cd backend && ruff check . ../scripts && ruff format --check app tests`; `cd frontend && npx tsc -b --noEmit`; `actionlint` | Passing |
| 5 | Real-engine integration tests | `scripts/integration-test.sh` (or `.ps1`) with every engine; inspect `integration-results/real-engine-matrix.md` | **To run on the Docker host before the tag** (MySQL TLS test fix and the new TLS certificates). Local run 2026-10-20: 35 passed (PostgreSQL 16, MariaDB 10.11 with TLS, VictoriaMetrics, OpenLDAP) |
| 6 | Docker images build | `docker compose build` | CI (`ci-docker.yml`, `ci-kubernetes.yml`) on the push; the preparation environment has no container runtime. Base images changed: `node:24-alpine`, `nginx-unprivileged:1.31-alpine` |
| 7 | Docker Compose works | `docker compose up -d`; sign in; register a server; restore drill | CI "Compose and restore drill" on the push (the drill fix of this release). The stack ran here without Docker: API + a second worker, PostgreSQL 16, VictoriaMetrics, Vite 8; 41 pages without page errors |
| 8 | Migrations tested | fresh install and upgrade from the previous release | Passing: step by step 0001 → 0014, downgrade, head = models, upgrades with data from the first schema and from 0.12.0 (`0010`), 0.13.0 (`0011`), 0.14.0 (`0013`); three replicas starting together |
| 9 | Kubernetes | `helm lint`, kubeconform, `scripts/k8s-policy-check.py`, `pytest tests/deploy`; kind install in CI | Passing here (3 value sets, 53 objects, 0 violations; reference manifests 12 objects); kind install and failover on the push |
| 10 | Documentation builds | `pip install -r docs/requirements.txt && mkdocs build --strict` | Passing (mkdocs-material 9.7.7) |
| 11 | No secrets committed | gitleaks with `.gitleaks.toml` over the history and the working tree | Passing: no leaks (the CI-only chart values are allowlisted by path and exact value); screenshots contain no credentials |
| 12 | Dependency review | `pip-audit`; `npm audit` | Passing 2026-10-20: npm audit 0; pip-audit found `certifi` 2026.7.22 (PYSEC-2026-4320) in the local environment — the images install the fixed 2026.10.19, which was audited clean |
| 13 | Security review | changes to authentication, RBAC, credential handling, admin actions | Done: About no longer exposes host names; `/platform/instances` needs `collectors.view`; chart: restricted Pod Security, no service-account tokens, NetworkPolicies, required secrets |
| 14 | Capability matrix, roadmap, traceability, audit | `docs/product/*`, `ROADMAP.md` | Updated (0.15 DONE, 1.0 NEXT and not started) |
| 15 | Changelog and release notes | `CHANGELOG.md`, `docs/release-notes/X.Y.Z.md` (required by `release.yml`) | Done (0.15.0) |
| 16 | Version updated | one version in backend, frontend, chart and reference manifests; matches the tag | `0.15.0` everywhere (`tests/unit/test_version_consistency.py`, `tests/deploy`, `release.yml` check) |
| 17 | License present | `LICENSE` exists and README states it | Passing — Apache-2.0 |

Release steps once all rows are green (**the version bump comes before the tag**: `release.yml`
refuses a tag that differs from the product and chart versions, as it did for `v0.15.2`): update
`CHANGELOG.md`, bump the version, open a pull
request, merge to `main`, tag `vX.Y.Z`, push the tag. From 0.15.0 the tag runs `release.yml`:
signed images and chart on GHCR and the GitHub release with the notes and SBOMs; Read the Docs
builds the tagged version.
