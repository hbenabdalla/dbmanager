# MySQL

* **Backup:** `mysqldump --single-transaction --routines --triggers --events --hex-blob
  --no-tablespaces --default-character-set=utf8mb4 --set-gtid-purged=OFF <db>` (one database,
  without `CREATE DATABASE`/`USE`, so it can be restored under another name). Credentials and TLS
  options go through a temporary `0600` option file.
* **Consistency:** InnoDB tables are consistent; non-transactional tables (MyISAM) are not under
  `--single-transaction`.
* **Verification:** the dump header and the `-- Dump completed` trailer.
* **Restore:** `CREATE DATABASE … utf8mb4` (refused when it exists), then the dump through `mysql`.
  A dump with definitions qualified with the source database name (for example
  ``CREATE TRIGGER `shop`.trg``) is refused under another name, because it would change the source
  database; restore it on another server under the same name or edit the definitions.
* **Least privilege:** backup `SELECT, SHOW VIEW, TRIGGER, EVENT, LOCK TABLES` (no `PROCESS`
  thanks to `--no-tablespaces`, no `RELOAD` thanks to `--single-transaction`); restore into new
  databases `CREATE, DROP, ALTER, INDEX, INSERT, UPDATE, DELETE, CREATE VIEW, CREATE ROUTINE,
  ALTER ROUTINE, REFERENCES` on the target name pattern. `DEFINER` clauses are removed, so the
  restoring account needs no `SUPER` / `SET_USER_ID`: objects are owned by it.
* **Binary logging:** with binary logging on (the MySQL 8 default) and
  `log_bin_trust_function_creators = OFF`, only an account with `SUPER` can create triggers and
  routines (error 1419). DBManager checks this before creating the target database and refuses
  with the fix: a DBA sets `SET PERSIST log_bin_trust_function_creators = ON` on the target server
  (a deliberate decision: it lets routine creators write non-deterministic routines to the binary
  log), or the restore runs with an account that holds `SUPER`. Dumps without triggers or routines
  are not affected.
* **Tools:** the Docker image ships the MariaDB client; with only that client MySQL is `PARTIAL`.
  Mount MySQL's `mysqldump`/`mysql` and set `DBM_BACKUP_MYSQL_BIN_DIR` for full fidelity.
* Not managed: MySQL Enterprise Backup, physical backups, binary-log point-in-time recovery, users
  and grants (the `mysql` system database).
