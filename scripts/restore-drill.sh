#!/usr/bin/env bash
# End-to-end restore drill for a Docker Compose installation (used by CI, safe only on a
# throw-away stack: it DELETES the stack's volumes).
#
#   scripts/restore-drill.sh --i-understand-this-wipes-the-stack
#
# 1. signs in as the bootstrap admin and creates a marker user and a marker environment
# 2. runs scripts/backup.sh --with-metrics
# 3. wipes the installation (docker compose down -v) and starts an empty one
# 4. checks that the marker user is gone (the wipe really happened)
# 5. runs scripts/restore.sh
# 6. signs in as the marker user, finds the marker environment and compares the schema revision
#    reported by /api/v1/about with the revision recorded by the backup
set -euo pipefail
cd "$(dirname "$0")/.."
[ "${1:-}" = "--i-understand-this-wipes-the-stack" ] || {
  echo "this deletes the Compose volumes; re-run with --i-understand-this-wipes-the-stack" >&2; exit 2; }
command -v curl >/dev/null && command -v python3 >/dev/null || { echo "needs curl and python3" >&2; exit 2; }
set -a; [ -f .env ] && . ./.env; set +a
BASE="${DRILL_BASE_URL:-http://localhost:8080}"
OUT="$(mktemp -d)"
JAR="$OUT/cookies"
MARK_USER="drill.$(date -u +%H%M%S)"
MARK_PW="Drill-Passw0rd!$(date -u +%s)"
MARK_ENV="Drill $(date -u +%Y%m%dT%H%M%S)"

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
wait_healthy() {
  for _ in $(seq 1 90); do curl -fsS "$BASE/healthz" >/dev/null 2>&1 && return 0; sleep 2; done
  echo "backend did not become healthy" >&2; docker compose logs --no-color backend | tail -50; exit 1
}
login() {  # login <user> <password> -> prints the CSRF token, keeps the session cookie in $JAR
  curl -fsS -c "$JAR" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$1\",\"password\":\"$2\"}" "$BASE/api/v1/auth/login" | json 'd["csrf_token"]'
}
api() {  # api <method> <path> [json-body]
  curl -fsS -b "$JAR" -X "$1" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
    ${3:+-d "$3"} "$BASE/api/v1$2"
}

echo "== 1. marker data"
wait_healthy
CSRF=$(login admin "$DBM_BOOTSTRAP_ADMIN_PASSWORD")
api POST /users "{\"username\":\"$MARK_USER\",\"password\":\"$MARK_PW\",\"must_change_password\":false}" >/dev/null
api POST /environments "{\"name\":\"$MARK_ENV\",\"tier\":\"test\"}" >/dev/null

echo "== 2. backup"
scripts/backup.sh --with-metrics "$OUT/backup"
DUMP=$(ls "$OUT"/backup/dbmanager-*.dump | head -1)
REV=$(cat "$OUT"/backup/dbmanager-*.revision | awk '{print $1}')
ls "$OUT"/backup/tsdata-*.tgz >/dev/null
[ -s "$DUMP" ] || { echo "empty dump" >&2; exit 1; }

echo "== 3. wipe (down -v) and start an empty installation"
docker compose down -v
docker compose up -d --wait --wait-timeout 600
wait_healthy

echo "== 4. the marker user must be gone"
if login "$MARK_USER" "$MARK_PW" >/dev/null 2>&1; then echo "wipe failed: marker user still exists" >&2; exit 1; fi

echo "== 5. restore"
scripts/restore.sh "$DUMP" --yes
wait_healthy

echo "== 6. verify"
CSRF=$(login "$MARK_USER" "$MARK_PW")
CSRF=$(login admin "$DBM_BOOTSTRAP_ADMIN_PASSWORD")
api GET /environments | python3 -c "import json,sys; envs=[e['name'] for e in json.load(sys.stdin)]; \
  assert '$MARK_ENV' in envs, envs"
SCHEMA=$(api GET /about | json 'd["version"]["schema"]')
[ -z "$REV" ] || [ "$SCHEMA" = "$REV" ] || { echo "schema $SCHEMA != backup revision $REV" >&2; exit 1; }
echo "restore drill passed: user, environment and schema revision ($SCHEMA) restored"
rm -rf "$OUT"
