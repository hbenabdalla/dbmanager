# Restore

Open a backup in **Backup history → Restore**. The wizard walks through:

1. **Select backup** — and supply its key when it is encrypted (age identity or passphrase; used
   for this run only, never stored).
2. **Validate manifest** — format, creation date and user, DBManager and schema versions.
3. **Verify integrity** — the stored archive is downloaded, its SHA-256 compared with the catalog
   and every file in it compared with the manifest. A mismatch marks the backup `CORRUPTED` and
   stops the restore.
4. **Check compatibility**:
    * **Compatible** — same schema version.
    * **Migration required** — the backup has an older schema of this product: rows are mapped
      column by column and columns added since then take their defaults. You must confirm this.
    * **Unsupported** — unknown or newer schema, another archive format, or a required column has
      no value: restore it with the DBManager version that made it (or newer).
    * Missing master keys (`key_ids` not configured) are reported; you can only continue by
      confirming that those encrypted values will not be decryptable.
5. **Select components** — any subset of the components in the backup.
6. **Impact analysis** — the restore runs once inside a transaction that is **rolled back**, and
   reports per table how many rows would be added, overwritten and removed, plus rows that foreign
   keys would cascade-delete in tables that are not restored (for example alert history of a rule
   that disappears). A failure of the trial (for example a foreign key that cannot be satisfied)
   blocks the restore.
7. **Confirmation** — type `RESTORE PLATFORM`. A **pre-restore safety backup** of every component
   (same target, same encryption) is taken first unless you turn it off.
8. **Restore** → 9. **Validation** (row counts equal the backup for every replaced table) →
   10. **Result**, including the id of the safety backup.

Restore semantics: rows absent from the backup are deleted, rows in the backup are inserted or
overwritten by primary key; the `audit` component is merge-only (missing events are added, nothing
is deleted or overwritten). Sequences follow the restored ids. Sign-in sessions are not restored:
if your own account is not in the backup, the wizard warns you.

## Database restore

A database backup is restored **into a new database (or schema) on a server of the same engine**:
choose the server and a new name, then type that name to confirm. **Restores never overwrite an
existing database**: when the name exists the job fails and nothing is changed. The restore job
re-checks the stored file's SHA-256 and the engine's own verification before restoring.

* PostgreSQL: `CREATE DATABASE`, then `pg_restore --no-owner --exit-on-error`.
* MySQL / MariaDB: `CREATE DATABASE … utf8mb4`, then the dump through the command-line client.
  Dumps containing definitions qualified with the source database name (``CREATE TRIGGER `src`.t``)
  are refused under another name, because they would change the source database.
* SQL Server: `RESTORE VERIFYONLY`, `RESTORE FILELISTONLY`, then `RESTORE DATABASE … WITH MOVE`
  of every file into the instance default data/log directories, `CHECKSUM, RECOVERY`.
* Oracle: Data Pump import with `REMAP_SCHEMA` into a new schema.
* Db2 LUW and Db2 for z/OS: **external tool required** — the wizard shows the command to run on the
  Db2 host; DBManager does not run it.

## Safety rules

* Restores need `restore.execute` plus `restore.platform` (platform) or `restore.database`
  (on the target server); Super Admin is not required. See [Security and RBAC](security.md).
* Every request, start and completion (or failure) is audited with the job's correlation id.
* Only one restore runs at a time.
* Never test restores against production systems: use isolated environments (the automated
  restore tests in the repository only use throw-away databases and containers).
