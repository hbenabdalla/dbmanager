# Architecture

```text
API (app/api/v1/backups.py)
  └─ BackupService (app/backup/service.py)  — jobs, verification, retention, schedules, notifications
       ├─ platform archive (app/backup/platform.py)  — export, compatibility, trial restore, apply
       ├─ DatabaseBackupProvider (app/backup/engines.py) — one provider per engine
       ├─ encryption (app/backup/encryption.py)       — age via pyrage, no custom cryptography
       └─ StorageProvider (app/backup/storage.py)     — filesystem (local/mounted/NFS), S3-compatible
```

## Asynchronous jobs

Every backup, verification and restore is a job in `backup_jobs`. The API returns the job at once
(`202`); the worker process (`BackgroundRunner`, `DBM_PROCESS_ROLE=worker` or `all`) claims queued
jobs with `FOR UPDATE SKIP LOCKED`, runs at most `DBM_BACKUP_CONCURRENCY` (default 1) at a time and
commits progress in small steps, so the history shows what is happening. A running job sends a
heartbeat every minute; a job without heartbeat for `DBM_BACKUP_STALE_MINUTES` (default 30) is
marked `FAILED` ("worker lost").

| Job state | Meaning |
|---|---|
| `QUEUED` | waiting for a worker (can be cancelled) |
| `RUNNING` / `VERIFYING` | in progress (cancellation is requested and honoured between assets and while external tools run) |
| `SUCCESS` | every asset succeeded |
| `PARTIAL_SUCCESS` | some assets of a bulk backup failed |
| `FAILED` / `CANCELLED` | ended without (complete) result |

A running platform restore cannot be cancelled: it commits in one transaction.

Secrets supplied for one run (a passphrase, an age identity) are sealed with the platform key ring
in the job and removed when the job ends; they are never logged or returned by the API.

## Backup states

```text
CREATING ──► VERIFYING ──► SUCCESS
    │             └──────► CORRUPTED   (the stored copy does not match what was written)
    └──────────────────►  FAILED
SUCCESS ──(verification later fails)──► CORRUPTED
any ──(deleted)──► DELETED             (catalog entry kept for the audit trail)
```

## Storage layout

Every backup gets its own folder in the target:

```text
<target>/platform/2026/10/<backup id>/archive.tar.age   the archive (encrypted)
                                     /key.age           passphrase mode only: the wrapped per-backup key
                                     /manifest.json     catalog entry, unencrypted, no secrets
<target>/database/2026/10/<backup id>/<db>.dump.age     pg_dump / mysqldump output
```

`manifest.json` lets a fresh installation find and register backups without the original
database ([disaster recovery](disaster-recovery.md)). DBManager only deletes inside a backup's own
folder.

## Scheduler

The global job `backup_scheduler` runs every minute on the scheduler leader: it queues policies
whose next run is due (never twice while a run is active), marks lost jobs as failed, updates the
`dbm_backup_last_successful_backup_timestamp_seconds` gauge and sends "no successful backup within
policy" notifications.
