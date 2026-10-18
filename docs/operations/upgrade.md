# Upgrade

1. **Back up first** — the application database and, separately, `DBM_MASTER_KEY`
   ([Backup and restore](backup-restore.md)). The time-series volume holds only monitoring history.
2. **Read the changelog** for the target version (`CHANGELOG.md` in the repository).
3. **Pull or build the new images** and restart.

## Database migrations

The schema is managed by Alembic (`backend/alembic/versions/`, currently `0001_initial` …
`0004_alerting`). Migrations are incremental and do not delete data.

| Deployment | How migrations run |
|---|---|
| Docker Compose | at backend start (`DBM_RUN_MIGRATIONS_ON_START=true`, the default), followed by role sync and bootstrap |
| Kubernetes / OpenShift | the `dbmanager-migrate` Job (`10-migrate-job.yaml`: `python -m app.cli migrate && python -m app.cli bootstrap`); API and worker pods run with `DBM_RUN_MIGRATIONS_ON_START=false` |
| Manual | `python -m app.cli migrate` inside the backend container |

`/readyz` reports the applied `schema_version`; the backend stays unready until migrations are
applied. Every revision has a `downgrade()`, but downgrading is not a substitute for restoring the
backup taken in step 1.

## Docker Compose

```bash
git pull                      # or check out the release tag, e.g. vX.Y.Z
docker compose up -d --build
docker compose logs -f backend
```

## Kubernetes / OpenShift

Update the image tags, run the migrate Job to completion, then roll out the backend, worker and
frontend Deployments.

## Master key rotation

Set the new key as `DBM_MASTER_KEY` (with a new `DBM_MASTER_KEY_ID`) and the old one in
`DBM_PREVIOUS_MASTER_KEYS` (`id:base64key`, comma separated), restart, then run
`python -m app.cli rotate-master-key` to re-wrap all stored secrets. Remove the previous key
afterwards.
