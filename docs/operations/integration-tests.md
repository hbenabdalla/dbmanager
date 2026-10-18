# Real-engine integration tests

`docker-compose.test.yml` is separate from the application deployment (which stays four
containers: frontend, backend, PostgreSQL, VictoriaMetrics). One command starts real engines from
the vendors' own registries, waits until each one answers SQL, runs the adapter suites inside the
backend image (all drivers, including Microsoft ODBC Driver 18) and writes the validation matrix:

```powershell
.\scripts\integration-test.ps1                 # Windows (Docker Desktop / WSL)
.\scripts\integration-test.ps1 mssql oracle    # a subset
.\scripts\integration-test.ps1 -Down           # remove the test containers and volumes
```
```bash
scripts/integration-test.sh [postgresql mysql mariadb mssql oracle db2 timeseries]
```

| Engine | Image (registry) | License you accept by starting it | First start |
|---|---|---|---|
| PostgreSQL 16 | `postgres:16-alpine` (Docker Hub) | PostgreSQL License | seconds |
| MySQL 8.4 / MariaDB 11.4 | `mysql:8.4`, `mariadb:11.4` (Docker Hub) | GPLv2 | < 1 min |
| SQL Server 2022 Developer | `mcr.microsoft.com/mssql/server:2022-latest` (Microsoft Container Registry) | Microsoft EULA (`ACCEPT_EULA=Y`) | ~1 min |
| Oracle Database 21c XE | `container-registry.oracle.com/database/express:21.3.0-xe` (Oracle Container Registry) | Oracle Free Use Terms | 5–20 min |
| Oracle 21c EE (`oracle-ee` profile, opt-in) | `container-registry.oracle.com/database/enterprise:21.3.0.0` | Oracle license; `docker login container-registry.oracle.com` after accepting it on the registry | 10–30 min |
| Db2 11.5 Community Edition | `icr.io/db2_community/db2` (IBM Container Registry, privileged) | IBM license (`LICENSE=accept`) | 5–10 min |
| VictoriaMetrics | `victoriametrics/victoria-metrics` (Docker Hub) | Apache-2.0 | seconds |

DB Manager never downloads or redistributes Oracle software itself: the script pulls the image on
your machine, under your acceptance. Enterprise Edition does not change DB Manager's licensing
policy — Diagnostics/Tuning Pack features stay off, and the Oracle suite proves from `V$SQL` that
the monitoring user never queried AWR, ASH, ADDM, SQL Monitor or the SQL Tuning Advisor.

* `.env.test` is generated on first run with random passwords (git-ignored, never committed).
* Health checks run real SQL (`pg_isready` + query, `mysql -e`, `sqlcmd -C`, Oracle's
  `checkDBStatus.sh`, `db2 connect to TESTDB`); Oracle and Db2 get long start periods.
* Each suite creates its own fixtures (schemas, tables, indexes, views, routines, triggers,
  5 000 customers / 20 000 orders), a workload, a real blocking chain and least-privilege users
  matching the [engine pages](../database-engines/index.md). Db2 identities are OS users created by
  `backend/tests/integration/db2/custom/dbm-it-users.sh`.
* Results: `integration-results/real-engine-matrix.{json,md}` and `run.log`; the run merges them
  into `backend/app/data/real_engine_validation.json`, which **Platform → Engine support** shows
  (a capability is only shown as plain FULL once the suite validated it; otherwise `FULL*`).
* Db2 for z/OS cannot run in a container: it stays "external validation required".

The test layout, markers and how to run individual suites are described in
[Testing](../development/testing.md).
