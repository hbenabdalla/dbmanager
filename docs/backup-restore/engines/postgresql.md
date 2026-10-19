# PostgreSQL

* **Backup:** `pg_dump --format=custom` of one database, run by DB Manager with the administration
  credential (`PGPASSWORD` in the environment, `PGSSLMODE` from the server's TLS mode, the server's
  CA certificate or the system trust store for `verify-ca`/`verify-full`). The dump is encrypted,
  stored in the target and its SHA-256 checked after upload.
* **Verification:** `pg_restore --list` must list the dump's table of contents (level `engine`).
* **Restore:** `CREATE DATABASE "<new>"` (refused when it exists), then
  `pg_restore --no-owner --exit-on-error`. A failed restore leaves the new database for inspection;
  DB Manager does not drop it.
* `pg_dump` must be the same major version as the server or newer.
* Not managed: `pg_basebackup`, WAL archiving, point-in-time recovery, roles and tablespaces
  (`pg_dumpall --globals-only`) — restore those with your own tooling.
