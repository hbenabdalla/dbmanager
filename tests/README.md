# Tests

| Suite | Location | Needs | Command |
|---|---|---|---|
| Backend unit (classifier, crypto, RBAC scopes, rules, plans, adapters, simulation planner) | `backend/tests/unit` | nothing | `cd backend && pytest tests/unit` |
| Backend API, RBAC, security and simulation lifecycle (fixture: a deterministic simulated CI fleet) | `backend/tests/api` | scratch PostgreSQL in `DBM_TEST_DATABASE_URL` (schema is dropped) | `cd backend && pytest tests/api` |
| PostgreSQL adapter integration | `backend/tests/integration` | a real PostgreSQL, see `docker-compose.test.yml` | `DBM_IT_PG_HOST=... pytest tests/integration` |
| Frontend components, UI flows, API client | `frontend/src/test` | nothing (fetch is mocked) | `cd frontend && npm test` |

`scripts/test-all.sh` runs everything.
