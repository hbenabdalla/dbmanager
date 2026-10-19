# Oracle

* **Backup:** a Data Pump **schema** export with `DBMS_DATAPUMP` into a directory object on the
  database server (default `DATA_PUMP_DIR`), consistent as of its start (`FLASHBACK_TIME`). The dump
  and log file stay on the database server.
* **Verification:** `DBMS_DATAPUMP.GET_DUMPFILE_INFO`.
* **Restore:** Data Pump import with `REMAP_SCHEMA` into a **new** schema (refused when the user
  exists), `TABLE_EXISTS_ACTION=SKIP`.
* **Licensing:** no Data Pump compression or encryption options are used (they need the Advanced
  Compression / Advanced Security options).
* **Permissions** (validated without `DBA`, `SYS` or `SYSTEM`): `CREATE SESSION`, `CREATE TABLE`
  and a tablespace quota (Data Pump's master table), `DATAPUMP_EXP_FULL_DATABASE` /
  `DATAPUMP_IMP_FULL_DATABASE` (or the schema owner for its own schema) and `READ, WRITE` on the
  directory object. Connect through the PDB service, not the CDB root.
* **RMAN:** physical backups and point-in-time recovery need RMAN — *external tool required*; DB
  Manager does not run RMAN and does not claim RMAN backups.
