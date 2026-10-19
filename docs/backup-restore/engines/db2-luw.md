# Db2 for Linux, UNIX and Windows

* **Backup:** `CALL SYSPROC.ADMIN_CMD('BACKUP DATABASE <db> ONLINE TO <dir> INCLUDE LOGS')` on the
  Db2 server, into the policy's *server directory* (required). Online backups need archive logging
  (`LOGARCHMETH1` not `OFF`); otherwise the backup fails with `REQUIRES_CONFIGURATION`.
* **Verification:** the recovery history (`SYSIBMADM.DB_HISTORY`) must record the backup without
  an error. A full image check needs `db2ckbkp` on the Db2 host (external tool).
* **Restore:** *external tool required*. DB Manager shows the command to run on the Db2 host:

  ```text
  db2 RESTORE DATABASE <db> FROM <dir> TAKEN AT <timestamp> INTO <new> LOGTARGET <dir>/dbm-restore-logs-<timestamp> WITHOUT PROMPTING
  db2 ROLLFORWARD DATABASE <new> TO END OF BACKUP AND COMPLETE OVERFLOW LOG PATH (<dir>/dbm-restore-logs-<timestamp>)
  ```

* **Least privilege:** `BACKUP DATABASE` needs an instance-level authority; the smallest is
  **SYSMAINT**, granted through the operating-system group named in `SYSMAINT_GROUP` (changing it
  needs an instance restart). The backup account then needs `CONNECT`, `EXECUTE` on
  `SYSPROC.ADMIN_CMD` and `SELECT` on `SYSIBMADM.DB_HISTORY`.
  Do not use SYSADM or the instance owner:

  ```sql
  -- on the Db2 host, as the instance owner (once): groupadd dbmmaint; useradd -g dbmmaint dbmbk
  -- db2 update dbm cfg using SYSMAINT_GROUP DBMMAINT; db2stop; db2start
  GRANT CONNECT ON DATABASE TO USER DBMBK;
  GRANT EXECUTE ON PROCEDURE SYSPROC.ADMIN_CMD TO USER DBMBK;
  GRANT SELECT ON SYSIBMADM.DB_HISTORY TO USER DBMBK;
  ```

* Applies to Db2 LUW only — never to Db2 for z/OS.
