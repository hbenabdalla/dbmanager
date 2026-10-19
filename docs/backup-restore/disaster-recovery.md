# Disaster recovery

Goal: rebuild DB Manager after the loss of its host or database, from backups stored **off the
host**.

## What you need

* A platform backup in an off-host storage target (S3-compatible bucket or NFS/SMB share), ideally
  produced by a scheduled policy.
* Its decryption key: the age identity (`AGE-SECRET-KEY-1…`) or the passphrase.
* The **master key** that was in use (`DBM_MASTER_KEY`, and any older keys in
  `DBM_PREVIOUS_MASTER_KEYS`). Without it the restored credentials and secrets cannot be decrypted.
* `.env` (or your deployment's configuration) — or at least the values above.

Keep the key and the master key in a vault or password manager, separately from the backups.

## Procedure

1. **Rebuild the host** and install DB Manager — the same version as the backup or a newer one
   (an older version reports the backup as *Unsupported*).
2. Configure **the same `DBM_MASTER_KEY`** (plus previous keys) before the first start. Start the
   stack; the bootstrap administrator signs in to the empty installation.
3. **Add the storage target** that holds the backups (Backup & Restore → Storage targets), with the
   same bucket/prefix or mounted directory, and **Check** it.
4. **Scan for backups**: the catalog entries (`manifest.json`) in the target are registered. They
   show the verification level `imported`.
5. **Verify** the newest platform backup with its key. It becomes `SUCCESS` when the archive and
   every file match their checksums.
6. **Restore** it with the restore wizard (all components; add `audit` to bring the audit log
   back). The trial run shows the impact; the bootstrap administrator is replaced by the accounts
   in the backup — sign in again with one of them.
7. **Re-check**: Platform → About (version, schema), Server list (registrations, credentials:
   *Test connection*), identity providers, notification channels, backup policies and targets.
   Collectors resume automatically; monitoring history restarts unless you also restore telemetry
   (VictoriaMetrics export, or the stack-level `tsdata` snapshot).
8. Restore managed databases as needed from their database backups (into new databases), or with
   the engines' own tools where restore is external.

## Relation to the stack backup

`scripts/backup.sh` / `scripts/restore.sh` ([Operations → Backup and restore](../operations/backup-restore.md))
back up and restore the whole PostgreSQL database of a Docker Compose installation from the host.
`scripts/restore-drill.sh` proves that procedure end to end in CI on a throw-away stack. Use the
stack backup for whole-installation snapshots (for example before an upgrade) and platform backups
for encrypted, off-host, policy-driven protection with selective restore. The in-application
restore path is covered by `backend/tests/api/test_backup.py` (backup → change → restore →
validate, on a test database only).
