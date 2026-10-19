# SQL Server

* **Backup:** `BACKUP DATABASE [db] TO DISK = N'<dir>/<db>_<time>_<id>.bak' WITH COPY_ONLY,
  CHECKSUM, INIT, FORMAT` — `COPY_ONLY` does not disturb the log chain of your own backup schedule.
  The file is written **by SQL Server on its own host or container**, into the instance default
  backup directory (`SERVERPROPERTY('InstanceDefaultBackupPath')`, SQL Server 2019+) or the
  validated *server directory* of the policy (absolute Windows or Linux path, no quotes or shell
  characters). DB Manager records size and checksum flag from `msdb.dbo.backupset`.
* **Verification:** `RESTORE VERIFYONLY … WITH CHECKSUM`.
* **Restore:** refused when the target database exists; `RESTORE VERIFYONLY`, `RESTORE
  FILELISTONLY`, then `RESTORE DATABASE [new] … WITH MOVE` of every file into the instance default
  data and log directories, `CHECKSUM, RECOVERY`.
* **Containers:** paths are inside the SQL Server container (for example
  `/var/opt/mssql/data`); mount that directory to keep the files.
* **Permissions:** `sysadmin` or `db_backupoperator` for backups, `dbcreator` for restores.
* Not supported: Azure SQL Database (backups are managed by Azure). Not managed: log backups,
  point-in-time restores, copying the `.bak` file off the server, deleting it.
