# Testing

| Suite | Location | Needs | Command |
|---|---|---|---|
| Backend unit | `backend/tests/unit` | nothing | `cd backend && pytest tests/unit` |
| Backend API (RBAC, security, onboarding, simulation, alerting …) | `backend/tests/api` | scratch PostgreSQL in `DBM_TEST_DATABASE_URL` — its schema is dropped | `cd backend && pytest tests/api` |
| Real-engine integration | `backend/tests/integration` | real engines (`docker-compose.test.yml`) | `scripts/integration-test.sh [engines]` |
| Frontend components, flows, API client | `frontend/src/test` | nothing (fetch is mocked) | `cd frontend && npm test` |

`DBM_TEST_DATABASE_URL` defaults to `postgresql+asyncpg://dbm:dbm_dev_pw@localhost:5432/dbmanager_test`;
API tests are skipped when it is unreachable.

## Markers

Defined in `backend/pyproject.toml`: `unit`, `api`, `integration`, `real_database`, one per engine
(`postgresql`, `mysql`, `mariadb`, `sqlserver`, `oracle`, `db2`, `db2zos`, `timeseries`), and
`engine(name)` / `capability(*names)` for the validation matrix.

```bash
pytest -m "integration and oracle"
```

## Unit tests for adapters

`tests/unit/fakedb.py` provides a scripted DB-API connection: every statement must match a route,
otherwise the test fails — so adapter tests prove which SQL is issued. Adapter suites also
syntax-check every statement constant in `sql.py` and run all read paths against an empty server.

## Real-engine tests

Each integration file is skipped unless its `DBM_IT_<ENGINE>_HOST` variable is set (VictoriaMetrics:
`DBM_IT_VM_URL`). The script starts the vendors' images, waits until they answer SQL and runs the
suites inside the backend image. Results go to `integration-results/` (`DBM_IT_RESULTS_DIR`) and are
merged into `backend/app/data/real_engine_validation.json` (`DBM_IT_MERGE_INTO`). Details, images
and licenses: [Integration tests](../operations/integration-tests.md).

!!! warning "Mocked is not validated"
    Unit tests with the fake DB-API prove the adapter's logic, not compatibility with a real server.
    Only real-engine runs update the validation matrix.
