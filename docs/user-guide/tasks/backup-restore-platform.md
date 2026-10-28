# Back up and restore the platform

**Goal:** KBM DBManager's own configuration — inventory, identity, findings, alerting, settings and
backup configuration — is backed up on a schedule, verified, and can be restored after a mistake
or onto a rebuilt installation.

## Before you start

* Permissions at **platform scope**: `backup.view` and `backup.create` to back up;
  `backup.policy.manage` for policies; `backup.target.manage` for storage targets;
  `restore.execute` **and** `restore.platform` to restore or download a platform backup. Platform
  Administrator holds all of them. (The Backup Operator role cannot back up or restore the platform.)
* A storage target (**Platform → Backup & Restore → Storage targets → New target**): a local
  directory, mounted volume, NFS mount or S3-compatible bucket, ideally outside the KBM DBManager host.
  **Check** on the target reports whether KBM DBManager can write to it.
* **`DBM_MASTER_KEY` kept safely elsewhere.** Stored database credentials inside the backup stay
  encrypted with the master key; the key itself is never included. Without it, a restored
  installation cannot decrypt them.

## Back up

1. Open **Platform → Backup & Restore → Create backup**.
2. **Type** — choose:
    * **Platform configuration** — organizations, inventory, identity, findings, alerting, settings
      and backup configuration;
    * **Platform + metrics** — the same plus a VictoriaMetrics export of recent telemetry;
    * **Custom** — pick the components yourself, for example to include the **audit** log.

    **Continue**.
3. **Scope** — review the components (with **Custom**, tick them). For *Platform + metrics* and
   *Custom*, set **Days of telemetry to export**. **Continue**.
4. **Storage target**, **Encryption** (**age public key (recommended)**, **Passphrase** or
   **None**), **Retention** — **Continue** after each. Backups created here are manual and never
   removed by retention.
5. **Review** — select **Start backup**. **Execute** shows the job; **Verification** shows the result
   of reading the stored copy back. With an encrypted backup, **Verify contents with the key**
   checks every file in the archive.

For a nightly backup: **Backup policies → New policy**, **What to back up** *KBM DBManager platform
(configuration)*, **Frequency** *Daily* at a **Time (HH:MM)** and **Time zone**, **Retention**
rules, **Notification channels** for failures, and **Alert when no successful backup for (hours)**
(0 = derived from the schedule: daily 26 h, weekly 8 days, monthly 32 days). Use a passphrase
*reference* (`env:NAME` or `file:/path`) for encrypted scheduled backups.

![Backup & Restore overview](../../assets/screenshots/backup-dashboard.png)

The **Overview** tab shows the last platform backup, failures, running jobs and the next scheduled
runs.

## Restore

A platform restore replaces the selected components of the current configuration.

1. In **Platform → Backup & Restore → Backup history**, set **Type** to *Platform* and select the
   restore icon on the backup.
2. **Select backup** — enter the private key or passphrase if it is encrypted. **Continue**.
3. **Validate manifest** — format, creator, KBM DBManager and schema version, components. **Continue**.
4. **Verify integrity** — the archive matches its SHA-256 and every table its checksum. **Continue**.
5. **Check compatibility** — *compatible*, or *migration required* when the backup comes from an
   older schema (columns added since then take their defaults), or unsupported. Missing master keys
   are listed here (`DBM_PREVIOUS_MASTER_KEYS`). **Continue**.
6. **Select components** — untick what you do not want to replace. **audit** is *merged*: missing
   rows are added, nothing is deleted. **Continue**.
7. **Impact analysis** — per table: rows in the backup, added, overwritten, removed, and other
   tables changed by cascading deletes. It is computed by a trial restore that is rolled back.
   **Continue**.
8. **Confirmation** — keep **Take a safety backup of the current platform first (recommended)**
   ticked. If you untick it, the wizard warns that the restore is refused unless an administrator
   has turned on `backup.allow_restore_without_safety_backup` in **Platform → Configuration →
   Settings**. Accept the column mapping or missing keys if asked, type `RESTORE PLATFORM` and
   select **Restore**.
9. **Restore**, **Validation**, **Result** — the result names the safety backup taken before the
   restore. A running platform restore cannot be cancelled: it commits in one transaction.

## What you should see

* The restored configuration (users, servers, rules … of the selected components).
* A new platform backup in **Backup history** — the safety backup — that undoes the restore if
  needed.
* Every backup, verification and restore in **Security → Audit logs**.

## If it does not work

* *Integrity check failed*: the stored copy is damaged; use another backup.
* Credentials do not work after restoring onto another installation: set the same
  `DBM_MASTER_KEY` (or list the old one in `DBM_PREVIOUS_MASTER_KEYS`) and restart.
* The restore is refused without a safety backup: tick the safety backup, or change the setting.
* For a complete disaster recovery of the host (control-plane database, time-series data, `.env`)
  see [Disaster recovery](../../backup-restore/disaster-recovery.md) and the stack scripts in
  [Operations → Backup and restore](../../operations/backup-restore.md).
* More: [Platform backup](../../backup-restore/platform-backup.md),
  [Restore](../../backup-restore/restore.md),
  [Backup and restore troubleshooting](../../backup-restore/troubleshooting.md).
