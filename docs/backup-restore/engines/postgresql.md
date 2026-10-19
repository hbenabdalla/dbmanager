# PostgreSQL

* **Backup:** `pg_dump --format=custom` of one database, run by DBManager with the administration
  credential (`PGPASSWORD` in the environment, `PGSSLMODE` from the server's TLS mode, the server's
  CA certificate or the system trust store for `verify-ca`/`verify-full`). The dump is encrypted,
  stored in the target and its SHA-256 checked after upload.
* **Verification:** `pg_restore --list` must list the dump's table of contents (level `engine`).
* **Restore:** `CREATE DATABASE "<new>"` (refused when it exists), then
  `pg_restore --no-owner --exit-on-error`. A failed restore leaves the new database for inspection;
  DBManager does not drop it.
* **Server older than the client tools** (for example `pg_restore` 17 against PostgreSQL 16): the
  dump is rendered to a script with `pg_restore --file`, settings the server does not know
  (`transaction_timeout`, PostgreSQL 17) are dropped, and the script runs with
  `psql --set ON_ERROR_STOP=1`. The result reports `tool: … → psql` and the settings dropped.
* **Least privilege** (validated): a role with `CREATEDB` and membership in `pg_read_all_data`; no
  superuser. With `--no-owner` the restored objects belong to that role.
* `pg_dump` must be the same major version as the server or newer.
* Not managed: `pg_basebackup`, WAL archiving, point-in-time recovery, roles and tablespaces
  (`pg_dumpall --globals-only`) — restore those with your own tooling.
