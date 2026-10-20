#!/usr/bin/env bash
# Back up a Docker Compose installation of DBManager.
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
. scripts/lib/dotenv.sh; load_dotenv .env
TS=$(date -u +%Y%m%dT%H%M%SZ)
PGU="${POSTGRES_USER:-dbmanager}"; PGD="${POSTGRES_DB:-dbmanager}"

echo "control-plane database -> $OUT/dbmanager-$TS.dump"
docker compose exec -T postgres pg_dump -U "$PGU" -d "$PGD" -Fc > "$OUT/dbmanager-$TS.dump"
docker compose exec -T postgres pg_restore --list < "$OUT/dbmanager-$TS.dump" > /dev/null  # dump is readable
docker compose exec -T backend python -m alembic current 2>/dev/null | tail -1 > "$OUT/dbmanager-$TS.revision" || true

if [ "$METRICS" = 1 ]; then
  # Ask VictoriaMetrics for a snapshot through the backend container: it already holds the
  # time-series credentials in its environment, so they never appear on a command line (and the
  # BusyBox wget of the VictoriaMetrics image has no --user/--password options).
  vm() {  # vm <path> -> response body
    docker compose exec -T backend python - "$1" <<'PY'
import os, sys, httpx
s = os.environ
auth = (s["DBM_TIMESERIES_USERNAME"], s["DBM_TIMESERIES_PASSWORD"]) if s.get("DBM_TIMESERIES_USERNAME") else None
url = s.get("DBM_TIMESERIES_URL", "http://timeseries:8428").rstrip("/") + sys.argv[1]
r = httpx.get(url, auth=auth, timeout=120, verify=s.get("DBM_TIMESERIES_TLS_CA_FILE") or True)
r.raise_for_status()
print(r.text)
PY
  }
  SNAP=$(vm /snapshot/create | sed -n 's/.*"snapshot":"\([^"]*\)".*/\1/p')
  [ -n "$SNAP" ] || { echo "snapshot creation failed" >&2; exit 1; }
  VOL=$(docker volume ls --format '{{.Name}}' | grep -E '_tsdata$' | head -1)
  echo "metric history snapshot $SNAP -> $OUT/tsdata-$SNAP.tgz"
  docker run --rm -v "$VOL":/storage:ro -v "$(cd "$OUT" && pwd)":/backup alpine \
    tar czf "/backup/tsdata-$SNAP.tgz" -C /storage/snapshots "$SNAP"
  vm "/snapshot/delete?snapshot=$SNAP" > /dev/null
fi
echo "done. Keep DBM_MASTER_KEY (in .env) with these files: without it stored credentials cannot be decrypted."
