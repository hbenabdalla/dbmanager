#!/usr/bin/env sh
# Creates .env from .env.example (if needed), adds settings that newer versions introduced
# (e.g. the time-series database password) and replaces every CHANGE_ME value with a
# generated secret. Existing non-placeholder values are kept.
#   ./scripts/init-env.sh
set -eu
cd "$(dirname "$0")/.."

[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example"; }

# append keys that exist in .env.example but not yet in .env (upgrades keep working)
added=""
while IFS= read -r line || [ -n "$line" ]; do
  line=$(printf '%s' "$line" | tr -d '\r')
  case "$line" in
    [A-Z]*=*)
      name=${line%%=*}
      if ! grep -q "^$name=" .env; then
        [ -n "$added" ] || printf '\n# --- added by init-env (new in this version) ---\n' >>.env
        printf '%s\n' "$line" >>.env
        added="$added $name"
      fi
      ;;
  esac
done <.env.example
[ -z "$added" ] || echo "Added new settings to .env:$added"

alnum() {
  while :; do
    s=$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1")
    case "$s" in *[a-z]*) ;; *) continue ;; esac
    case "$s" in *[A-Z]*) ;; *) continue ;; esac
    case "$s" in *[0-9]*) break ;; esac
  done
  printf '%s' "$s"
}

tmp=$(mktemp)
report=$(mktemp)
trap 'rm -f "$tmp" "$report"' EXIT

while IFS= read -r line || [ -n "$line" ]; do
  line=$(printf '%s' "$line" | tr -d '\r')
  case "$line" in
    [A-Z]*=*CHANGE_ME*)
      name=${line%%=*}
      case "$name" in
        DBM_MASTER_KEY) value=$(head -c 32 /dev/urandom | base64 | tr -d '\n') ;;
        POSTGRES_PASSWORD) value=$(alnum 32) ;;
        *) value=$(alnum 20) ;;
      esac
      printf '%s=%s\n' "$name" "$value" >>"$tmp"
      printf '%s=%s\n' "$name" "$value" >>"$report"
      ;;
    *) printf '%s\n' "$line" >>"$tmp" ;;
  esac
done <.env

if [ ! -s "$report" ]; then
  echo ".env has no CHANGE_ME values left; nothing changed."
  exit 0
fi
cat "$tmp" >.env
chmod 600 .env 2>/dev/null || true

echo
echo "Filled in .env: $(cut -d= -f1 "$report" | tr '\n' ' ')"
grep '^DBM_BOOTSTRAP_ADMIN_PASSWORD=' "$report" | sed 's/^[^=]*=/  Sign in as admin with:        /' || true
grep '^DBM_SAMPLE_MONITOR_PASSWORD=' "$report" | sed 's/^[^=]*=/  dbm_monitor target password:  /' || true
echo
echo "Back up DBM_MASTER_KEY separately: stored database credentials cannot be decrypted without it."
if grep -q '^POSTGRES_PASSWORD=' "$report"; then
  echo
  echo "If you already started the stack with the placeholder values, recreate the database volume"
  echo "so the new password applies (this deletes DBManager's own data):  docker compose down -v"
fi
echo "Then start:  docker compose up --build"
