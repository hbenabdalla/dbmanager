# shellcheck shell=bash
# Read KEY=VALUE lines of a Compose .env file into the environment WITHOUT executing it.
# (Sourcing .env with `.` runs any unquoted value with a space as a command — e.g.
# "DBM_MFA_ISSUER=DB Manager" would run "Manager".) Surrounding single or double quotes are
# removed, as Docker Compose does; comments and blank lines are skipped.
load_dotenv() {
  local file="${1:-.env}" line key value
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    key="${line%%=*}"
    [ "$key" = "$line" ] && continue
    key="${key#export }"
    key="${key//[[:space:]]/}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${line#*=}"
    if [[ "$value" =~ ^\"(.*)\"$ ]] || [[ "$value" =~ ^\'(.*)\'$ ]]; then value="${BASH_REMATCH[1]}"; fi
    export "$key=$value"
  done < "$file"
}
