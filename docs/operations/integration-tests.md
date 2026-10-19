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
scripts/integration-test.sh [postgresql mysql mariadb mssql oracle db2 timeseries identity vault]
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
| OpenLDAP (`identity`) | `osixia/openldap:1.5.0` (Docker Hub) | OpenLDAP Public License | seconds |
| Keycloak 26 (`identity`) | `quay.io/keycloak/keycloak:26.0.7` (Quay) | Apache-2.0 | < 1 min |
| HashiCorp Vault 1.17 dev server (`vault`) | `hashicorp/vault:1.17.6` (Docker Hub) | Business Source License 1.1 | seconds |

DBManager never downloads or redistributes Oracle software itself: the script pulls the image on
your machine, under your acceptance. Enterprise Edition does not change DBManager's licensing
policy — Diagnostics/Tuning Pack features stay off, and the Oracle suite proves from `V$SQL` that
the monitoring user never queried AWR, ASH, ADDM, SQL Monitor or the SQL Tuning Advisor.

* `.env.test` is generated on first run with random passwords (git-ignored, never committed).
* Health checks run real SQL (`pg_isready` + query, `mysql -e`, `sqlcmd -C`, Oracle's
  `checkDBStatus.sh`, `db2 connect to TESTDB`); Oracle and Db2 get long start periods.
* Each suite creates its own fixtures (schemas, tables, indexes, views, routines, triggers,
  5 000 customers / 20 000 orders), a workload, a real blocking chain and least-privilege users
  matching the [engine pages](../database-engines/index.md). Db2 identities are OS users created by
  `backend/tests/integration/db2/custom/dbm-it-users.sh`, including the backup operator `DBMBK`
  (SYSMAINT through `SYSMAINT_GROUP`; the setup restarts the Db2 instance once to apply it, and
  enables archive logging for online backups). The MySQL backup test checks the binary-logging
  refusal and then sets `log_bin_trust_function_creators` on the disposable container (reset at
  the end). All accounts: [database permissions](../database-permissions/index.md).
* Results: `integration-results/real-engine-matrix.{json,md}` and `run.log`; the run merges them
  into `backend/app/data/real_engine_validation.json`, which **Platform → Engine support** shows
  (a capability is only shown as plain FULL once the suite validated it; otherwise `FULL*`).
* Db2 for z/OS cannot run in a container: it stays "external validation required".

## Identity providers and Vault

Two targets test the platform's own integrations instead of a database engine:

* **`identity`** starts OpenLDAP and Keycloak with fixed test directories from
  `backend/tests/integration/identity/` (`ldap/50-dbm-it.ldif`: service account `svc-dbm`, users
  `alice` and `bob`, groups `dbadmins` and `developers`; `keycloak/dbm-realm.json`: realm `dbm`
  with the confidential client `dbmanager` and the same users and groups). `tests/integration/test_identity.py`
  checks the LDAP service bind, user search, password bind, group search, StartTLS (and that a
  self-signed certificate is refused with verification on), wrong passwords and an LDAP-injection
  attempt; and the OpenID Connect authorization-code flow with PKCE through Keycloak's real login
  form, ID-token validation, the group claim, replayed codes, a forged state, `require_mfa` without
  MFA evidence and a wrong client secret.
* **`vault`** starts a Vault **dev** server (in memory, root token from `.env.test`).
  `tests/integration/test_vault_real.py` writes KV v2 secrets, reads them with token auth, sets up
  AppRole with a policy for one path and checks that reads outside it are denied.

The passwords in these fixtures are fixed test values for the test containers
only. Results appear in the matrix as `openldap`, `keycloak` and `vault` with the capabilities
`authentication`, `group_mapping`, `mfa` and `secrets`.

## `.env.test`

The script creates `.env.test` with random values on first run and, when a newer version needs
more keys, **appends** the missing ones to an existing file (existing values are kept):

| Key | Used by |
|---|---|
| `IT_PG_PASSWORD`, `IT_MYSQL_PASSWORD`, `IT_MARIADB_PASSWORD`, `IT_MSSQL_SA_PASSWORD`, `IT_ORACLE_PASSWORD` | the engine containers' administrator accounts |
| `IT_DB2_PASSWORD`, `IT_DB2_MON_PASSWORD`, `IT_DB2_ADM_PASSWORD`, `IT_DB2_RO_PASSWORD` | Db2 instance owner and the OS users of the least-privilege accounts |
| `IT_LDAP_ADMIN_PASSWORD` | OpenLDAP `cn=admin` (`identity`) |
| `IT_KEYCLOAK_ADMIN_PASSWORD` | Keycloak bootstrap administrator (`identity`) |
| `IT_VAULT_TOKEN` | root token of the Vault dev server (`vault`) |

`.env.test` is git-ignored; never commit it.

The test layout, markers and how to run individual suites are described in
[Testing](../development/testing.md).

## Output and narrower runs

The runner prints one line per test (`-v`) and stops any single test after 15 minutes. Every
failure message is also written to `integration-results/junit.xml`. To run only some tests,
pass a pytest `-k` expression:

```shell
.\scripts\integration-test.ps1 -K backup                 # Windows
IT_PYTEST_ADDOPTS='-k backup' scripts/integration-test.sh   # Linux / macOS
```

## Troubleshooting

**A runner from an interrupted run is still going.** Remove it before starting again:
`docker rm -f $(docker ps -q --filter name=it-runner-run)`.


**The run stops after `CALL SYSPROC.SYSINSTALLOBJECTS(...)` (Db2 user setup).** An earlier run that
was interrupted with Ctrl+C keeps running *inside* the Db2 container and can hold catalog locks.
Db2 waits for a lock forever by default, so the next run blocks behind it. The setup script now:

- stops any earlier run of itself and its stale CLP processes when it starts;
- skips the explain tables when they already exist;
- gives up on a lock after 60 seconds;
- runs under a 15-minute `timeout`.

If a container is still stuck from before this fix, restart Db2 once (its data volume is kept):

```shell
docker compose -f docker-compose.test.yml --env-file .env.test --profile db2 restart db2
```

**Running `docker compose` yourself:** always pass `--env-file .env.test` (the passwords live there), otherwise Compose stops with "required variable IT_… is missing a value".

**Containers are still running after an interrupted run.** The script only stops the engines at
the end of a complete run. To stop them, or to start from empty databases:

```shell
docker compose -f docker-compose.test.yml --env-file .env.test --profile all stop   # keep data
scripts/integration-test.sh --down        # remove containers and data (Windows: .\scripts\integration-test.ps1 -Down)
```
