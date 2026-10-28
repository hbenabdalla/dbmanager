# Backup & Restore

KBM DBManager protects two kinds of assets from **Platform → Backup & Restore**:

* **The platform itself** — KBM DBManager's own configuration and state (organizations, inventory,
  encrypted credential references, users, groups, roles, identity providers, findings and their
  triage state, alerting and notification configuration, settings, audit forwarding, backup
  configuration, and optionally the audit log and monitoring telemetry). See
  [Platform backup](platform-backup.md).
* **Managed databases** — backups taken with each engine's own tooling (`pg_dump`, `mysqldump` /
  `mariadb-dump`, SQL Server `BACKUP DATABASE`, Oracle Data Pump, Db2 `BACKUP DATABASE`). See
  [Database backup](database-backup.md) and [Engine support](engine-support.md).

Backups are written to [storage targets](storage-targets.md) (a local directory, a mounted
volume, an NFS mount or an S3-compatible bucket), [encrypted](encryption.md) with
[age](https://age-encryption.org), run on demand or by [policies](policies.md) with
[retention](retention.md), and restored with a guided [restore wizard](restore.md) that shows the
exact impact before anything changes.

!!! important "A backup is only successful once it has been read back"
    A backup ends `SUCCESS` only after its stored copy was read back from the storage target and
    its SHA-256 matched (and, where possible, its contents or the engine's own check passed). A
    tool that merely exited with code 0 is not enough.

## Pages

| Page | Content |
|---|---|
| [Architecture](architecture.md) | Components, job model, states, storage layout |
| [Platform backup](platform-backup.md) | What is in a platform archive and what is not |
| [Database backup](database-backup.md) | Managed databases, bulk backups, per-asset progress |
| [Restore](restore.md) | The restore wizard and its safety rules |
| [Policies](policies.md) · [Retention](retention.md) | Schedules, protection status, retention rules, notifications (sent to channels; not alerts) |
| [Storage targets](storage-targets.md) | Directories, NFS, S3-compatible buckets |
| [Encryption](encryption.md) | age recipients and passphrases, master-key dependency |
| [Engine support](engine-support.md) | What each engine can do (and cannot) |
| [Disaster recovery](disaster-recovery.md) | Rebuilding KBM DBManager from its backups |
| [Security and RBAC](security.md) | Permissions, audit events, metrics, notifications |
| [Troubleshooting](troubleshooting.md) | Common errors |

## Two backup mechanisms

This section describes the **in-application** backups. The stack-level procedure in
[Operations → Backup and restore](../operations/backup-restore.md) (`scripts/backup.sh`, a
`pg_dump` of the whole control-plane database run from the host) remains available and is still
the simplest way to back up and restore an entire Docker Compose installation. The two complement
each other:

| | Stack backup (`scripts/backup.sh`) | Platform backup (this section) |
|---|---|---|
| Runs | from the host, by an operator or cron | inside KBM DBManager, on demand or by policy |
| Content | the whole PostgreSQL database, every table | selected components (operational tables excluded) |
| Encryption | none (protect the file yourself) | age recipient or passphrase |
| Restore | replaces the whole database | per component, with trial run and impact analysis |
| Storage | the host's `backups/` directory | any configured storage target |
