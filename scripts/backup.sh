#!/usr/bin/env bash
# Back up a Docker Compose installation of DB Manager.
#   scripts/backup.sh [--with-metrics] [output-dir]
# Writes <output-dir>/dbmanager-<UTC timestamp>.dump (pg_dump custom format of the control-plane
# database: identity, inventory, encrypted credentials, findings, audit, settings) and, with
# --with-metrics, tsdata-<snapshot>.tgz (a VictoriaMetrics snapshot of the metric history).
# The dump contains credentials encrypted with DBM_MASTER_KEY: back the key (.env) up separately
# and store both securely. Restoring needs the same key. See docs/operations/backup-restore.md.
set -euo pipefail
cd "$(dirname "$0")/.."
METRICS=0
if [ "${1:-}" = "--with-metrics" ]; then METRICS=1; shift; fi
OUT="${1:-backups}"
mkdir -p "$OUT"
set -a; [ -f .env ] && . ./.env; set +a
TS=$(date -u +%Y%m%dT%H%M%SZ)
PGU="${POSTGRES_USER:-dbmanager}"; PGD="${POSTGRES_DB:-dbmanager}"

echo "control-plane database -> $OUT/dbmanager-$TS.dump"
docker compose exec -T postgres pg_dump -U "$PGU" -d "$PGD" -Fc > "$OUT/dbmanager-$TS.dump"
docker compose exec -T postgres pg_restore --list < "$OUT/dbmanager-$TS.dump" > /dev/null  # dump is readable
docker compose exec -T backend python -m alembic current 2>/dev/null | tail -1 > "$OUT/dbmanager-$TS.revision" || true

if [ "$METRICS" = 1 ]; then
  VMU="${DBM_TIMESERIES_USERNAME:-dbm}"
  SNAP=$(docker compose exec -T timeseries wget -qO- --user "$VMU" --password "$DBM_TIMESERIES_PASSWORD" \
    http://127.0.0.1:8428/snapshot/create | sed -n 's/.*"snapshot":"\([^"]*\)".*/\1/p')
  [ -n "$SNAP" ] || { echo "snapshot creation failed" >&2; exit 1; }
  VOL=$(docker volume ls --format '{{.Name}}' | grep -E '_tsdata$' | head -1)
  echo "metric history snapshot $SNAP -> $OUT/tsdata-$SNAP.tgz"
  docker run --rm -v "$VOL":/storage:ro -v "$(cd "$OUT" && pwd)":/backup alpine \
    tar czf "/backup/tsdata-$SNAP.tgz" -C /storage/snapshots "$SNAP"
  docker compose exec -T timeseries wget -qO- --user "$VMU" --password "$DBM_TIMESERIES_PASSWORD" \
    "http://127.0.0.1:8428/snapshot/delete?snapshot=$SNAP" > /dev/null
fi
echo "done. Keep DBM_MASTER_KEY (in .env) with these files: without it stored credentials cannot be decrypted."
