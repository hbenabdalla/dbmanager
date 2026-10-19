# Db2 for Linux, UNIX and Windows

* **Backup:** `CALL SYSPROC.ADMIN_CMD('BACKUP DATABASE <db> ONLINE TO <dir> INCLUDE LOGS')` on the
  Db2 server, into the policy's *server directory* (required). Online backups need archive logging
  (`LOGARCHMETH1` not `OFF`); otherwise the backup fails with `REQUIRES_CONFIGURATION`.
* **Verification:** the recovery history (`SYSIBMADM.DB_HISTORY`) must record the backup without
  an error. A full image check needs `db2ckbkp` on the Db2 host (external tool).
* **Restore:** *external tool required*. DB Manager shows the command to run on the Db2 host:

  ```text
  db2 RESTORE DATABASE <db> FROM <dir> TAKEN AT <timestamp> INTO <new> LOGTARGET DEFAULT
  db2 ROLLFORWARD DATABASE <new> TO END OF BACKUP AND COMPLETE OVERFLOW LOG PATH (...)
  ```

* Applies to Db2 LUW only — never to Db2 for z/OS.
