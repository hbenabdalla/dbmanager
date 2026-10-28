# Test validation

The newest release section comes first; the 0.15 clean validation follows it.

## Release 0.16.0: KBM rebrand

Validation of the KBM corporate rebrand on the 0.16.0 code (cloud workspace, 2026-10-28).

| Area | Total | Passed | Failed | Skipped | External | Status |
|---|---|---|---|---|---|---|
| Backend unit (incl. 5 brand-consistency tests) | 693 | 693 | 0 | 0 | — | PASS |
| Backend API (control-plane TLS `verify-full`) | 164 | 164 | 0 | 0 | — | PASS |
| Migrations | 8 | 8 | 0 | 0 | — | PASS |
| Helm chart tests | 14 | 14 | 0 | 0 | — | PASS |
| Frontend tests | 183 | 183 | 0 | 0 | — | PASS |
| Frontend type check and production build | — | ✓ | — | — | — | PASS |
| Documentation (`mkdocs build --strict`, KBM theme) | — | ✓ | — | — | — | PASS |
| gitleaks (working tree), npm audit | — | clean | — | — | — | PASS |
| UI pages (screenshot run, page errors and error states) | 43 | 43 | 0 | 0 | — | PASS |
| Promotional videos (frames, audio, subtitles, claims) | 3 | 3 | 0 | 0 | — | PASS |
| Real engines | — | — | — | — | not re-run: no adapter changed (last full Docker run 2026-10-21: 89 passed, 0 failed, 1 skipped) | — |

Findings during the rebrand, all fixed before release:

| # | Finding | Classification | Fix |
|---|---|---|---|
| 15 | Tests expected the old name in e-mail subjects, the MFA issuer and Teams cards | STALE TEST (intended rename) | expectations updated to *KBM DBManager*; the `/version` identifier test stays unchanged on purpose |
| 16 | The first sidebar lockup overflowed the 232 px rail with the wider Plus Jakarta Sans | PRODUCT BUG (found in visual review) | lockup proportions fixed to fit its width |
| 17 | The automated rename had changed historical statements (0.14 branding) to the new name | DOCUMENTATION | history phrased as *the former DBManager logo/branding*; the brand check allows exactly that phrase |
| 18 | Charts on several pages showed *No samples* in the first screenshot run | ENVIRONMENT (simulated telemetry 8 days old) | simulated fleet regenerated before the final capture |

### Visual validation

| View | Light | Dark | Viewports | Result |
|---|---|---|---|---|
| Sign-in (KBM cover) | ✓ | ✓ | 1440 × 900, 1920 × 1080 | PASS |
| Dashboard | ✓ | ✓ | 1440 × 900, 1920 × 1080 | PASS |
| About (KBM banner) | ✓ | — | 1440 × 900, 1920 × 1080 | PASS |
| All 43 documented pages and dialogs | ✓ | dashboard | 1440 × 900 | PASS: no page errors, no error states, status colours unchanged, engine marks unchanged |
| PDF report (security findings) | ✓ | — | A4 landscape | PASS: KBM header, deep-blue table header, footer mark |
| Documentation site (home, a table page) | ✓ | ✓ | 1440 × 900 | PASS: KBM header, navy dark scheme, tables and callouts |

Not covered: a tablet layout review (the application targets desktop and laptop widths; the 1024 px
layout of 0.14 was not changed) and an automated accessibility scan (planned in 1.0.3). Contrast of
the new tokens was computed: body text 12.3 : 1, secondary text 5.4 : 1, KBM blue on white 8.8 : 1,
rail text 6.9 : 1 on KBM blue, dark-theme accent 6.4 : 1.

## Release 0.15: clean validation

A clean validation of the whole repository, run before milestone 0.15 work started and again
after it. Earlier reports were not reused: every number on this page comes from a run made for
this release, and the environment each one ran in is named. Real-engine results per capability
are in the [real-engine validation report](real-engine-validation.md).

## Baseline

| Item | Value at the start of the validation |
|---|---|
| Branch / commit | `main` at `823f3b8` (tag `v0.14.0`, published 2026-10-20) |
| Versions | backend `0.14.0`, frontend `0.14.0`, schema `0013` |
| Released tags | `v0.12.0` (`22708f1`), `v0.13.0` (`620794c`), `v0.14.0` (`823f3b8`) |
| CI | `ci-backend`, `ci-frontend`, `ci-docs`, `ci-docker` (Compose start and restore drill), `security` (gitleaks, pip-audit, npm audit, Trivy), `integration-engines` (on demand) |
| Docker | `backend/Dockerfile` (runtime and `it` targets), `frontend/Dockerfile`, `docker-compose.yml`, `docker-compose.test.yml` |

## Environments

| Name | What ran there |
|---|---|
| **Cloud workspace** | Linux, Python 3.13, Node 22.22 (and Node 24.22 for the build check), PostgreSQL 16.15, MariaDB 10.11.14 (TLS with a test CA), VictoriaMetrics 1.122.0, OpenLDAP 2.6.10 (StartTLS), Helm 3.16, kubeconform, actionlint 1.7.7, gitleaks. No container runtime and no access to container registries |
| **Owner's Docker host** | Docker Desktop on Windows (WSL 2), `scripts/integration-test.ps1` with every vendor image (run of 2026-10-20 07:26 UTC on the 0.14.0 code) |
| **GitHub Actions** | the CI workflows on push of `main` |

## Inventory

| Area | Location | Files | Tests | Needs |
|---|---|---|---|---|
| Backend unit | `backend/tests/unit` | 36 | 684 | Python only |
| Backend API | `backend/tests/api` | 30 | 164 | PostgreSQL (`DBM_TEST_DATABASE_URL`); `DBM_TEST_DATABASE_TLS_CA` for the 2 control-plane TLS tests |
| Migrations | `backend/tests/migrations` | 3 | 8 | PostgreSQL |
| Deployment (Helm) | `backend/tests/deploy` | 1 | 13 | `helm` binary |
| 14 | The owner's Docker run started VictoriaMetrics but deselected its 5 tests | PRODUCT BUG (test tooling) | the runner image runs `pytest -m real_database`; the VictoriaMetrics suite is marked `timeseries` (it is not a managed database) | runner selects `real_database or timeseries`; the suite carries the `timeseries` marker | `test_ops_scripts.py::test_the_docker_runner_selects_every_integration_suite` (the runner's marker expression collects every integration test) |
| Real engines | `backend/tests/integration` | 9 | 95 | the engine named by each `DBM_IT_*_HOST` |
| Frontend | `frontend/src/test` | 23 | 183 | Node |
| Type check, build | `tsc -b`, `vite build` | — | — | Node |
| Kubernetes manifests | `helm lint`, kubeconform (1.29, 1.31), `scripts/k8s-policy-check.py` | — | 3 value sets + 5 reference manifests | `helm`, kubeconform |
| Kubernetes install | `ci-kubernetes.yml` kind job: install, `helm test`, leader failover | — | — | GitHub Actions |
| Compose stack | `ci-docker.yml`: build, start, restore drill | — | — | Docker |
| Documentation | `mkdocs build --strict` | — | — | Python |
| Workflows | actionlint | 8 workflows | — | — |
| Secrets | gitleaks (directory and history) | — | — | — |
| UI pages | screenshot run of 41 pages against the running application | — | 41 | API + frontend |

The frontend has no lint step (no ESLint configuration in the repository); `tsc -b` in strict mode
is the enforced static check.

## Results

### Before (0.14.0 code, start of this validation)

| Area | Total | Passed | Failed | Skipped | External | Status |
|---|---|---|---|---|---|---|
| Backend unit + API + migrations | 839 | 839 | 0 | 0 | — | PASS |
| Frontend tests | 180 | 180 | 0 | 0 | — | PASS |
| Frontend type check and build | — | ✓ | — | — | — | PASS |
| Real engines, owner's Docker run | 90 | 87 | 1 | 2 | Db2 for z/OS | FAIL (MySQL TLS) |
| Real engines, cloud workspace (PG, MariaDB, VM, LDAP) | 95 | not recorded | ≥ 1 | not recorded | other engines | FAIL (LDAP StartTLS, #4; the suites were run one by one while fixing, no complete run was kept) |
| Compose restore drill (GitHub Actions) | 1 | 0 | 1 | 0 | — | FAIL |
| Documentation (`--strict`) | — | ✓ | — | — | — | PASS |
| Upgrade from the latest release | — | not tested | — | — | — | GAP (0.14.0 missing) |

### After (0.15.0 release commit)

| Area | Total | Passed | Failed | Skipped | External | Status |
|---|---|---|---|---|---|---|
| Backend unit | 684 | 684 | 0 | 0 | — | PASS |
| Backend API (with control-plane TLS `verify-full`) | 164 | 164 | 0 | 0 | — | PASS |
| Migrations (fresh install, step-by-step, upgrades from first, 0.12.0, 0.13.0, 0.14.0, concurrent start, dump/restore) | 8 | 8 | 0 | 0 | — | PASS |
| Helm chart tests | 13 | 13 | 0 | 0 | — | PASS |
| Frontend tests | 183 | 183 | 0 | 0 | — | PASS |
| Frontend type check and build (Node 22 and Node 24) | — | ✓ | — | — | — | PASS |
| Real engines, cloud workspace | 95 | 35 | 0 | 60 | MySQL, SQL Server, Oracle, Db2 LUW, Keycloak, Vault need the Docker run; Db2 for z/OS | PASS for what ran |
| Helm lint / kubeconform / Pod Security policy | 3 value sets, 53 rendered objects | ✓ | 0 | 1 (OpenShift Route: no Kubernetes schema) | — | PASS |
| Reference manifests | 12 objects | 12 | 0 | 0 | — | PASS |
| actionlint | 8 workflows | ✓ | 0 | — | — | PASS |
| gitleaks (directory and history) | — | no leaks | — | — | — | PASS |
| Documentation (`--strict`) | — | ✓ | — | — | — | PASS |
| UI pages (screenshot run, page errors and error states) | 41 | 41 | 0 | 0 | — | PASS |
| Compose build, start and restore drill (GitHub, `d2134e8`) | 1 | 1 | 0 | 0 | — | PASS |
| kind install, `helm test`, leader failover (GitHub, `d2134e8`) | 1 | 1 | 0 | 0 | — | PASS |
| Backend CI on GitHub (`d2134e8`) | — | — | 1 | — | — | FAIL (#12, fixed in 0.15.1) |
| Release workflow (tag `v0.15.0`) | — | — | 1 | — | — | FAIL (#13, fixed in 0.15.1) |
| Real engines, full Docker matrix (owner, 2026-10-21, 0.15.0 code) | 95 | 89 | 0 | 1 (Oracle TCPS) | 5 VictoriaMetrics tests deselected by the runner (#14, fixed in 0.15.1); Db2 for z/OS | PASS |

Cloud-workspace real-engine runs per suite (after):

| Suite | Passed | Failed | Skipped (reason) |
|---|---|---|---|
| `test_postgres_adapter` (PostgreSQL 16.15) | 12 | 0 | 0 |
| `test_mysql_family` (MariaDB 10.11.14, TLS `verify-ca`, wrong CA refused, `require`, `prefer`) | 14 | 0 | 14 (MySQL host not available) |
| `test_backup_engines` | 2 (PostgreSQL, MariaDB) | 0 | 4 (MySQL, SQL Server, Oracle, Db2 not available) |
| `test_victoriametrics` (1.122.0) | 5 | 0 | 0 |
| `test_identity` (OpenLDAP 2.6.10, StartTLS verified) | 2 | 0 | 1 (Keycloak not available) |
| `test_mssql`, `test_oracle`, `test_db2`, `test_vault_real` | 0 | 0 | 13 + 14 + 13 + 1 (engine not available) |

### Skipped tests

No test is skipped where its dependency is available. Every skip names the missing dependency:
real engines that cannot run in the cloud workspace, the two control-plane TLS tests without
`DBM_TEST_DATABASE_TLS_CA` (CI sets it), and Oracle TCPS (the Oracle XE image has no TCPS
listener: the only skip in the owner's Docker run that is not an environment choice). The MariaDB
TLS skip of the owner's run is removed: `docker-compose.test.yml` now gives MySQL and MariaDB a
certificate from a generated test CA, so the TLS tests run there.

## Failures and root causes

| # | Failure | Classification | Root cause | Fix | Regression coverage |
|---|---|---|---|---|---|
| 1 | Compose restore drill failed in GitHub Actions | PRODUCT BUG | `scripts/backup.sh` called BusyBox `wget` with options it does not have to create and delete the VictoriaMetrics snapshot | the snapshot calls run through Python in the backend container (TLS CA honoured) | `test_ops_scripts.py`: no BusyBox-incompatible `wget` options, no `DBM_*` secret on a command line, snapshots through the backend |
| 2 | MySQL TLS test failed in the owner's Docker run | TEST BUG | the test detected TLS through `have_ssl`, which MySQL 8.4 removed | detection by `have_ssl = YES` or a non-empty `Ssl_server_not_after` | the same test, now with TLS served by the test CA |
| 3 | MariaDB TLS test skipped in the owner's Docker run | ENVIRONMENT | the test containers served no TLS | `tls-certs` service generates a CA and server certificate; MySQL and MariaDB start with them; the runner gets the CA | the TLS tests run in the Docker matrix |
| 4 | LDAP StartTLS failed against a local OpenLDAP addressed by IP | PRODUCT BUG (dependency) | ldap3 matches host names only against DNS subject alternative names and refused a certificate with the IP SAN `127.0.0.1` | a hostname check that also accepts IP SANs, installed for ldap3 | `test_ldap_hostname.py` (3), `test_identity.py` StartTLS with CA verification |
| 5 | Replicas started together on an empty database failed (duplicate `pg_type`) | PRODUCT BUG (found while building 0.15 HA) | migrations and start-up work ran concurrently in every replica | PostgreSQL advisory locks around migrations and the start-up work | `tests/migrations/test_concurrent_start.py` (fails without the locks) |
| 6 | `GET /about`, open to every signed-in user, returned the scheduler leader's host name and process id | PRODUCT BUG (security, found in the 0.15 review) | the deployment summary was passed through unchanged | About returns counts only; the per-process list stays behind `collectors.view` | `test_instances.py`: a Monitoring User gets no host names from About and 403 on `/platform/instances` |
| 7 | Upgrades from 0.14.0 were not tested | STALE TEST | the release list of the upgrade test ended at 0.13.0 | 0.14.0 (schema `0013`) added; `report_schedules` seeded | `test_existing_installation_upgrades_to_head_with_its_data[0.14.0]` |
| 8 | An RBAC assertion accepted 200 or 403 | TEST BUG (written in this iteration) | the test did not decide which role it checked | exact 403 for Monitoring User, 200 for an environment DBA | `test_instances.py` |
| 9 | jsdom 30 broke a download test (`Blob.stream` missing) | DEPENDENCY | jsdom 30's `Blob` is not accepted by Node's `Response` | jsdom stays on 26 (works with vitest 5); browsers are unaffected | the download test |
| 10 | Vite 8 rejected the build configuration | MIGRATION | Rolldown accepts `manualChunks` only as a function | function form with package-family prefixes; the main chunk stays under the 900 kB limit | `vite build` in CI |
| 11 | The owner's run `integration-test.ps1 postgresql mysql … vault` (2026-10-21) selected 13 PostgreSQL tests and skipped them all (`DBM_IT_PG_HOST not set`); PostgreSQL was not started | PRODUCT BUG (test tooling) | PowerShell bound the first positional argument to `-K`, so `postgresql` became the pytest `-k` filter and was dropped from the engine list; `.\scripts\integration-test.ps1 mssql oracle` had the same defect. The matrix printed `—` as `â€”` (file read as ANSI) | `[CmdletBinding(PositionalBinding = $false)]`: engines are the only positional arguments; matrix read as UTF-8; an all-skipped run no longer re-dates `real_engine_validation.json` | `test_ops_scripts.py::test_powershell_integration_script_binds_every_engine_positionally` (binds the script's own parameters with PowerShell 7), `test_integration_matrix.py` |
| 12 | GitHub *Backend* failed: `test_verified_tls_to_the_control_plane` — `verify-full` to `127.0.0.1` did not raise | TEST BUG | the CI PostgreSQL certificate came from the shared generator, which also names `127.0.0.1`; locally the server certificate named only `localhost`. Reproduced here with the generator's certificate | CI generates it with `make_certs.py --dns-only`; the generator adds key identifiers (Python 3.13 strict verification) and regenerates material whose IP addresses differ | `test_control_plane_tls.py` passes against PostgreSQL 16 with the `--dns-only` certificate and fails with the old one |
| 13 | GitHub *Release* (tag `v0.15.0`) failed at *Set up job* | PRODUCT BUG (release workflow) | `sigstore/cosign-installer@v4` is not a tag of that action (only `v4.x.y`) | pinned to `v4.1.2`; every `uses:` reference checked against GitHub | published as 0.15.1 |

## Engine-level validation

| Engine | Cloud workspace (this release) | Owner's Docker run, 2026-10-21 (0.15.0 code, `d2134e8`) |
|---|---|---|
| PostgreSQL | 16.15: 12 + 1 backup passed | 16.15: every capability PASS |
| MySQL | not available | 8.4.11: every capability PASS, **TLS PASS** (was FAIL on 2026-10-20, #2) |
| MariaDB | 10.11.14: 14 + 1 backup passed, TLS verified | 11.4.13: every capability PASS, **TLS PASS** (was skipped, #3) |
| SQL Server | not available | 2022 (16.0.4305.5): every capability PASS |
| Oracle | not available | 21.3 XE: every capability PASS; TCPS skipped (no listener) |
| Db2 LUW | not available | 11.5.9.0: every tested capability PASS (restore is external) |
| Db2 for z/OS | — | EXTERNAL VALIDATION REQUIRED (z/OS subsystem with Db2 Connect; Db2 LUW results are never used for it) |
| VictoriaMetrics | 1.122.0: 5 passed | started, tests deselected (#14, fixed in 0.15.1) |
| OpenLDAP | 2.6.10: 2 passed (StartTLS verified) | osixia 1.5.0: PASS |
| Keycloak, Vault | not available | Keycloak 26, Vault 1.17.6: PASS |

Oracle licensed features (AWR, ASH, ADDM, Tuning Pack) stay disabled by default and are not used
by any test.

## How to reproduce

```bash
# backend (PostgreSQL 15+ reachable)
cd backend
DBM_TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dbm_test \
  pytest -q tests/unit tests/api tests/migrations tests/deploy
# frontend
cd frontend && npm ci && npx tsc -b --noEmit && npx vitest run && npm run build
# chart and manifests
helm lint deployment/helm/dbmanager -f deployment/helm/dbmanager/ci/default-values.yaml
python scripts/k8s-policy-check.py deployment/kubernetes/*.yaml
# every real engine (Docker)
./scripts/integration-test.sh postgresql mysql mariadb mssql oracle db2 timeseries identity vault
```
