# Security and RBAC

## Permissions

| Permission | Scope | Allows |
|---|---|---|
| `backup.view` | platform or servers | backups, jobs, policies, targets, protection status |
| `backup.create` | platform (platform backups) or servers | run backups, *Run now* |
| `backup.verify` | platform or servers | verify stored backups |
| `backup.delete` | platform only | delete stored backups |
| `backup.policy.manage` | platform only | create/edit/delete policies, pin backups, generate key pairs |
| `backup.target.manage` | platform only | storage targets, *Scan for backups* |
| `restore.execute` | platform or servers | start restores (with one of the next two) |
| `restore.platform` | platform only | restore DB Manager's configuration; download platform backups |
| `restore.database` | servers | restore managed databases; download database backups |

Built-in roles: **Platform Administrator** has all of them; **Backup Operator** (`backup.view`,
`backup.create`, `backup.verify`, `restore.execute`, `restore.database`, plus server and database
view) runs and verifies backups and restores managed databases but cannot restore the platform or
delete backups; **DBA Administrator** gets the same backup permissions on its scope; **Security
Auditor** gets `backup.view`. Restores do not require the Platform Administrator role.

## Audit events (category `backup`)

| Action | Recorded when |
|---|---|
| `backup.request` | a backup is requested (API) |
| `backup.create` | a backup ends (success or failure), per asset |
| `backup.verify` | a verification ends |
| `backup.delete`, `backup.retention` | a backup is deleted by a user / by retention |
| `backup.import` | backups are registered by *Scan for backups* |
| `backup.pin`, `backup.unpin`, `backup.download`, `backup.cancel`, `backup.key_generate` | the corresponding action |
| `restore.request`, `restore.start`, `restore.complete` | a restore is requested, starts, ends (with result) |
| `backup_target.create/update/delete`, `backup_policy.create/update/delete` | configuration changes |

Every event carries the user, time, asset, backup id and job id; events written by a job carry the
request's correlation id. Keys, identities, passphrases and passwords are never part of an event.
Audit events can be forwarded to a SIEM ([Audit forwarding](../administration/siem.md), category
`backup`).

## Metrics

Exposed on `/metrics` of the worker process:

| Metric | Type |
|---|---|
| `dbm_backup_jobs_total{kind,result}` | counter |
| `dbm_backup_failures_total{kind}` | counter (failed or corrupted backups) |
| `dbm_backup_duration_seconds{kind}` | histogram |
| `dbm_backup_bytes_total{kind}` | counter (bytes written to storage) |
| `dbm_restore_jobs_total{kind,result}` | counter |
| `dbm_restore_failures_total{kind}` | counter |
| `dbm_backup_last_successful_backup_timestamp_seconds{policy}` | gauge (`policy="manual"` for manual backups) |

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `DBM_BACKUP_ALLOWED_ROOTS` | `/var/lib/dbmanager/backups` | directories file-system targets must be in |
| `DBM_BACKUP_WORK_DIR` | system temp (Compose: `/var/lib/dbmanager/work`) | scratch space |
| `DBM_BACKUP_CONCURRENCY` | 1 | backup jobs at the same time per worker |
| `DBM_BACKUP_STALE_MINUTES` | 30 | heartbeat timeout of a running job |
| `DBM_BACKUP_IDENTITY_FILE` | — | age identity for content verification / restore without pasting the key |
| `DBM_BACKUP_PG_BIN_DIR`, `DBM_BACKUP_MYSQL_BIN_DIR` | PATH | client tool directories |
| `DBM_BACKUP_TOOL_TIMEOUT_SECONDS` | 21600 | longest external tool run |
| `DBM_BACKUP_HOST_DIR` (Compose) | volume `backups` | host directory or share mounted as the default backup directory |
