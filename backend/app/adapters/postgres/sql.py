"""PostgreSQL catalog / statistics queries used by the reference adapter.

All queries are static text with bind parameters ($1, $2...). No user input is ever
interpolated into SQL here.
"""

SERVER_INFO = """
SELECT version() AS version_full,
       current_setting('server_version') AS version,
       current_setting('server_version_num')::int AS version_num,
       pg_postmaster_start_time() AS started_at,
       extract(epoch FROM now() - pg_postmaster_start_time()) AS uptime_seconds,
       pg_is_in_recovery() AS is_replica,
       current_user AS current_user,
       current_database() AS current_database,
       (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS is_superuser,
       pg_has_role(current_user, 'pg_monitor', 'USAGE') AS has_pg_monitor,
       pg_has_role(current_user, 'pg_read_all_stats', 'USAGE') AS has_read_all_stats,
       inet_server_addr()::text AS server_addr
"""

CLOUD_HINTS = """
SELECT
  EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rds_superuser') AS aws_rds,
  EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'aurora_version') AS aws_aurora,
  EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'azure_pg_admin') AS azure,
  EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cloudsqlsuperuser') AS gcp_cloudsql,
  EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'alloydbsuperuser') AS gcp_alloydb
"""

EXTENSIONS = "SELECT extname, extversion FROM pg_extension ORDER BY 1"

PGSS_PROBE = "SELECT 1 FROM pg_stat_statements LIMIT 1"

LIST_DATABASES = """
SELECT d.datname AS name,
       pg_get_userbyid(d.datdba) AS owner,
       CASE WHEN has_database_privilege(d.oid, 'CONNECT') OR pg_has_role(current_user, 'pg_read_all_stats', 'USAGE')
            THEN pg_database_size(d.oid) END AS size_bytes,
       pg_encoding_to_char(d.encoding) AS encoding,
       d.datcollate AS collation,
       d.datallowconn AS allow_connections,
       d.datistemplate AS is_template,
       d.datconnlimit AS connection_limit,
       t.spcname AS tablespace,
       has_database_privilege(d.oid, 'CONNECT') AS accessible,
       s.numbackends, s.xact_commit, s.xact_rollback, s.blks_read, s.blks_hit, s.deadlocks,
       s.temp_bytes, s.stats_reset
FROM pg_database d
LEFT JOIN pg_tablespace t ON t.oid = d.dattablespace
LEFT JOIN pg_stat_database s ON s.datid = d.oid
ORDER BY d.datistemplate, d.datname
"""

LIST_SCHEMAS = """
SELECT n.nspname AS name,
       pg_get_userbyid(n.nspowner) AS owner,
       (n.nspname IN ('pg_catalog', 'information_schema', 'pg_toast') OR n.nspname LIKE 'pg\\_temp\\_%'
        OR n.nspname LIKE 'pg\\_toast\\_temp\\_%') AS is_system,
       count(c.oid) FILTER (WHERE c.relkind IN ('r','p')) AS tables,
       count(c.oid) FILTER (WHERE c.relkind = 'v') AS views,
       count(c.oid) FILTER (WHERE c.relkind = 'm') AS materialized_views,
       count(c.oid) FILTER (WHERE c.relkind = 'i') AS indexes,
       count(c.oid) FILTER (WHERE c.relkind = 'S') AS sequences,
       (SELECT count(*) FROM pg_proc p WHERE p.pronamespace = n.oid) AS routines,
       coalesce(sum(pg_total_relation_size(c.oid)) FILTER (WHERE c.relkind IN ('r','p','m')), 0)::bigint AS size_bytes
FROM pg_namespace n
LEFT JOIN pg_class c ON c.relnamespace = n.oid
WHERE n.nspname NOT LIKE 'pg\\_toast%' AND n.nspname NOT LIKE 'pg\\_temp\\_%'
GROUP BY n.oid, n.nspname, n.nspowner
ORDER BY is_system, n.nspname
"""

RELKIND_TYPES = {
    "table": ("r", "p"),
    "view": ("v",),
    "materialized_view": ("m",),
    "index": ("i", "I"),
    "sequence": ("S",),
    "foreign_table": ("f",),
}

LIST_RELATIONS = """
SELECT c.relname AS name, n.nspname AS schema_name,
       CASE c.relkind WHEN 'r' THEN 'table' WHEN 'p' THEN 'table' WHEN 'v' THEN 'view'
            WHEN 'm' THEN 'materialized_view' WHEN 'i' THEN 'index' WHEN 'I' THEN 'index'
            WHEN 'S' THEN 'sequence' WHEN 'f' THEN 'foreign_table' END AS type,
       pg_get_userbyid(c.relowner) AS owner,
       CASE WHEN c.relkind IN ('r','p','m','i','I','S','f') THEN pg_total_relation_size(c.oid) END AS size_bytes,
       CASE WHEN c.relkind IN ('r','p','m') THEN c.reltuples::bigint END AS row_estimate,
       CASE WHEN c.relkind IN ('i','I') THEN (SELECT t.relname FROM pg_index x JOIN pg_class t ON t.oid = x.indrelid
                                             WHERE x.indexrelid = c.oid) END AS parent,
       c.relkind = 'p' AS partitioned,
       c.relispartition AS is_partition,
       obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1 AND c.relkind::text = ANY($2::text[])
ORDER BY c.relname
LIMIT $3
"""

LIST_ROUTINES = """
SELECT p.proname AS name, n.nspname AS schema_name,
       CASE p.prokind WHEN 'p' THEN 'procedure' ELSE 'function' END AS type,
       pg_get_userbyid(p.proowner) AS owner,
       pg_get_function_identity_arguments(p.oid) AS arguments,
       l.lanname AS language,
       p.prosecdef AS security_definer,
       pg_get_function_result(p.oid) AS returns
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_language l ON l.oid = p.prolang
WHERE n.nspname = $1 AND ($2::text IS NULL OR (CASE p.prokind WHEN 'p' THEN 'procedure' ELSE 'function' END) = $2)
  AND p.prokind IN ('f', 'p', 'w')
ORDER BY p.proname
LIMIT $3
"""

LIST_TRIGGERS = """
SELECT t.tgname AS name, n.nspname AS schema_name, 'trigger' AS type, c.relname AS parent,
       pg_get_triggerdef(t.oid) AS definition, t.tgenabled <> 'D' AS enabled
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE NOT t.tgisinternal AND n.nspname = $1
ORDER BY c.relname, t.tgname
LIMIT $2
"""

LIST_CONSTRAINTS = """
SELECT con.conname AS name, n.nspname AS schema_name, 'constraint' AS type, c.relname AS parent,
       CASE con.contype WHEN 'p' THEN 'primary key' WHEN 'f' THEN 'foreign key' WHEN 'u' THEN 'unique'
            WHEN 'c' THEN 'check' WHEN 'x' THEN 'exclusion' ELSE con.contype::text END AS kind,
       pg_get_constraintdef(con.oid) AS definition
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = con.connamespace
WHERE n.nspname = $1
ORDER BY c.relname, con.conname
LIMIT $2
"""

REL_OID = """
SELECT c.oid, c.relkind::text AS relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = $1 AND c.relname = $2
"""

COLUMNS = """
SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS data_type, NOT a.attnotnull AS nullable,
       pg_get_expr(d.adbin, d.adrelid) AS "default", a.attnum AS position, col_description(a.attrelid, a.attnum) AS comment
FROM pg_attribute a
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE a.attrelid = $1 AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""

INDEXES_OF = """
SELECT i.relname AS name, pg_get_indexdef(x.indexrelid) AS definition, x.indisunique AS is_unique,
       x.indisprimary AS is_primary, x.indisvalid AS is_valid, pg_relation_size(x.indexrelid) AS size_bytes,
       s.idx_scan, s.idx_tup_read
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
LEFT JOIN pg_stat_all_indexes s ON s.indexrelid = x.indexrelid
WHERE x.indrelid = $1
ORDER BY i.relname
"""

CONSTRAINTS_OF = """
SELECT conname AS name,
       CASE contype WHEN 'p' THEN 'primary key' WHEN 'f' THEN 'foreign key' WHEN 'u' THEN 'unique'
            WHEN 'c' THEN 'check' WHEN 'x' THEN 'exclusion' ELSE contype::text END AS kind,
       pg_get_constraintdef(oid) AS definition
FROM pg_constraint WHERE conrelid = $1 ORDER BY contype, conname
"""

TRIGGERS_OF = """
SELECT tgname AS name, pg_get_triggerdef(oid) AS definition, tgenabled <> 'D' AS enabled
FROM pg_trigger WHERE tgrelid = $1 AND NOT tgisinternal ORDER BY tgname
"""

TABLE_STATS = """
SELECT seq_scan, seq_tup_read, idx_scan, idx_tup_fetch, n_tup_ins, n_tup_upd, n_tup_del, n_live_tup, n_dead_tup,
       n_mod_since_analyze, last_vacuum, last_autovacuum, last_analyze, last_autoanalyze, vacuum_count,
       autovacuum_count, analyze_count, autoanalyze_count,
       pg_relation_size(relid) AS table_bytes, pg_indexes_size(relid) AS index_bytes,
       pg_total_relation_size(relid) AS total_bytes
FROM pg_stat_all_tables WHERE relid = $1
"""

VIEW_DEF = "SELECT pg_get_viewdef($1::oid, true)"
FUNC_DEF = """
SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = $1 AND p.proname = $2 AND p.prokind IN ('f','p','w') LIMIT 1
"""
SEQ_DEF = """
SELECT data_type, start_value, min_value, max_value, increment_by, cycle, cache_size, last_value
FROM pg_sequences WHERE schemaname = $1 AND sequencename = $2
"""

ROLES = """
SELECT r.rolname AS name, r.rolsuper, r.rolinherit, r.rolcreaterole, r.rolcreatedb, r.rolcanlogin,
       r.rolreplication, r.rolbypassrls, r.rolconnlimit, r.rolvaliduntil,
       ARRAY(SELECT b.rolname FROM pg_auth_members m JOIN pg_roles b ON m.roleid = b.oid
             WHERE m.member = r.oid ORDER BY 1) AS member_of
FROM pg_roles r
ORDER BY r.rolname
"""

PRIVILEGES = """
WITH acl AS (
  SELECT 'database'::text AS object_type, d.datname::text AS object_name, a.*
  FROM pg_database d, aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a
  WHERE d.datname = current_database()
  UNION ALL
  SELECT 'schema', n.nspname::text, a.*
  FROM pg_namespace n, aclexplode(n.nspacl) a
  WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\\_%'
  UNION ALL
  SELECT CASE c.relkind WHEN 'S' THEN 'sequence' WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view'
              WHEN 'f' THEN 'foreign_table' ELSE 'table' END,
         n.nspname || '.' || c.relname, a.*
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
  WHERE c.relkind IN ('r','p','v','m','S','f')
    AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\\_%'
  UNION ALL
  SELECT CASE p.prokind WHEN 'p' THEN 'procedure' ELSE 'function' END,
         n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', a.*
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace, aclexplode(p.proacl) a
  WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\\_%'
  UNION ALL
  SELECT 'column', n.nspname || '.' || c.relname || '.' || att.attname, a.*
  FROM pg_attribute att
  JOIN pg_class c ON c.oid = att.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(att.attacl) a
  WHERE att.attacl IS NOT NULL AND att.attnum > 0 AND NOT att.attisdropped
    AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\\_%'
)
SELECT object_type, object_name,
       CASE WHEN grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee) END AS grantee,
       pg_get_userbyid(grantor) AS grantor, privilege_type AS privilege, is_grantable AS grantable
FROM acl
WHERE grantee <> grantor
  AND ($1::text IS NULL OR (CASE WHEN grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee) END) = $1)
ORDER BY object_type, object_name, grantee, privilege
LIMIT $2
"""

ROLE_MEMBERSHIPS = """
SELECT m.rolname AS grantee, r.rolname AS role, g.rolname AS grantor, am.admin_option
FROM pg_auth_members am
JOIN pg_roles r ON r.oid = am.roleid
JOIN pg_roles m ON m.oid = am.member
LEFT JOIN pg_roles g ON g.oid = am.grantor
WHERE ($1::text IS NULL OR m.rolname = $1)
ORDER BY 1, 2
"""

SESSIONS = """
SELECT a.pid, a.usename, a.datname, a.application_name, a.client_addr::text AS client_addr, a.state,
       a.query, a.query_start, a.backend_start, a.xact_start,
       extract(epoch FROM now() - coalesce(a.query_start, a.backend_start)) AS duration_seconds,
       a.wait_event_type, a.wait_event, pg_blocking_pids(a.pid) AS blocked_by, a.backend_type, s.ssl
FROM pg_stat_activity a
LEFT JOIN pg_stat_ssl s ON s.pid = a.pid
WHERE a.pid <> pg_backend_pid()
ORDER BY (a.state = 'active') DESC, duration_seconds DESC NULLS LAST
LIMIT 2000
"""

LOCKS = """
SELECT l.pid, l.locktype, l.mode, l.granted, d.datname,
       CASE WHEN l.relation IS NOT NULL AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
            THEN l.relation::regclass::text
            WHEN l.relation IS NOT NULL THEN 'oid:' || l.relation::text END AS relation,
       a.usename,
       CASE WHEN NOT l.granted THEN extract(epoch FROM now() - coalesce(l.waitstart, a.state_change)) END AS waiting_seconds,
       pg_blocking_pids(l.pid) AS blocked_by, a.query
FROM pg_locks l
LEFT JOIN pg_stat_activity a ON a.pid = l.pid
LEFT JOIN pg_database d ON d.oid = l.database
WHERE l.pid <> pg_backend_pid() AND l.locktype <> 'virtualxid'
ORDER BY l.granted, waiting_seconds DESC NULLS LAST
LIMIT 2000
"""

# PG < 14 has no pg_locks.waitstart
LOCKS_LEGACY = LOCKS.replace("coalesce(l.waitstart, a.state_change)", "a.state_change")

ACTIVITY_METRICS = """
SELECT count(*) FILTER (WHERE backend_type = 'client backend') AS total,
       count(*) FILTER (WHERE backend_type = 'client backend' AND state = 'active') AS active,
       count(*) FILTER (WHERE backend_type = 'client backend' AND state = 'idle') AS idle,
       count(*) FILTER (WHERE backend_type = 'client backend' AND state LIKE 'idle in transaction%') AS idle_in_txn,
       count(*) FILTER (WHERE backend_type = 'client backend' AND wait_event_type = 'Lock') AS waiting,
       count(*) FILTER (WHERE backend_type = 'client backend' AND cardinality(pg_blocking_pids(pid)) > 0) AS blocked,
       coalesce(max(extract(epoch FROM now() - xact_start)) FILTER (WHERE backend_type = 'client backend'), 0) AS longest_xact,
       coalesce(max(extract(epoch FROM now() - query_start)) FILTER (WHERE backend_type = 'client backend' AND state = 'active'), 0) AS longest_query,
       coalesce(max(extract(epoch FROM now() - state_change)) FILTER (WHERE backend_type = 'client backend' AND state LIKE 'idle in transaction%'), 0) AS longest_idle_in_txn,
       current_setting('max_connections')::int AS max_connections,
       current_setting('superuser_reserved_connections')::int AS reserved_connections
FROM pg_stat_activity
"""

WAIT_EVENTS = """
SELECT coalesce(wait_event_type, 'CPU') AS wait_type, count(*) AS n
FROM pg_stat_activity
WHERE state = 'active' AND backend_type = 'client backend' AND pid <> pg_backend_pid()
GROUP BY 1
"""

DATABASE_COUNTERS = """
SELECT sum(xact_commit)::float8 AS commits, sum(xact_rollback)::float8 AS rollbacks,
       sum(blks_read)::float8 AS blks_read, sum(blks_hit)::float8 AS blks_hit,
       sum(tup_returned)::float8 AS tup_returned, sum(tup_fetched)::float8 AS tup_fetched,
       (sum(tup_inserted) + sum(tup_updated) + sum(tup_deleted))::float8 AS tup_modified,
       sum(deadlocks)::float8 AS deadlocks, sum(temp_files)::float8 AS temp_files,
       sum(temp_bytes)::float8 AS temp_bytes, sum(conflicts)::float8 AS conflicts
FROM pg_stat_database
"""

DATABASE_TOTAL_SIZE = """
SELECT coalesce(sum(pg_database_size(oid)), 0)::float8
FROM pg_database
WHERE datallowconn AND (has_database_privilege(oid, 'CONNECT') OR pg_has_role(current_user, 'pg_read_all_stats', 'USAGE'))
"""

LOCK_COUNTS = "SELECT count(*) AS total, count(*) FILTER (WHERE NOT granted) AS waiting FROM pg_locks"

CHECKPOINTS_17 = "SELECT num_timed::float8 AS timed, num_requested::float8 AS requested FROM pg_stat_checkpointer"
CHECKPOINTS_LEGACY = (
    "SELECT checkpoints_timed::float8 AS timed, checkpoints_req::float8 AS requested FROM pg_stat_bgwriter"
)

REPLICA_LAG = """
SELECT CASE WHEN pg_last_wal_receive_lsn() = pg_last_wal_replay_lsn() THEN 0
            ELSE extract(epoch FROM now() - pg_last_xact_replay_timestamp()) END AS lag
"""
PRIMARY_REPLICATION = """
SELECT count(*) AS replicas, coalesce(max(extract(epoch FROM replay_lag)), 0) AS max_lag
FROM pg_stat_replication
"""

PGSS_TOTALS = "SELECT sum(calls)::float8 AS calls, sum({total})::float8 AS total_ms FROM pg_stat_statements"

PGSS_TOP = """
SELECT s.queryid::text AS query_id, s.query, d.datname, r.rolname, s.calls, s.{total} AS total_ms,
       s.{mean} AS mean_ms, s.{max} AS max_ms, s.{stddev} AS stddev_ms, s.rows,
       s.shared_blks_hit, s.shared_blks_read, s.temp_blks_written, s.temp_blks_read
FROM pg_stat_statements s
LEFT JOIN pg_database d ON d.oid = s.dbid
LEFT JOIN pg_roles r ON r.oid = s.userid
WHERE s.query NOT ILIKE '%pg_stat_statements%'
ORDER BY s.{total} DESC
LIMIT $1
"""

REPLICATION_PRIMARY_DETAIL = """
SELECT application_name, client_addr::text AS client_addr, state, sync_state,
       sent_lsn::text AS sent_lsn, replay_lsn::text AS replay_lsn,
       extract(epoch FROM write_lag) AS write_lag_s, extract(epoch FROM flush_lag) AS flush_lag_s,
       extract(epoch FROM replay_lag) AS replay_lag_s,
       pg_wal_lsn_diff(sent_lsn, replay_lsn)::bigint AS replay_gap_bytes
FROM pg_stat_replication
"""
REPLICATION_SLOTS = """
SELECT slot_name, slot_type, active, database,
       CASE WHEN pg_is_in_recovery() THEN NULL ELSE pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint END AS retained_bytes
FROM pg_replication_slots
"""
WAL_RECEIVER = """
SELECT status, sender_host, sender_port, slot_name, latest_end_time, last_msg_receipt_time
FROM pg_stat_wal_receiver
"""

SETTINGS = """
SELECT name, setting, unit, category, short_desc, source, boot_val, context, pending_restart,
       (setting = boot_val) AS is_default
FROM pg_settings
ORDER BY category, name
"""

TABLESPACES = "SELECT spcname AS name, pg_get_userbyid(spcowner) AS owner, pg_tablespace_size(oid) AS size_bytes FROM pg_tablespace"
TABLESPACES_NOSIZE = (
    "SELECT spcname AS name, pg_get_userbyid(spcowner) AS owner, NULL::bigint AS size_bytes FROM pg_tablespace"
)

TOP_TABLES = """
SELECT n.nspname AS schema, c.relname AS name, pg_total_relation_size(c.oid) AS total_bytes,
       pg_relation_size(c.oid) AS table_bytes, pg_indexes_size(c.oid) AS index_bytes, c.reltuples::bigint AS row_estimate
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r','p','m') AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\\_toast%'
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 25
"""
TOP_INDEXES = """
SELECT n.nspname AS schema, i.relname AS name, t.relname AS table_name, pg_relation_size(i.oid) AS size_bytes,
       s.idx_scan
FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = i.relnamespace
LEFT JOIN pg_stat_all_indexes s ON s.indexrelid = x.indexrelid
WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg\\_toast%'
ORDER BY pg_relation_size(i.oid) DESC
LIMIT 25
"""
TEMP_USAGE = "SELECT coalesce(sum(temp_files),0)::bigint AS temp_files, coalesce(sum(temp_bytes),0)::bigint AS temp_bytes FROM pg_stat_database"

# --- security context -------------------------------------------------------------------
SECURITY_SETTINGS = """
SELECT name, setting FROM pg_settings
WHERE name IN ('ssl', 'password_encryption', 'log_connections', 'log_disconnections', 'log_statement',
               'shared_preload_libraries', 'listen_addresses', 'server_version_num', 'ssl_min_protocol_version',
               'log_min_duration_statement', 'row_security')
"""
MD5_PASSWORDS = "SELECT rolname FROM pg_authid WHERE rolpassword LIKE 'md5%' AND rolcanlogin"
EMPTY_PASSWORD_LOGINS = (
    "SELECT rolname FROM pg_authid WHERE rolcanlogin AND rolpassword IS NULL AND rolname NOT LIKE 'pg\\_%'"
)
HBA_RULES = """
SELECT line_number, type, database, user_name, address, netmask, auth_method, error
FROM pg_hba_file_rules ORDER BY line_number
"""
PUBLIC_SCHEMA_CREATE = """
SELECT EXISTS (
  SELECT 1 FROM pg_namespace n, aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) a
  WHERE n.nspname = 'public' AND a.grantee = 0 AND a.privilege_type = 'CREATE'
)
"""
PUBLIC_DATABASE_PRIVS = """
SELECT d.datname, a.privilege_type
FROM pg_database d, aclexplode(coalesce(d.datacl, acldefault('d', d.datdba))) a
WHERE a.grantee = 0 AND NOT d.datistemplate
"""
SSL_CONNECTIONS = """
SELECT count(*) FILTER (WHERE NOT coalesce(s.ssl, false)) AS non_ssl, count(*) AS total
FROM pg_stat_activity a LEFT JOIN pg_stat_ssl s ON s.pid = a.pid
WHERE a.backend_type = 'client backend' AND a.client_addr IS NOT NULL
"""
DIRECT_TABLE_GRANTS = """
SELECT pg_get_userbyid(a.grantee) AS grantee, count(*) AS grants
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
JOIN pg_roles r ON r.oid = a.grantee
WHERE c.relkind IN ('r','p','v','m') AND r.rolcanlogin AND a.grantee <> c.relowner
  AND n.nspname NOT IN ('pg_catalog','information_schema')
GROUP BY 1 ORDER BY 2 DESC LIMIT 100
"""
SECURITY_DEFINER_FUNCS = """
SELECT n.nspname || '.' || p.proname AS name, pg_get_userbyid(p.proowner) AS owner
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema')
  AND NOT EXISTS (SELECT 1 FROM unnest(coalesce(p.proconfig, '{}')) c WHERE c LIKE 'search_path=%')
LIMIT 100
"""

# --- tuning context ---------------------------------------------------------------------
TUNING_SETTINGS = """
SELECT name, setting, unit FROM pg_settings
WHERE name IN ('shared_buffers', 'work_mem', 'maintenance_work_mem', 'effective_cache_size', 'max_connections',
               'max_wal_size', 'checkpoint_timeout', 'checkpoint_completion_target', 'random_page_cost',
               'autovacuum', 'autovacuum_vacuum_scale_factor', 'autovacuum_max_workers', 'track_io_timing',
               'default_statistics_target', 'huge_pages', 'wal_buffers', 'jit')
"""
SEQ_SCAN_TABLES = """
SELECT schemaname, relname, seq_scan, seq_tup_read, coalesce(idx_scan, 0) AS idx_scan, n_live_tup,
       pg_total_relation_size(relid) AS total_bytes
FROM pg_stat_user_tables
WHERE seq_scan > 0
ORDER BY seq_tup_read DESC
LIMIT 50
"""
DEAD_TUPLE_TABLES = """
SELECT schemaname, relname, n_live_tup, n_dead_tup, last_autovacuum, last_vacuum, autovacuum_count,
       n_mod_since_analyze, last_analyze, last_autoanalyze, pg_total_relation_size(relid) AS total_bytes
FROM pg_stat_user_tables
WHERE n_dead_tup > 0 OR n_mod_since_analyze > 0
ORDER BY n_dead_tup DESC
LIMIT 50
"""
UNUSED_INDEXES = """
SELECT s.schemaname, s.relname AS table_name, s.indexrelname AS index_name, s.idx_scan,
       pg_relation_size(s.indexrelid) AS size_bytes
FROM pg_stat_user_indexes s JOIN pg_index x ON x.indexrelid = s.indexrelid
WHERE s.idx_scan = 0 AND NOT x.indisunique AND NOT x.indisprimary
  AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = s.indexrelid)
ORDER BY pg_relation_size(s.indexrelid) DESC
LIMIT 50
"""
DUPLICATE_INDEXES = """
SELECT n.nspname AS schema, t.relname AS table_name, array_agg(i.relname ORDER BY i.relname) AS indexes,
       sum(pg_relation_size(i.oid))::bigint AS total_bytes
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
WHERE n.nspname NOT IN ('pg_catalog','information_schema')
GROUP BY n.nspname, t.relname, x.indrelid, x.indkey::text, x.indclass::text, coalesce(x.indexprs::text, ''), coalesce(x.indpred::text, '')
HAVING count(*) > 1
LIMIT 50
"""
STATS_RESET = "SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()"
