# Troubleshooting

| Message | Cause and fix |
|---|---|
| `… is outside the allowed backup directories` | The target directory is not under `DBM_BACKUP_ALLOWED_ROOTS`. Add the root (and mount it in the container) or choose a directory inside it. |
| `the directory is not on a mounted file system` (check warning) | A `mounted`/`nfs` target points at a plain directory: backups would stay on this host. Mount the share first. |
| `S3 AccessDenied / NoSuchBucket` | Credentials, bucket, region or addressing style (MinIO and Ceph usually need `path`). |
| `TOOL_MISSING` / `pg_dump is not installed` | The image was built with `INSTALL_BACKUP_TOOLS=false`, or set `DBM_BACKUP_PG_BIN_DIR` / `DBM_BACKUP_MYSQL_BIN_DIR`. |
| `pg_dump: error: aborting because of server version mismatch` | `pg_dump` is older than the server; use a newer client (the image ships 17). |
| `an administration credential is required` | Add an administration credential under Server → Credentials. |
| `This server is registered as Monitoring only` | Restores need *Administration enabled* (Server → Settings). |
| `database … already exists: restores never overwrite a database` | Choose a new name. |
| `the dump contains objects qualified with the source database name` | MySQL/MariaDB definitions reference the source database; restore under the same name on another server or edit the dump. |
| `REQUIRES_CONFIGURATION: online backups need archive logging` | Db2: configure `LOGARCHMETH1`. |
| `SQL Server does not report a default backup directory` | Before SQL Server 2019: set the policy's server directory. |
| `this backup is encrypted: supply its …` | Paste the age identity / passphrase, or configure `DBM_BACKUP_IDENTITY_FILE` / a passphrase reference. |
| `wrong passphrase (or the key file is damaged)` / `decryption failed` | Wrong key, or the archive was modified. |
| `integrity check failed` / status `CORRUPTED` | The stored object does not match its checksum: restore from another backup and check the storage. |
| `incompatible backup` / *Unsupported* | The backup was made by a newer or unknown schema: restore it with that KBM DBManager version or newer. |
| `master key(s) … are not configured` | Configure the old key in `DBM_PREVIOUS_MASTER_KEYS`. |
| `worker lost (no heartbeat)` | The worker stopped during the job; run it again. |
| `the trial restore failed: IntegrityError …` | Restoring only some components would break a reference (for example servers whose environments are not restored): add the referenced component. |
| `422 safety_backup_required` | A platform restore was requested without the pre-restore safety backup. Keep it on, or have a Platform Administrator enable `backup.allow_restore_without_safety_backup` ([Restore](restore.md#platform-restore)). |
| Notification *Backup storage unavailable* | The storage target could not be written or read (permissions, full disk, unmounted share, S3 credentials or network). **Check** the target under Storage targets. |
| Job `CANCELLED`, backup `FAILED: cancelled: …` | Someone cancelled the job; it stopped at the next checkpoint without storing a partial backup ([Architecture](architecture.md#asynchronous-jobs)). |
| No notification for a manual backup | Backups that no policy owns notify only the channels named in `backup.notification_channels`. Backup events never appear on the Alerts page ([Policies → Notifications](policies.md#notifications)). |
| Scoped user does not see a policy or job | A grant limited to servers sees a policy or job only when every server it acts on is in scope, and never platform backups. |
