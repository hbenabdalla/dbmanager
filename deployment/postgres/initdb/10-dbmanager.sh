#!/bin/sh
# Runs once when the application database volume is first initialised.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SQL

# Optional: a least-privileged monitoring role so this PostgreSQL instance can be registered
# in DB Manager as a first real target (MWP walkthrough). Only created when a password is set.
if [ -n "${DBM_SAMPLE_MONITOR_PASSWORD:-}" ]; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
       -v pw="$DBM_SAMPLE_MONITOR_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE dbm_monitor LOGIN PASSWORD %L', :'pw')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dbm_monitor') \gexec
GRANT pg_monitor TO dbm_monitor;
SQL
fi
