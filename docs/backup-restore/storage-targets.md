# Storage targets

| Kind | Description |
|---|---|
| `local` | a directory on the DBManager host / container volume |
| `mounted` | a directory on a mounted volume (block storage, SMB) — the check warns when it is not a mount point |
| `nfs` | a directory on an NFS mount (mount it on the host or in the container; DBManager does not mount shares) |
| `s3` | an S3-compatible bucket: AWS S3, MinIO, Ceph RGW, … (endpoint, bucket, prefix, region, access key, path or virtual addressing, TLS verification with an optional CA) |

Azure Blob Storage and Google Cloud Storage are **planned, not implemented**.

## Rules

* File-system targets must be inside one of `DBM_BACKUP_ALLOWED_ROOTS` (default
  `/var/lib/dbmanager/backups`); symbolic links are resolved before the check and object keys can
  never leave the target directory.
* Writes go to a temporary name and are renamed into place, so a crash never leaves a half-written
  file under the final name.
* The S3 secret access key is stored envelope-encrypted and never returned.
* **Check** writes, reads back and deletes a small probe object and reports free space.
* **Scan for backups** registers backups found in the target (their `manifest.json`) that the
  catalog does not know — used for [disaster recovery](disaster-recovery.md).
* A target can only be removed when no policy uses it and no catalog entry is stored on it;
  removing a target never deletes stored files.

## Docker Compose

`docker-compose.yml` mounts a `backups` volume at `/var/lib/dbmanager/backups`
(`DBM_BACKUP_HOST_DIR` replaces it with a host directory or a mounted share) and a `backupwork`
volume at `/var/lib/dbmanager/work` for building archives and dumps (`DBM_BACKUP_WORK_DIR`; not
tmpfs, dumps can be large). Keep at least one target **off the DBManager host** — a local
directory does not survive the loss of the host.
