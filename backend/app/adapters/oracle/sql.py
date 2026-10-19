"""SQL used by the Oracle adapter (Oracle Database 12.2, 18c, 19c, 21c, 23ai).

Licensing boundary — read before adding a query
------------------------------------------------
Only dictionary views that are part of every Oracle Database edition without an extra
management pack are used here: ``V$INSTANCE``, ``V$DATABASE``, ``V$SESSION``, ``V$LOCK``,
``V$TRANSACTION``, ``V$SYSSTAT``, ``V$OSSTAT``, ``V$SYSTEM_WAIT_CLASS``, ``V$SYSTEM_EVENT``,
``V$SGA``, ``V$PGASTAT``, ``V$RESOURCE_LIMIT``, ``V$SQLSTATS``, ``V$SQL_PLAN_STATISTICS_ALL``,
``V$PARAMETER``, ``V$PDBS``, ``V$DATAGUARD_STATS``, ``V$ARCHIVE_DEST_STATUS``,
``V$RECOVERY_FILE_DEST``, ``V$LOG``, ``V$LOG_HISTORY`` and the ``DBA_``/``CDB_`` catalog.

Never add (without a licensing flag checked in code): ``V$ACTIVE_SESSION_HISTORY``,
``DBA_HIST_*`` (AWR), ``DBA_ADDM_*``, ``DBMS_WORKLOAD_REPOSITORY``, ``DBMS_SQLTUNE``,
``DBMS_ADVISOR`` (tuning tasks), SQL Monitoring (``V$SQL_MONITOR``) or ``DBMS_XPLAN.DISPLAY_AWR``:
they belong to the Diagnostics Pack / Tuning Pack. Metric views (``V$SYSMETRIC``…) are not used
either, so no feature-usage record can be attributed to this tool.

Binds use the oracledb named style (``:owner``).
"""

SERVER_INFO = """
SELECT i.instance_name, i.host_name, i.version, i.startup_time, i.status AS instance_status,
       i.database_status, i.instance_role, i.logins, i.parallel,
       d.name AS db_name, d.db_unique_name, d.dbid, d.log_mode, d.open_mode, d.database_role, d.cdb,
       d.platform_name, d.force_logging, d.flashback_on, d.protection_mode, d.created,
       SYS_CONTEXT('USERENV', 'CON_NAME') AS con_name, SYS_CONTEXT('USERENV', 'CON_ID') AS con_id,
       SYS_CONTEXT('USERENV', 'SESSION_USER') AS session_user,
       SYS_CONTEXT('USERENV', 'NETWORK_PROTOCOL') AS network_protocol,
       SYS_CONTEXT('USERENV', 'SERVICE_NAME') AS service_name,
       SYS_CONTEXT('USERENV', 'SID') AS sid
FROM v$instance i CROSS JOIN v$database d
"""
#: fallback when the account cannot read V$ views (PUBLIC can read these)
BASIC_INFO = """
SELECT (SELECT version FROM product_component_version WHERE product LIKE 'Oracle%' FETCH FIRST 1 ROWS ONLY) AS version,
       SYS_CONTEXT('USERENV', 'DB_NAME') AS db_name, SYS_CONTEXT('USERENV', 'CON_NAME') AS con_name,
       SYS_CONTEXT('USERENV', 'CON_ID') AS con_id, SYS_CONTEXT('USERENV', 'SESSION_USER') AS session_user,
       SYS_CONTEXT('USERENV', 'NETWORK_PROTOCOL') AS network_protocol,
       SYS_CONTEXT('USERENV', 'SERVICE_NAME') AS service_name, SYS_CONTEXT('USERENV', 'SERVER_HOST') AS host_name,
       SYS_CONTEXT('USERENV', 'SID') AS sid
FROM dual
"""
VERSION_FULL = "SELECT version_full FROM v$instance"  # 18c+
BANNER = "SELECT banner FROM v$version WHERE banner LIKE 'Oracle%'"
SESSION_PRIVS = "SELECT privilege FROM session_privs"
SESSION_ROLES = "SELECT role FROM session_roles"
CPU_COUNT = "SELECT stat_name, value FROM v$osstat WHERE stat_name IN ('NUM_CPUS', 'PHYSICAL_MEMORY_BYTES', 'BUSY_TIME', 'IDLE_TIME')"

# --- inventory -------------------------------------------------------------------------------
PDBS = """
SELECT p.con_id, p.name, p.open_mode, p.restricted, p.open_time, p.total_size
FROM v$pdbs p
ORDER BY p.con_id
"""
DATAFILE_BYTES = "SELECT SUM(bytes) AS bytes FROM dba_data_files"
TEMPFILE_BYTES = "SELECT SUM(bytes) AS bytes FROM dba_temp_files"
CDB_DATAFILE_BYTES = "SELECT con_id, SUM(bytes) AS bytes FROM cdb_data_files GROUP BY con_id"

#: ``{v}`` = dba | cdb; ``{con}`` = "" or "AND x.con_id = :con_id" (CDB root reading a PDB)
USERS_FOR_SCHEMAS = """
SELECT u.username, u.oracle_maintained, u.created, u.account_status
FROM {v}_users u
WHERE 1 = 1 {con_u}
ORDER BY u.username
"""
OBJECT_COUNTS = """
SELECT o.owner, o.object_type, COUNT(*) AS n
FROM {v}_objects o
WHERE o.object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW', 'INDEX', 'SEQUENCE', 'PROCEDURE', 'FUNCTION',
                        'PACKAGE', 'TRIGGER', 'SYNONYM', 'TYPE')
  AND o.subobject_name IS NULL {con_o}
GROUP BY o.owner, o.object_type
"""
SCHEMA_SIZES = """
SELECT s.owner, SUM(s.bytes) AS bytes
FROM {v}_segments s
WHERE 1 = 1 {con_s}
GROUP BY s.owner
"""
OBJECTS = """
SELECT o.object_name, o.object_type, o.status, o.created, o.last_ddl_time, o.generated,
       t.num_rows, t.last_analyzed, t.partitioned, t.temporary, t.iot_type,
       (SELECT SUM(s.bytes) FROM {v}_segments s WHERE s.owner = o.owner AND s.segment_name = o.object_name
          AND s.segment_type LIKE 'TABLE%' {con_s_o}) AS bytes,
       (SELECT tr.table_name FROM {v}_triggers tr WHERE tr.owner = o.owner AND tr.trigger_name = o.object_name
          {con_tr_o}) AS trigger_table
FROM {v}_objects o
LEFT JOIN {v}_tables t ON t.owner = o.owner AND t.table_name = o.object_name AND o.object_type = 'TABLE' {con_t_o}
WHERE o.owner = :owner AND o.subobject_name IS NULL AND o.object_type IN ({types}) {con_o}
ORDER BY o.object_type, o.object_name
"""
INDEXES = """
SELECT i.index_name, i.table_name, i.uniqueness, i.index_type, i.status, i.visibility, i.partitioned,
       i.num_rows, i.last_analyzed,
       (SELECT LISTAGG(c.column_name, ', ') WITHIN GROUP (ORDER BY c.column_position)
          FROM {v}_ind_columns c WHERE c.index_owner = i.owner AND c.index_name = i.index_name {con_c_i}) AS columns
FROM {v}_indexes i
WHERE i.owner = :owner {table_filter} {con_i}
ORDER BY i.table_name, i.index_name
"""
COLUMNS = """
SELECT column_name, data_type, data_length, data_precision, data_scale, char_length, char_used, nullable,
       data_default, column_id
FROM dba_tab_columns
WHERE owner = :owner AND table_name = :name
ORDER BY column_id
"""
CONSTRAINTS = """
SELECT constraint_name, constraint_type, status, validated, r_owner, r_constraint_name, delete_rule, index_name,
       (SELECT LISTAGG(cc.column_name, ', ') WITHIN GROUP (ORDER BY cc.position)
          FROM dba_cons_columns cc WHERE cc.owner = c.owner AND cc.constraint_name = c.constraint_name) AS columns
FROM dba_constraints c
WHERE c.owner = :owner AND c.table_name = :name AND c.constraint_type IN ('P', 'U', 'R', 'C')
  AND c.constraint_name NOT LIKE 'BIN$%'
"""
TRIGGERS_OF = """
SELECT trigger_name, trigger_type, triggering_event, status
FROM dba_triggers WHERE table_owner = :owner AND table_name = :name
"""
OBJECT_ROW = "SELECT object_type, status FROM dba_objects WHERE owner = :owner AND object_name = :name AND subobject_name IS NULL"
GET_DDL = "SELECT DBMS_METADATA.GET_DDL(:otype, :name, :owner) AS ddl FROM dual"
TABLE_STATS = """
SELECT num_rows, blocks, avg_row_len, last_analyzed, stale_stats, sample_size
FROM dba_tab_statistics WHERE owner = :owner AND table_name = :name AND object_type = 'TABLE'
"""

# --- principals ------------------------------------------------------------------------------
USERS = """
SELECT username, account_status, lock_date, expiry_date, default_tablespace, temporary_tablespace, profile,
       created, authentication_type, oracle_maintained, common, last_login, password_versions
FROM dba_users
ORDER BY username
"""
ROLE_PRIVS = "SELECT grantee, granted_role, admin_option, default_role FROM dba_role_privs"
SYS_PRIVS = "SELECT grantee, privilege, admin_option FROM dba_sys_privs"
ROLES = "SELECT role, authentication_type, common, oracle_maintained FROM dba_roles ORDER BY role"
PWFILE_USERS = "SELECT username, sysdba, sysoper, sysasm, sysbackup, sysdg, syskm FROM v$pwfile_users"
TAB_PRIVS = """
SELECT grantee, owner, table_name, grantor, privilege, grantable, type
FROM dba_tab_privs
WHERE grantee NOT IN (SELECT username FROM dba_users WHERE oracle_maintained = 'Y')
  AND grantee NOT IN (SELECT role FROM dba_roles WHERE oracle_maintained = 'Y')
  -- grants on Oracle's own objects (tens of thousands to PUBLIC) would crowd out application grants
  AND owner NOT IN (SELECT username FROM dba_users WHERE oracle_maintained = 'Y')
FETCH FIRST :lim ROWS ONLY
"""
COL_PRIVS = """
SELECT grantee, owner, table_name, column_name, grantor, privilege, grantable
FROM dba_col_privs
WHERE grantee NOT IN (SELECT username FROM dba_users WHERE oracle_maintained = 'Y')
  AND grantee NOT IN (SELECT role FROM dba_roles WHERE oracle_maintained = 'Y')
  -- grants on Oracle's own objects (tens of thousands to PUBLIC) would crowd out application grants
  AND owner NOT IN (SELECT username FROM dba_users WHERE oracle_maintained = 'Y')
FETCH FIRST :lim ROWS ONLY
"""
DIRECT_TABLE_GRANTS = """
SELECT p.grantee, COUNT(*) AS grants
FROM dba_tab_privs p
JOIN dba_users u ON u.username = p.grantee AND u.oracle_maintained = 'N'
WHERE p.type IN ('TABLE', 'VIEW')
GROUP BY p.grantee
HAVING COUNT(*) > 25
"""

# --- sessions & locks ------------------------------------------------------------------------
SESSIONS = """
SELECT s.sid, s.serial#, s.username, s.status, s.osuser, s.machine, s.program, s.module, s.type, s.sql_id,
       s.sql_exec_start, s.logon_time, s.last_call_et, s.blocking_session, s.final_blocking_session,
       s.event, s.wait_class, s.state, s.wait_time_micro, s.service_name, s.con_id, t.start_date AS xact_start,
       (SELECT q.sql_text FROM v$sqlstats q WHERE q.sql_id = s.sql_id FETCH FIRST 1 ROWS ONLY) AS sql_text
FROM v$session s
LEFT JOIN v$transaction t ON t.addr = s.taddr
WHERE s.sid <> TO_NUMBER(SYS_CONTEXT('USERENV', 'SID'))
ORDER BY s.type DESC, s.status, s.sid
FETCH FIRST 2000 ROWS ONLY
"""
SESSION_COUNTS = """
SELECT COUNT(*) AS total,
       SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) AS active,
       SUM(CASE WHEN status = 'INACTIVE' AND taddr IS NOT NULL THEN 1 ELSE 0 END) AS idle_in_txn,
       SUM(CASE WHEN blocking_session IS NOT NULL THEN 1 ELSE 0 END) AS blocked,
       MAX(CASE WHEN status = 'ACTIVE' THEN last_call_et END) AS longest_call_s
FROM v$session WHERE type = 'USER'
"""
LONGEST_TXN = """
SELECT MAX((SYSDATE - t.start_date) * 86400) AS seconds FROM v$transaction t
"""
LOCKS = """
SELECT l.sid, s.serial#, s.username, l.type, l.lmode, l.request, l.ctime, l.block, l.id1, l.id2,
       o.owner, o.object_name, s.blocking_session, s.sql_id
FROM v$lock l
JOIN v$session s ON s.sid = l.sid
LEFT JOIN dba_objects o ON l.type = 'TM' AND o.object_id = l.id1
WHERE l.type IN ('TM', 'TX', 'UL') AND s.type = 'USER'
FETCH FIRST 2000 ROWS ONLY
"""
WAITING_BY_CLASS = """
SELECT wait_class, COUNT(*) AS n
FROM v$session
WHERE status = 'ACTIVE' AND type = 'USER' AND wait_class <> 'Idle' AND state = 'WAITING'
GROUP BY wait_class
"""

# --- metrics ---------------------------------------------------------------------------------
SYSSTAT = """
SELECT name, value FROM v$sysstat WHERE name IN (
  'user commits', 'user rollbacks', 'execute count', 'DB time', 'db block gets', 'consistent gets',
  'physical reads', 'physical writes', 'parse count (total)', 'parse count (hard)', 'redo size', 'user calls',
  'sorts (disk)', 'sorts (memory)', 'table scans (long tables)', 'logons cumulative', 'enqueue deadlocks',
  'physical read total bytes', 'physical write total bytes', 'session logical reads',
  'bytes sent via SQL*Net to client', 'bytes received via SQL*Net from client')
"""
RESOURCE_LIMIT = """
SELECT resource_name, current_utilization, max_utilization, limit_value
FROM v$resource_limit WHERE resource_name IN ('sessions', 'processes')
"""
SGA = "SELECT SUM(value) AS bytes FROM v$sga"
PGASTAT = """
SELECT name, value FROM v$pgastat
WHERE name IN ('total PGA allocated', 'aggregate PGA target parameter', 'over allocation count', 'cache hit percentage')
"""
WAIT_CLASSES = """
SELECT wait_class, time_waited, total_waits,
       100 * time_waited / NULLIF(SUM(time_waited) OVER (), 0) AS pct
FROM v$system_wait_class
WHERE wait_class <> 'Idle'
ORDER BY time_waited DESC
"""
TOP_EVENTS = """
SELECT event, wait_class, total_waits, time_waited_micro
FROM v$system_event
WHERE wait_class <> 'Idle'
ORDER BY time_waited_micro DESC
FETCH FIRST 10 ROWS ONLY
"""
DATAGUARD_STATS = "SELECT name, value, unit, time_computed FROM v$dataguard_stats WHERE name IN ('transport lag', 'apply lag', 'apply finish time')"
ARCHIVE_DESTS = """
SELECT dest_id, dest_name, status, type, database_mode, recovery_mode, destination, gap_status, error
FROM v$archive_dest_status
WHERE status <> 'INACTIVE' AND type <> 'LOCAL'
"""
FRA = "SELECT name, space_limit, space_used, space_reclaimable, number_of_files FROM v$recovery_file_dest"
LOG_SWITCHES_24H = "SELECT COUNT(*) AS n FROM v$log_history WHERE first_time > SYSDATE - 1"
REDO_LOGS = "SELECT group#, thread#, bytes, members, status FROM v$log"

# --- query statistics (plan cache) -----------------------------------------------------------
QUERY_STATS = """
SELECT s.sql_id, MIN(s.sql_text) AS sql_text, s.con_id,
       SUM(s.executions) AS executions, SUM(s.elapsed_time) / 1000 AS total_ms, SUM(s.cpu_time) / 1000 AS cpu_ms,
       SUM(s.buffer_gets) AS buffer_gets, SUM(s.disk_reads) AS disk_reads, SUM(s.rows_processed) AS rows_processed,
       SUM(s.application_wait_time) / 1000 AS app_wait_ms, SUM(s.concurrency_wait_time) / 1000 AS concurrency_wait_ms,
       SUM(s.user_io_wait_time) / 1000 AS io_wait_ms, SUM(s.direct_writes) AS direct_writes,
       COUNT(DISTINCT s.plan_hash_value) AS plans,
       MAX(s.plan_hash_value) KEEP (DENSE_RANK LAST ORDER BY s.elapsed_time) AS top_plan,
       MAX(s.last_active_time) AS last_active_time
FROM v$sqlstats s
WHERE s.executions > 0
GROUP BY s.sql_id, s.con_id
ORDER BY SUM(s.elapsed_time) DESC
FETCH FIRST :lim ROWS ONLY
"""
CONTAINERS = "SELECT con_id, name FROM v$containers"

# --- explain ---------------------------------------------------------------------------------
PLAN_ROWS = """
SELECT id, parent_id, operation, options, object_owner, object_name, object_type, cost, cardinality, bytes,
       cpu_cost, io_cost, access_predicates, filter_predicates, partition_start, partition_stop, depth
FROM plan_table WHERE statement_id = :sid ORDER BY id
"""
PREV_CURSOR = (
    "SELECT prev_sql_id, prev_child_number FROM v$session WHERE sid = TO_NUMBER(SYS_CONTEXT('USERENV', 'SID'))"
)
PLAN_STATS = """
SELECT id, parent_id, operation, options, object_owner, object_name, object_type, cost, cardinality,
       access_predicates, filter_predicates, last_starts, last_output_rows, last_elapsed_time,
       last_cr_buffer_gets, last_disk_reads, last_tempseg_size, depth
FROM v$sql_plan_statistics_all
WHERE sql_id = :sql_id AND child_number = :child
ORDER BY id
"""

# --- configuration ---------------------------------------------------------------------------
PARAMETERS = """
SELECT name, value, display_value, isdefault, ismodified, issys_modifiable, type, description
FROM v$parameter
ORDER BY name
"""
PARAMETER_VALUES = "SELECT name, value FROM v$parameter WHERE name IN ({names})"
SPFILE_PENDING = """
SELECT p.name FROM v$parameter p JOIN v$spparameter sp ON sp.name = p.name AND sp.sid IN ('*', SYS_CONTEXT('USERENV', 'INSTANCE_NAME'))
WHERE sp.isspecified = 'TRUE' AND NVL(sp.value, '~') <> NVL(p.value, '~') AND p.issys_modifiable = 'FALSE'
"""

# --- storage ---------------------------------------------------------------------------------
TABLESPACES = """
SELECT m.tablespace_name AS name, t.contents, t.status, t.bigfile, t.encrypted, t.block_size,
       m.used_space * t.block_size AS used_bytes, m.tablespace_size * t.block_size AS max_bytes,
       m.used_percent,
       NVL((SELECT MAX(CASE WHEN f.autoextensible = 'YES' THEN 1 ELSE 0 END) FROM dba_data_files f
            WHERE f.tablespace_name = m.tablespace_name),
           (SELECT MAX(CASE WHEN f.autoextensible = 'YES' THEN 1 ELSE 0 END) FROM dba_temp_files f
            WHERE f.tablespace_name = m.tablespace_name)) AS autoextend,
       NVL((SELECT SUM(f.bytes) FROM dba_data_files f WHERE f.tablespace_name = m.tablespace_name),
           (SELECT SUM(f.bytes) FROM dba_temp_files f WHERE f.tablespace_name = m.tablespace_name)) AS allocated_bytes
FROM dba_tablespace_usage_metrics m
JOIN dba_tablespaces t ON t.tablespace_name = m.tablespace_name
ORDER BY m.used_percent DESC
"""
TOP_SEGMENTS = """
SELECT s.owner, s.segment_name, s.segment_type, s.tablespace_name, SUM(s.bytes) AS bytes
FROM dba_segments s
WHERE s.owner NOT IN (SELECT username FROM dba_users WHERE oracle_maintained = 'Y')
GROUP BY s.owner, s.segment_name, s.segment_type, s.tablespace_name
ORDER BY 5 DESC
FETCH FIRST 20 ROWS ONLY
"""
TEMP_USAGE = """
SELECT SUM(u.blocks * t.block_size) AS bytes
FROM v$tempseg_usage u JOIN dba_tablespaces t ON t.tablespace_name = u.tablespace
"""

# --- security evidence -----------------------------------------------------------------------
DEFAULT_PWD_USERS = "SELECT username FROM dba_users_with_defpwd"
OPEN_MAINTAINED = """
SELECT username, account_status FROM dba_users
WHERE oracle_maintained = 'Y' AND account_status = 'OPEN' AND username NOT IN ('SYS', 'SYSTEM')
"""
SAMPLE_SCHEMAS_OPEN = """
SELECT username FROM dba_users
WHERE username IN ('SCOTT', 'HR', 'OE', 'SH', 'PM', 'IX', 'BI', 'CO') AND account_status = 'OPEN'
"""
PUBLIC_EXECUTE = """
SELECT table_name FROM dba_tab_privs
WHERE grantee = 'PUBLIC' AND privilege = 'EXECUTE' AND owner = 'SYS'
  AND table_name IN ('UTL_FILE', 'UTL_TCP', 'UTL_HTTP', 'UTL_SMTP', 'UTL_INADDR', 'UTL_MAIL', 'DBMS_LDAP',
                     'HTTPURITYPE', 'DBMS_JAVA', 'DBMS_BACKUP_RESTORE')
ORDER BY table_name
"""
UNIFIED_PURE = "SELECT value FROM v$option WHERE parameter = 'Unified Auditing'"
UNIFIED_POLICIES = "SELECT DISTINCT policy_name FROM audit_unified_enabled_policies"
PASSWORD_LIFE = """
SELECT profile, limit FROM dba_profiles WHERE resource_name = 'PASSWORD_LIFE_TIME'
"""
FAILED_LOGIN = """
SELECT profile, limit FROM dba_profiles WHERE resource_name = 'FAILED_LOGIN_ATTEMPTS'
"""
PROFILE_USERS = """
SELECT profile, COUNT(*) AS n FROM dba_users
WHERE oracle_maintained = 'N' AND account_status = 'OPEN' AND authentication_type = 'PASSWORD'
GROUP BY profile
"""
DBA_GRANTEES = """
SELECT p.grantee FROM dba_role_privs p
WHERE p.granted_role = 'DBA'
  AND p.grantee NOT IN (SELECT username FROM dba_users WHERE oracle_maintained = 'Y')
  AND p.grantee NOT IN (SELECT role FROM dba_roles WHERE oracle_maintained = 'Y')
"""
ANY_PRIV_GRANTEES = """
SELECT p.grantee, p.privilege FROM dba_sys_privs p
WHERE (p.privilege LIKE '% ANY %' OR p.privilege IN ('GRANT ANY PRIVILEGE', 'GRANT ANY ROLE', 'ALTER SYSTEM',
                                                     'BECOME USER', 'EXEMPT ACCESS POLICY', 'EXEMPT REDACTION POLICY'))
  AND p.grantee NOT IN (SELECT username FROM dba_users WHERE oracle_maintained = 'Y')
  AND p.grantee NOT IN (SELECT role FROM dba_roles WHERE oracle_maintained = 'Y')
ORDER BY p.grantee, p.privilege
"""
WEAK_PASSWORD_VERSIONS = """
SELECT username, password_versions FROM dba_users
WHERE oracle_maintained = 'N' AND authentication_type = 'PASSWORD' AND account_status = 'OPEN'
  AND password_versions IS NOT NULL AND password_versions NOT LIKE '%11G%' AND password_versions NOT LIKE '%12C%'
"""
PUBLIC_SYS_PRIVS = "SELECT privilege FROM dba_sys_privs WHERE grantee = 'PUBLIC'"
TDE_TABLESPACES = "SELECT tablespace_name, encrypted FROM dba_tablespaces WHERE contents = 'PERMANENT'"

# --- tuning evidence -------------------------------------------------------------------------
STALE_STATS = """
SELECT s.owner, s.table_name AS "table", s.last_analyzed, s.stale_stats, s.num_rows,
       NVL(ROUND(SYSDATE - s.last_analyzed), 99999) AS last_analyzed_days
FROM dba_tab_statistics s
JOIN dba_users u ON u.username = s.owner AND u.oracle_maintained = 'N'
WHERE s.object_type = 'TABLE' AND (s.stale_stats = 'YES' OR s.last_analyzed IS NULL)
ORDER BY s.num_rows DESC NULLS LAST
FETCH FIRST 50 ROWS ONLY
"""
INVALID_OBJECTS = """
SELECT o.owner, o.object_type, COUNT(*) AS n
FROM dba_objects o JOIN dba_users u ON u.username = o.owner AND u.oracle_maintained = 'N'
WHERE o.status = 'INVALID'
GROUP BY o.owner, o.object_type
"""
UNUSABLE_INDEXES = """
SELECT i.owner, i.index_name, i.table_name FROM dba_indexes i
JOIN dba_users u ON u.username = i.owner AND u.oracle_maintained = 'N'
WHERE i.status = 'UNUSABLE'
FETCH FIRST 50 ROWS ONLY
"""
