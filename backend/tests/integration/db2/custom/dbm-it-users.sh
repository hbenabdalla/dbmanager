#!/bin/bash
# DB Manager integration tests: least-privilege identities for Db2 LUW (icr.io/db2_community/db2).
#
# Db2 authenticates outside the database, so the monitoring, administration and read-only
# identities are operating-system users of the container. Grants follow
# docs/database-engines/db2-luw.md. Idempotent: the IBM image runs /var/custom scripts after
# creating the database, and scripts/integration-test runs it again explicitly (as root) once the
# container is healthy, so it works whichever of the two happens first.
set -uo pipefail
LOG=/database/dbm-it-users.log
DONE=/database/dbm-it-users.done
DB=${DBNAME:-TESTDB}
INST=${DB2INSTANCE:-db2inst1}
exec > >(tee -a "$LOG") 2>&1
echo "== $(date -u +%FT%TZ) dbm-it-users"

declare -A PW=(
  [dbmmon]="${DBM_IT_DB2_MON_PASSWORD:?}"
  [dbmadm]="${DBM_IT_DB2_ADM_PASSWORD:?}"
  [dbmro]="${DBM_IT_DB2_RO_PASSWORD:?}"
)
for u in dbmmon dbmadm dbmro; do
  id "$u" >/dev/null 2>&1 || useradd -m -s /bin/bash "$u"
  echo "$u:${PW[$u]}" | chpasswd
done

# wait until the database accepts connections (first start creates it)
for _ in $(seq 1 120); do
  su - "$INST" -c "db2 connect to $DB >/dev/null 2>&1 && db2 terminate >/dev/null" && break
  sleep 10
done

run_as_owner() {
  # each statement on its own line; a failed statement is reported, not fatal (the tests then show
  # exactly which privilege is missing)
  su - "$INST" -c "db2 connect to $DB >/dev/null; while IFS= read -r s; do [ -n \"\$s\" ] && { db2 -v \"\$s\" || echo \"WARN: failed: \$s\"; }; done; db2 terminate >/dev/null"
}

run_as_owner <<SQL
CREATE SCHEMA SHOP
GRANT CONNECT ON DATABASE TO USER DBMMON
GRANT SQLADM ON DATABASE TO USER DBMMON
GRANT EXPLAIN ON DATABASE TO USER DBMMON
GRANT EXECUTE ON FUNCTION SYSPROC.ENV_GET_SYSTEM_RESOURCES TO USER DBMMON
GRANT EXECUTE ON FUNCTION SYSPROC.AUTH_LIST_AUTHORITIES_FOR_AUTHID TO USER DBMMON
GRANT SELECT ON SYSIBMADM.DBCFG TO USER DBMMON
GRANT SELECT ON SYSIBMADM.DBMCFG TO USER DBMMON
GRANT CONNECT ON DATABASE TO USER DBMADM
GRANT ACCESSCTRL ON DATABASE TO USER DBMADM
GRANT WLMADM ON DATABASE TO USER DBMADM
GRANT SQLADM ON DATABASE TO USER DBMADM
GRANT CREATEIN, ALTERIN ON SCHEMA SHOP TO USER DBMADM
GRANT CONNECT ON DATABASE TO USER DBMRO
GRANT SELECTIN ON SCHEMA SHOP TO USER DBMRO
SQL

# explain tables in the monitoring user's own schema (EXPLAIN writes to CURRENT USER's tables).
# Created by the instance owner, then the monitoring user gets DML on them. "Already exists"
# (SQL0601N) on a re-run is expected.
run_as_owner <<SQL
CALL SYSPROC.SYSINSTALLOBJECTS('EXPLAIN', 'C', CAST(NULL AS VARCHAR(128)), 'DBMMON')
SQL
# The Db2 CLP keeps its connection per shell: db2 commands inside a pipeline or $( ) run in a
# subshell without it (SQL1024N), so results go through a file and the loop reads from it.
su - "$INST" -c "db2 connect to $DB >/dev/null; \
  db2 -x \"SELECT 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE DBMMON.' || TABNAME || ' TO USER DBMMON' FROM SYSCAT.TABLES WHERE TABSCHEMA = 'DBMMON' AND TYPE = 'T'\" > /tmp/dbm-it-grants.sql; \
  while IFS= read -r g; do g=\$(echo \"\$g\" | sed 's/ *\$//'); [ -n \"\$g\" ] && { db2 \"\$g\" >/dev/null || echo \"WARN: failed: \$g\"; }; done < /tmp/dbm-it-grants.sql; \
  db2 -x \"SELECT COUNT(*) FROM SYSCAT.TABAUTH WHERE TABSCHEMA = 'DBMMON' AND GRANTEE = 'DBMMON' AND INSERTAUTH IN ('Y', 'G')\" > /tmp/dbm-it-count.txt; \
  echo \"explain tables writable by DBMMON: \$(tr -d ' ' < /tmp/dbm-it-count.txt)\"; \
  db2 terminate >/dev/null"

touch "$DONE"
echo "== done"
