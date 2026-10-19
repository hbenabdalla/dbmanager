#!/usr/bin/env bash
# Restore the control-plane database of a Docker Compose installation from a backup.sh dump.
#   scripts/restore.sh <dbmanager-*.dump> --yes
# Stops the backend, replaces the database contents, runs migrations (an older dump is upgraded to
# the current schema) and starts the backend again. .env must contain the DBM_MASTER_KEY that was
# in use when the dump was taken. Metric history (tsdata-*.tgz) is restored separately, see
# docs/operations/backup-restore.md.
set -euo pipefail
cd "$(dirname "$0")/.."
DUMP="${1:?usage: scripts/restore.sh <dump> --yes}"
[ "${2:-}" = "--yes" ] || { echo "this replaces the current DBManager database; re-run with --yes" >&2; exit 2; }
[ -f "$DUMP" ] || { echo "no such file: $DUMP" >&2; exit 2; }
. scripts/lib/dotenv.sh; load_dotenv .env
PGU="${POSTGRES_USER:-dbmanager}"; PGD="${POSTGRES_DB:-dbmanager}"

docker compose stop backend frontend
docker compose up -d --wait postgres
docker compose exec -T postgres psql -U "$PGU" -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$PGD' AND pid <> pg_backend_pid()" \
  -c "DROP DATABASE IF EXISTS \"$PGD\"" -c "CREATE DATABASE \"$PGD\" OWNER \"$PGU\""
docker compose exec -T postgres pg_restore -U "$PGU" -d "$PGD" --no-owner --exit-on-error < "$DUMP"
docker compose up -d --wait backend frontend   # the backend runs "alembic upgrade head" on start
echo "restored. Sign in and check Platform → About (schema version) and a server's Test connection."
