# Roadmap traceability

Capability → gap → milestone/sprint → implementation → tests → documentation. A row is only
marked DONE when the implementation and tests columns point at real code. Paths are relative to
the repository root; "—" means not yet created.

## Baseline (DONE)

| Capability | Gap | Sprint | Implementation | Tests | Documentation |
|---|---|---|---|---|---|
| Engine adapters (7) with capability discovery | — | Baseline | `backend/app/adapters/{postgres,mysql,mssql,oracle,db2,db2zos}/` | `backend/tests/unit/test_{adapters,mssql,oracle,db2,mysql_family}.py`, `backend/tests/integration/` (75 real-engine tests) | `docs/development/adding-a-database-engine.md`, `docs/database-engines/permissions.md` |
| Real-engine validation matrix | — | Baseline | `backend/tests/integration/{matrix,conftest}.py`, `backend/app/data/real_engine_validation.json`, `scripts/integration-test.{sh,ps1}` | the integration suites themselves | `docs/operations/index.md` |
| Telemetry pipeline | — | Baseline | `backend/app/timeseries/`, `backend/app/services/collection.py`, `backend/app/jobs/` | `backend/tests/unit/test_timeseries.py`, `backend/tests/integration/test_victoriametrics.py`, `backend/tests/api/test_metrics.py` | `docs/monitoring/architecture.md`, `docs/monitoring/timeseries-storage.md` |
| Advisors (performance + security rules) | — | Baseline | `backend/app/advisors/` | `backend/tests/unit/test_rules.py`, security/recommendation tests in integration suites | `docs/security/index.md` |
| RBAC & scopes | — | Baseline | `backend/app/domain/{permissions,scopes}.py`, `backend/app/services/authz.py` | `backend/tests/unit/test_authz.py`, `backend/tests/api/test_rbac.py` | `docs/administration/rbac.md` |
| Guarded administration | — | Baseline | `backend/app/adapters/admin.py`, `backend/app/api/v1/admin_actions.py` | administration tests in each integration suite | `docs/database-engines/permissions.md` |
| Onboarding & wizards | — | Baseline | `backend/app/api/v1/{setup,onboarding,idp_wizard}.py`, `frontend/src/features/{setup,onboarding}/` | `backend/tests/api/test_onboarding.py`, `frontend/src/test/wizards.test.tsx` | `docs/architecture/ux-audit.md` |
| Topology | — | Baseline | `frontend/src/features/topology/` | `frontend/src/test/topology.test.tsx` | `docs/architecture/ux-audit.md` |
| Driver health (SQL Server ODBC) | — | Baseline | `backend/app/adapters/drivers.py` | `backend/tests/unit/test_drivers.py`, `backend/tests/api/test_servers.py` | `docs/database-engines/index.md` |

## Milestone 0.9 — Release foundation

| Capability | Gap | Sprint | Implementation | Tests | Documentation | Status |
|---|---|---|---|---|---|---|
| Source control & publication | G-01 | 0.9.1 | Git repository, `origin` → `hbenabdalla/dbmanager` | first CI run | `CONTRIBUTING.md` | IN PROGRESS |
| License | G-02 | 0.9.1 | `LICENSE` (Apache-2.0), `backend/pyproject.toml`, `frontend/package.json`, About page | `tests/api/test_alerting.py` (About reports Apache-2.0) | `README.md`, `CONTRIBUTING.md` | DONE |
| Governance files | G-05, G-07 | 0.9.1 | `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md`, `.gitignore` | secret scan | same files | IN PROGRESS |
| CI | G-03 | 0.9.2 | `.github/workflows/` | workflows | `CONTRIBUTING.md` | IN PROGRESS |
| Documentation site | G-06 | 0.9.3 | `mkdocs.yml`, `.readthedocs.yaml`, `docs/requirements.txt`, `docs/**` | `mkdocs build --strict` | this site | IN PROGRESS |
| Version & About page | G-04 | 0.9.4 | version API + About page (in progress) | API + UI tests (in progress) | administration guide | IN PROGRESS |
| Upgrade & recovery verification | G-20, G-22 | 0.9.5 | — | — | operations guide | NEXT |

## Milestone 0.10 — Operations & alerting

| Capability | Gap | Sprint | Implementation | Tests | Documentation | Status |
|---|---|---|---|---|---|---|
| Alert rules & evaluation | G-10, G-11 | 0.10.1 | in progress (backend alerting module, migration) | in progress | user guide "Alerts" | IN PROGRESS |
| SMTP & webhook channels | G-12, G-13, G-14 | 0.10.2 | in progress | in progress | administration guide | IN PROGRESS |
| Maintenance windows | G-15 | 0.10.3 | in progress | in progress | user guide | IN PROGRESS |
| Alert UX, Teams/Slack presets, re-notify | G-16, G-17 | 0.10.4 | — | — | — | PLANNED |
| Collector diagnostics bundle | G-23 | 0.10.5 | — | — | — | PLANNED |

## Later milestones

| Capability | Gap | Sprint | Status |
|---|---|---|---|
| API tokens | G-18 | 0.11.1 | NEXT |
| API rate limiting | G-19 | 0.11.2 | PLANNED |
| Real LDAP/AD/OIDC validation | G-40 | 0.11.3 | PLANNED |
| SIEM export | G-33 | 0.11.4 | PLANNED |
| Vault / secret managers | G-36 | 0.11.5 | PLANNED |
| MFA readiness | G-38 | 0.11.6 | PLANNED |
| Baselines in alerts/rules | G-24 | 0.12.1 | PLANNED |
| Query regression detection | G-27 | 0.12.2 | PLANNED |
| Capacity forecasting | G-25 | 0.12.3 | PLANNED |
| Query comparison | G-28 | 0.12.4 | PLANNED |
| Recommendation validation | G-29 | 0.12.5 | PLANNED |
| Security & query reports | G-32 | 0.13.1 | PLANNED |
| Scheduled reports | G-30 | 0.13.2 | PLANNED |
| PDF export | G-31 | 0.13.3 | PLANNED |
| Helm, images, manifest CI, HA/TLS reference | G-41, G-42, G-44, G-45 | 0.14.x | PLANNED |
| Scale, security, accessibility, upgrade drill, RC | G-54, G-58, G-22 | 1.0.x | PLANNED |
| Oracle TCPS validation | G-55 | Future | PLANNED |
| Db2 for z/OS validation and deep monitoring | G-56 | Future | BLOCKED |

Back to the [roadmap](roadmap.md).
