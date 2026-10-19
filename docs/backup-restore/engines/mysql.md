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
* **Tools:** the Docker image ships the MariaDB client; with only that client MySQL is `PARTIAL`.
  Mount MySQL's `mysqldump`/`mysql` and set `DBM_BACKUP_MYSQL_BIN_DIR` for full fidelity.
* Not managed: MySQL Enterprise Backup, physical backups, binary-log point-in-time recovery, users
  and grants (the `mysql` system database).
