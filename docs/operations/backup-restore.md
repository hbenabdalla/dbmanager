# Backup and restore

!!! tip "In-application backups"
    This page describes the **stack-level** backup of a Docker Compose installation
    (`scripts/backup.sh`: a `pg_dump` of the whole control-plane database, run from the host).
    DB Manager also backs itself up and the managed databases from **Platform → Backup &
    Restore**: encrypted (age), to local, NFS or S3-compatible storage, scheduled by policies with
    retention, and restored per component with an impact analysis. See
    [Backup & Restore](../backup-restore/index.md) and
    [Disaster recovery](../backup-restore/disaster-recovery.md) for when to use which.

DB Manager has two persistence layers with different value:

| Layer | Contains | Loss means |
|---|---|---|
| PostgreSQL (`pgdata` volume) | users, RBAC, inventory, encrypted credentials, settings, recommendations, findings, audit, jobs, query fingerprint metadata | **the platform** — back it up like any production database |
| Time-series DB (`tsdata` volume) | monitoring history | charts and trends restart empty; nothing else is affected |

Losing monitoring history never prevents recovery: the application rebuilds its whole
state from PostgreSQL, collectors resume, and new telemetry starts flowing immediately.

Also back up, separately from both: **`DBM_MASTER_KEY`** (without it stored target
credentials cannot be decrypted) and `.env`.

## Scripts (Docker Compose)

```bash
scripts/backup.sh                      # backups/dbmanager-<UTC>.dump (+ .revision with the schema version)
scripts/backup.sh --with-metrics       # also backups/tsdata-<snapshot>.tgz (metric history)
scripts/restore.sh backups/dbmanager-20261018T080000Z.dump --yes
```

`backup.sh` dumps the control-plane database in `pg_dump` custom format and checks that the dump
is readable; `--with-metrics` adds a VictoriaMetrics snapshot. `restore.sh` stops the backend and
frontend, recreates the database from the dump and starts them again; the backend runs
`alembic upgrade head` on start (`DBM_RUN_MIGRATIONS_ON_START`, default on), so a dump taken on an
older release is upgraded to the current schema. `.env` must hold the `DBM_MASTER_KEY` that was in
use when the dump was taken.

What is verified automatically:

* `backend/tests/migrations/` (CI job *Backend tests*): every migration upgrades step by step,
  downgrades to base and upgrades again; the head schema equals the models; an installation
  holding data at the first revision and at the revision before head upgrades to head without
  losing rows; and a `pg_dump -Fc` / `pg_restore` round trip of a migrated, populated database
  keeps the schema revision, users (who can still sign in), the inventory and the encrypted
  credentials, which decrypt with the same key and fail with any other key.
* `scripts/restore-drill.sh` (CI job *Docker images → compose*): on a throw-away Compose stack it
  creates a marker user and environment, runs `scripts/backup.sh --with-metrics`, deletes the
  volumes (`docker compose down -v`), checks that the data is gone, runs `scripts/restore.sh`,
  then signs in as the marker user and compares the schema revision with the backup. The metric
  snapshot is checked to exist; restoring it is the manual procedure below.

## PostgreSQL (manual commands)

```bash
# backup (consistent, online)
docker compose exec -T postgres pg_dump -U dbmanager -Fc dbmanager > dbmanager-$(date +%F).dump
# restore into an empty database
docker compose stop backend
docker compose exec -T postgres dropdb -U dbmanager dbmanager
docker compose exec -T postgres createdb -U dbmanager dbmanager
docker compose exec -T postgres pg_restore -U dbmanager -d dbmanager < dbmanager-2026-10-15.dump
docker compose start backend
```

Production: continuous archiving / PITR (pgBackRest, Barman, or the managed service's
backups).

## Time-series database

VictoriaMetrics supports instant snapshots (hard links, no downtime):

```bash
# create a snapshot inside the volume
docker compose exec timeseries wget -qO- --user dbm --password "$DBM_TIMESERIES_PASSWORD" \
  http://127.0.0.1:8428/snapshot/create
# -> {"status":"ok","snapshot":"20261015120000-…"}
# copy it out (the snapshot lives in /storage/snapshots/<name>)
docker run --rm -v database-manager_tsdata:/storage -v "$PWD":/backup alpine \
  tar czf /backup/tsdata-<name>.tgz -C /storage/snapshots <name>
# delete the snapshot afterwards
docker compose exec timeseries wget -qO- --user dbm --password "$DBM_TIMESERIES_PASSWORD" \
  "http://127.0.0.1:8428/snapshot/delete?snapshot=<name>"
```

For regular, incremental backups to object storage use `vmbackup` / `vmrestore`
(open-source tools from the same project):

```bash
vmbackup -storageDataPath=/storage -snapshot.createURL=http://dbm:<pw>@timeseries:8428/snapshot/create \
         -dst=s3://bucket/dbmanager/tsdb/latest
```

Restore:

```bash
docker compose stop timeseries
docker run --rm -v database-manager_tsdata:/storage -v "$PWD":/backup alpine sh -c \
  'rm -rf /storage/* && mkdir -p /storage && tar xzf /backup/tsdata-<name>.tgz -C /tmp && cp -a /tmp/<name>/. /storage/'
docker compose start timeseries
```

(or `vmrestore -src=s3://… -storageDataPath=/storage` with the container stopped).

Samples written while the time-series database was being restored are buffered by the
backend (up to `DBM_METRIC_BUFFER_MAX`) and written when it is back.

## Disaster recovery

1. Restore PostgreSQL and `.env` (with the same `DBM_MASTER_KEY`). Start the stack: the
   platform is fully usable.
2. Restore the time-series snapshot if history is needed; otherwise start with an empty
   volume — history rebuilds from new collections.
3. Optional: if the legacy PostgreSQL metric tables still hold data, re-run
   `python -m app.cli metrics migrate-to-timeseries --restart` (idempotent).

Recommended targets: PostgreSQL RPO minutes (PITR), RTO < 1 h; time-series RPO 24 h (daily
snapshot) — monitoring gaps are acceptable, configuration loss is not.

## Volume persistence

`pgdata` and `tsdata` are named Docker volumes and survive `docker compose down` and image
upgrades. `docker compose down -v` deletes them. On Kubernetes both are PersistentVolumeClaims
(`StatefulSet` volumeClaimTemplates).
