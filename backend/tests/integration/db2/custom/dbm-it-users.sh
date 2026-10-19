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

# One run at a time. A run interrupted on the host (Ctrl+C) keeps going inside the container and
# can hold catalog locks; a new run would then wait for them forever (Db2's default LOCKTIMEOUT is
# -1). Stop any earlier run and its CLP back-end processes first.
for pid in $(pgrep -f '/var/custom/dbm-it-users.sh' || true); do
  # not this run, its launcher (timeout) or its own subshells (the tee process substitution)
  [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ] && continue
  [ "$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')" = "$$" ] && continue
  kill "$pid" 2>/dev/null && echo "stopped an earlier run (pid $pid)"
done
pkill -u "$INST" -f db2bp 2>/dev/null && echo "stopped stale CLP back-end processes"
sleep 1

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
  # a statement that waits for a lock gives up after 60 s instead of hanging the run
  su - "$INST" -c "db2 connect to $DB >/dev/null; db2 'SET CURRENT LOCK TIMEOUT 60' >/dev/null; while IFS= read -r s; do [ -n \"\$s\" ] && { db2 -v \"\$s\" || echo \"WARN: failed: \$s\"; }; done; db2 terminate >/dev/null"
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
# (CLP output goes through a file: db2 inside a pipeline runs without the connection, SQL1024N)
su - "$INST" -c "db2 connect to $DB >/dev/null; db2 -x \"SELECT COUNT(*) FROM SYSCAT.TABLES WHERE TABSCHEMA = 'DBMMON' AND TABNAME = 'EXPLAIN_INSTANCE'\" > /tmp/dbm-it-explain.txt; db2 terminate >/dev/null"
have_explain=$(tr -d ' \n' < /tmp/dbm-it-explain.txt 2>/dev/null)
if [ "$have_explain" = "1" ]; then
  echo "explain tables already installed in schema DBMMON"
else
  run_as_owner <<SQL
CALL SYSPROC.SYSINSTALLOBJECTS('EXPLAIN', 'C', CAST(NULL AS VARCHAR(128)), 'DBMMON')
SQL
fi
# The Db2 CLP keeps its connection per shell: db2 commands inside a pipeline or $( ) run in a
# subshell without it (SQL1024N), so results go through a file and the loop reads from it.
su - "$INST" -c "db2 connect to $DB >/dev/null; \
  db2 -x \"SELECT 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE DBMMON.' || TABNAME || ' TO USER DBMMON' FROM SYSCAT.TABLES WHERE TABSCHEMA = 'DBMMON' AND TYPE = 'T'\" > /tmp/dbm-it-grants.sql; \
  while IFS= read -r g; do g=\$(echo \"\$g\" | sed 's/ *\$//'); [ -n \"\$g\" ] && { db2 \"\$g\" >/dev/null || echo \"WARN: failed: \$g\"; }; done < /tmp/dbm-it-grants.sql; \
  db2 -x \"SELECT COUNT(*) FROM SYSCAT.TABAUTH WHERE TABSCHEMA = 'DBMMON' AND GRANTEE = 'DBMMON' AND INSERTAUTH IN ('Y', 'G')\" > /tmp/dbm-it-count.txt; \
  echo \"explain tables writable by DBMMON: \$(tr -d ' ' < /tmp/dbm-it-count.txt)\"; \
  db2 terminate >/dev/null"

# Online backups (DB Manager's Db2 backup provider) need archive logging, as on any production
# database. Switching LOGARCHMETH1 from OFF puts the database in BACKUP PENDING until one offline
# backup is taken, so this disposable test database gets one (to /dev/null) right away.
mkdir -p /database/dbm-it-archive /database/dbm-it-backups
chown "$INST" /database/dbm-it-archive /database/dbm-it-backups
su - "$INST" -c "db2 get db cfg for $DB > /tmp/dbm-it-dbcfg.txt"
if grep -q "LOGARCHMETH1) = OFF" /tmp/dbm-it-dbcfg.txt; then
  echo "enabling archive logging (LOGARCHMETH1 DISK:/database/dbm-it-archive)"
  su - "$INST" -c "db2 update db cfg for $DB using LOGARCHMETH1 DISK:/database/dbm-it-archive/ \
    && db2 force application all; sleep 3; db2 deactivate db $DB; \
    db2 backup db $DB to /dev/null without prompting && db2 activate db $DB" \
    || echo "WARN: could not enable archive logging (Db2 backup tests will report REQUIRES_CONFIGURATION)"
else
  echo "archive logging already enabled"
fi

# Backup operator (least privilege for BACKUP DATABASE): Db2's instance-level SYSMAINT authority
# comes from an operating-system group named in SYSMAINT_GROUP (not SYSADM, not the instance owner).
# Changing SYSMAINT_GROUP needs an instance restart, done once on this disposable container.
if [ -n "${DBM_IT_DB2_BK_PASSWORD:-}" ]; then
  getent group dbmmaint >/dev/null || groupadd dbmmaint
  id dbmbk >/dev/null 2>&1 || useradd -m -s /bin/bash -g dbmmaint dbmbk
  echo "dbmbk:${DBM_IT_DB2_BK_PASSWORD}" | chpasswd
  su - "$INST" -c "db2 get dbm cfg > /tmp/dbm-it-dbmcfg.txt"
  if grep -q "SYSMAINT_GROUP) = DBMMAINT" /tmp/dbm-it-dbmcfg.txt; then
    echo "SYSMAINT_GROUP already DBMMAINT"
  else
    echo "setting SYSMAINT_GROUP = DBMMAINT (instance restart)"
    su - "$INST" -c "db2 update dbm cfg using SYSMAINT_GROUP DBMMAINT && db2 force application all; sleep 3; \
      db2stop force; db2start; db2 activate db $DB" || echo "WARN: could not set SYSMAINT_GROUP"
  fi
  run_as_owner <<SQL
GRANT CONNECT ON DATABASE TO USER DBMBK
GRANT EXECUTE ON PROCEDURE SYSPROC.ADMIN_CMD TO USER DBMBK
GRANT SELECT ON SYSIBMADM.DB_HISTORY TO USER DBMBK
SQL
else
  echo "DBM_IT_DB2_BK_PASSWORD not set: no backup operator account (Db2 backup test skips)"
fi

touch "$DONE"
echo "== done"
