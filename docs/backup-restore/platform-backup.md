# Platform backup

A platform backup is a tar archive (format `dbmanager-platform/1`), usually age-encrypted:

```text
manifest.json                       versions, components, row counts, per-file SHA-256, key_ids
tables/<table>.jsonl                one JSON object per row
metrics/victoriametrics.native      optional telemetry export
```

All tables are exported inside one `REPEATABLE READ` snapshot, so the archive is consistent across
tables.

## Components

| Component | Tables |
|---|---|
| `organization` | organizations, environments, sites, clusters, applications |
| `inventory` | servers, server tags, credentials (still encrypted), discovered databases and accounts |
| `identity` | users (password hashes, encrypted TOTP seeds), groups, members, roles, role assignments, identity providers, group mappings, linked identities, API tokens (hashes) |
| `findings` | recommendations and security findings with their triage state, saved query plans |
| `alerting` | alert rules, maintenance windows, notification channels (webhook URLs and secrets still encrypted), report schedules |
| `settings` | platform settings, audit forwarding (SIEM) destinations |
| `backup_config` | backup storage targets and policies |
| `audit` | audit log and SQL workspace history — *optional*, merged on restore, never deleted |

The default selection is every component except `audit`. **Platform + metrics** adds a
VictoriaMetrics native export (`/api/v1/export/native`) of the last *N* days (0 = all retained);
it requires the VictoriaMetrics time-series backend. Native, server-side VictoriaMetrics snapshots
are not taken by the in-app backup: use VictoriaMetrics' own `vmbackup` (or the snapshot step of
`scripts/backup.sh`) when you need a full copy of a large telemetry store.

The `inventory` component is KBM DBManager's **server configuration protection**: it preserves how
each server is registered (connection settings, placement, tags, credential references,
monitoring settings). It does not back up the servers' operating systems or database files.

## Not included

Operational state that rebuilds itself is excluded on purpose: sign-in sessions, rate-limit
counters, the collector job queue, legacy metric tables and query fingerprints, simulation run
history, alert instances and comments, notification delivery logs, report run history, and the
backup job history and catalog. A unit test fails whenever a new table is added without being classified.

## Secrets

Secrets are exported exactly as stored: still envelope-encrypted with the platform master key
(`DBM_MASTER_KEY`). The manifest lists the master-key ids they need (`key_ids`). **The master key
itself is never written into a backup.** Restoring credentials therefore needs the same master key
(or the old one listed in `DBM_PREVIOUS_MASTER_KEYS`); keep it safe separately from the backups.
See [Encryption](encryption.md).

## Verification

* At creation: the stored object is read back and its SHA-256 compared. When KBM DBManager can decrypt
  it (unencrypted, passphrase known, or `DBM_BACKUP_IDENTITY_FILE` set), the archive is extracted
  and every file's checksum compared with the manifest (`content` level); otherwise the level is
  `checksum`.
* Later: **Verify** in the history (optionally with the key for this run) repeats the check. A
  mismatch marks the backup `CORRUPTED`.
