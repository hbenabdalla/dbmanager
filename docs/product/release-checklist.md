# Release checklist

Run before tagging any `v0.x.y` / `v1.x.y` release. Commands are from the repository root.

| # | Item | How to verify | State (0.16.0) |
|---|---|---|---|
| 1 | Backend tests pass | `cd backend && pytest tests/unit tests/api tests/migrations tests/deploy` | Passing: 879 (693 unit incl. brand checks, 164 API, 8 migration, 14 chart), none skipped |
| 2 | Frontend tests pass | `cd frontend && npm ci && npx vitest run` | Passing: 183 |
| 3 | Frontend production build | `cd frontend && npm run build` | Passing |
| 4 | Lint | `ruff check . ../scripts`, `ruff format --check app tests`, `npx tsc -b --noEmit`, `actionlint` | Passing |
| 5 | Real-engine integration tests | `scripts/integration-test.ps1` with every engine | Not required: no adapter changed. Last full run 2026-10-21 on 0.15.0 code: 89 passed, 0 failed, 1 skipped |
| 6 | Docker images build | CI on the push | Base images unchanged since 0.15.3 |
| 7 | Docker Compose works | CI "Compose and restore drill" | CI on the push |
| 8 | Migrations tested | fresh install and upgrade | Passing (no new migration; schema `0014`) |
| 9 | Kubernetes | chart tests, kind job | Passing here (chart tests); kind job on the push |
| 10 | Documentation builds | `mkdocs build --strict` | Passing with the KBM theme |
| 11 | No secrets committed | gitleaks | Passing: no leaks; screenshots and videos show simulated data only |
| 12 | Dependency review | `npm audit`, `pip-audit` | npm audit 0 |
| 13 | Brand review | [brand guidelines](../branding/kbm-dbmanager-brand-guidelines.md), `tests/unit/test_brand_naming.py`, visual review | Done ([visual validation](test-validation.md#visual-validation)); KBM confirmation of inferred brand rules pending |
| 14 | Capability matrix, roadmap, traceability | `docs/product/*`, `ROADMAP.md` | Updated (0.16 DONE, 1.0 NEXT and not started) |
| 15 | Changelog and release notes | `CHANGELOG.md`, `docs/release-notes/0.16.0.md` | Done |
| 16 | Version updated | backend, frontend, chart, reference manifests; matches the tag | `0.16.0` everywhere |
| 17 | License present | `LICENSE` | Passing — Apache-2.0 |

Release steps once all rows are green (**the version bump comes before the tag**: `release.yml`
refuses a tag that differs from the product and chart versions, as it did for `v0.15.2`): update
`CHANGELOG.md`, bump the version, open a pull
request, merge to `main`, tag `vX.Y.Z`, push the tag. From 0.15.0 the tag runs `release.yml`:
signed images and chart on GHCR and the GitHub release with the notes and SBOMs; Read the Docs
builds the tagged version.
