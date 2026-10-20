"""Catalog and monitoring SQL shared by MySQL 8.x and MariaDB 10.x/11.x.

Statements here only use objects present in both servers (information_schema,
performance_schema tables that MariaDB also implements, SHOW commands). Engine-specific
statements live in :class:`app.adapters.mysql.adapter.MySQLAdapter` and
:class:`app.adapters.mysql.mariadb.MariaDBAdapter`. Placeholders use the PyMySQL ``%s`` style.
"""

SYSTEM_SCHEMAS = ("mysql", "information_schema", "performance_schema", "sys")

SERVER_INFO = """
SELECT VERSION() AS version, @@version_comment AS version_comment, @@hostname AS hostname, @@port AS port,
       CURRENT_USER() AS current_account, @@read_only AS read_only, @@character_set_server AS charset,
       @@collation_server AS collation_name, @@server_id AS server_id, @@performance_schema AS performance_schema,
       @@version_compile_os AS os, @@version_compile_machine AS machine, @@max_connections AS max_connections,
       DATABASE() AS current_db
"""

STATUS = "SHOW GLOBAL STATUS"
VARIABLES_IN = "SHOW GLOBAL VARIABLES WHERE Variable_name IN ({names})"
ALL_VARIABLES = "SHOW GLOBAL VARIABLES"

# --- inventory ---------------------------------------------------------------------------
LIST_DATABASES = """
SELECT s.SCHEMA_NAME AS name, s.DEFAULT_CHARACTER_SET_NAME AS charset, s.DEFAULT_COLLATION_NAME AS collation_name,
       COALESCE(SUM(t.DATA_LENGTH + t.INDEX_LENGTH), 0) AS size_bytes, COUNT(t.TABLE_NAME) AS tables
FROM information_schema.SCHEMATA s
LEFT JOIN information_schema.TABLES t ON t.TABLE_SCHEMA = s.SCHEMA_NAME
GROUP BY s.SCHEMA_NAME, s.DEFAULT_CHARACTER_SET_NAME, s.DEFAULT_COLLATION_NAME
ORDER BY s.SCHEMA_NAME
"""

SCHEMA_COUNTS = """
SELECT
  (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ('BASE TABLE', 'SYSTEM VERSIONED')) AS tables,
  (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'VIEW') AS views,
  (SELECT COUNT(DISTINCT TABLE_NAME, INDEX_NAME) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = %s) AS indexes,
  (SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = %s AND ROUTINE_TYPE = 'PROCEDURE') AS procedures,
  (SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = %s AND ROUTINE_TYPE = 'FUNCTION') AS functions,
  (SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = %s) AS triggers,
  (SELECT COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0) FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s) AS size_bytes
"""

TABLES = """
SELECT TABLE_NAME AS name, TABLE_TYPE AS table_type, ENGINE AS engine, TABLE_ROWS AS row_estimate,
       DATA_LENGTH AS data_bytes, INDEX_LENGTH AS index_bytes, DATA_FREE AS free_bytes, TABLE_COLLATION AS collation_name,
       CREATE_TIME AS created, UPDATE_TIME AS updated, TABLE_COMMENT AS comment_text, ROW_FORMAT AS row_format
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = %s AND TABLE_TYPE IN ({types})
ORDER BY TABLE_NAME
"""

INDEXES = """
SELECT TABLE_NAME AS table_name, INDEX_NAME AS name, MIN(NON_UNIQUE) AS non_unique, MIN(INDEX_TYPE) AS index_type,
       GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ', ') AS columns_list, MAX(CARDINALITY) AS cardinality
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = %s
GROUP BY TABLE_NAME, INDEX_NAME
ORDER BY TABLE_NAME, INDEX_NAME
"""

INDEX_SIZES = """
SELECT table_name, index_name, stat_value * @@innodb_page_size AS size_bytes
FROM mysql.innodb_index_stats
WHERE database_name = %s AND stat_name = 'size'
"""

ROUTINES = """
SELECT ROUTINE_NAME AS name, ROUTINE_TYPE AS routine_type, DEFINER AS definer, DATA_TYPE AS returns_type,
       SECURITY_TYPE AS security_type, IS_DETERMINISTIC AS is_deterministic, CREATED AS created, LAST_ALTERED AS altered,
       ROUTINE_COMMENT AS comment_text
FROM information_schema.ROUTINES
WHERE ROUTINE_SCHEMA = %s AND ROUTINE_TYPE = %s
ORDER BY ROUTINE_NAME
"""

TRIGGERS = """
SELECT TRIGGER_NAME AS name, EVENT_OBJECT_TABLE AS table_name, ACTION_TIMING AS timing, EVENT_MANIPULATION AS event_name,
       DEFINER AS definer, CREATED AS created
FROM information_schema.TRIGGERS
WHERE TRIGGER_SCHEMA = %s
ORDER BY EVENT_OBJECT_TABLE, TRIGGER_NAME
"""

CONSTRAINTS = """
SELECT tc.CONSTRAINT_NAME AS name, tc.TABLE_NAME AS table_name, tc.CONSTRAINT_TYPE AS constraint_type,
       GROUP_CONCAT(k.COLUMN_NAME ORDER BY k.ORDINAL_POSITION SEPARATOR ', ') AS columns_list,
       MIN(k.REFERENCED_TABLE_SCHEMA) AS ref_schema, MIN(k.REFERENCED_TABLE_NAME) AS ref_table,
       GROUP_CONCAT(k.REFERENCED_COLUMN_NAME ORDER BY k.ORDINAL_POSITION SEPARATOR ', ') AS ref_columns
FROM information_schema.TABLE_CONSTRAINTS tc
LEFT JOIN information_schema.KEY_COLUMN_USAGE k
  ON k.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA AND k.CONSTRAINT_NAME = tc.CONSTRAINT_NAME AND k.TABLE_NAME = tc.TABLE_NAME
WHERE tc.TABLE_SCHEMA = %s {table_filter}
GROUP BY tc.CONSTRAINT_NAME, tc.TABLE_NAME, tc.CONSTRAINT_TYPE
ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_NAME
"""

COLUMNS = """
SELECT COLUMN_NAME AS name, COLUMN_TYPE AS data_type, IS_NULLABLE AS nullable, COLUMN_DEFAULT AS default_value,
       ORDINAL_POSITION AS position, COLUMN_COMMENT AS comment_text, EXTRA AS extra, COLUMN_KEY AS column_key
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
ORDER BY ORDINAL_POSITION
"""

TABLE_ONE = """
SELECT TABLE_NAME AS name, TABLE_TYPE AS table_type, ENGINE AS engine, TABLE_ROWS AS row_estimate,
       DATA_LENGTH AS data_bytes, INDEX_LENGTH AS index_bytes, DATA_FREE AS free_bytes, AUTO_INCREMENT AS auto_increment,
       CREATE_TIME AS created, UPDATE_TIME AS updated, TABLE_COMMENT AS comment_text, ROW_FORMAT AS row_format
FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
"""

# --- privileges (information_schema: every grant the account may see) -----------------------
USER_PRIVILEGES = "SELECT GRANTEE AS grantee, PRIVILEGE_TYPE AS privilege, IS_GRANTABLE AS grantable FROM information_schema.USER_PRIVILEGES"
SCHEMA_PRIVILEGES = """
SELECT GRANTEE AS grantee, TABLE_SCHEMA AS db, PRIVILEGE_TYPE AS privilege, IS_GRANTABLE AS grantable
FROM information_schema.SCHEMA_PRIVILEGES {where}
"""
TABLE_PRIVILEGES = """
SELECT GRANTEE AS grantee, TABLE_SCHEMA AS db, TABLE_NAME AS table_name, PRIVILEGE_TYPE AS privilege, IS_GRANTABLE AS grantable
FROM information_schema.TABLE_PRIVILEGES {where}
"""
COLUMN_PRIVILEGES = """
SELECT GRANTEE AS grantee, TABLE_SCHEMA AS db, TABLE_NAME AS table_name, COLUMN_NAME AS column_name,
       PRIVILEGE_TYPE AS privilege, IS_GRANTABLE AS grantable
FROM information_schema.COLUMN_PRIVILEGES {where}
"""
DIRECT_GRANT_COUNTS = """
SELECT GRANTEE AS grantee, COUNT(*) AS grants FROM (
  SELECT GRANTEE FROM information_schema.TABLE_PRIVILEGES
  UNION ALL SELECT GRANTEE FROM information_schema.COLUMN_PRIVILEGES
) g GROUP BY GRANTEE ORDER BY grants DESC
"""

# --- sessions / locks ---------------------------------------------------------------------
PROCESSLIST = """
SELECT ID AS id, USER AS user_name, HOST AS host, DB AS db, COMMAND AS command, TIME AS time_s, STATE AS state,
       LEFT(INFO, 4000) AS info
FROM information_schema.PROCESSLIST
ORDER BY TIME DESC
"""
INNODB_TRX = """
SELECT trx_mysql_thread_id AS thread_id, trx_started AS started, trx_state AS state, trx_rows_locked AS rows_locked,
       trx_lock_structs AS lock_structs, trx_wait_started AS wait_started
FROM information_schema.INNODB_TRX
"""

# --- query statistics (performance_schema digests; timers are picoseconds) ------------------
DIGEST_TOTALS = """
SELECT SUM(COUNT_STAR) AS calls, SUM(SUM_TIMER_WAIT) / 1000000000 AS total_ms
FROM performance_schema.events_statements_summary_global_by_event_name
WHERE EVENT_NAME LIKE 'statement/sql/%%'
"""
DIGEST_TOP = """
SELECT SCHEMA_NAME AS db, DIGEST AS digest, LEFT(DIGEST_TEXT, 8000) AS digest_text, COUNT_STAR AS calls,
       SUM_TIMER_WAIT / 1000000000 AS total_ms, AVG_TIMER_WAIT / 1000000000 AS mean_ms, MAX_TIMER_WAIT / 1000000000 AS max_ms,
       SUM_LOCK_TIME / 1000000000 AS lock_ms, SUM_ROWS_SENT AS rows_sent, SUM_ROWS_EXAMINED AS rows_examined,
       SUM_ROWS_AFFECTED AS rows_affected, SUM_CREATED_TMP_TABLES AS tmp_tables, SUM_CREATED_TMP_DISK_TABLES AS tmp_disk_tables,
       SUM_NO_INDEX_USED AS no_index_used, SUM_SELECT_FULL_JOIN AS full_joins, SUM_SORT_MERGE_PASSES AS sort_merge_passes,
       SUM_ERRORS AS errors, FIRST_SEEN AS first_seen, LAST_SEEN AS last_seen {extra}
FROM performance_schema.events_statements_summary_by_digest
WHERE DIGEST IS NOT NULL AND COUNT_STAR > 0
ORDER BY SUM_TIMER_WAIT DESC
LIMIT %s
"""

UNUSED_INDEXES = """
SELECT OBJECT_SCHEMA AS db, OBJECT_NAME AS table_name, INDEX_NAME AS index_name
FROM performance_schema.table_io_waits_summary_by_index_usage
WHERE INDEX_NAME IS NOT NULL AND INDEX_NAME <> 'PRIMARY' AND COUNT_STAR = 0
  AND OBJECT_SCHEMA NOT IN ('mysql', 'performance_schema', 'sys', 'information_schema')
ORDER BY OBJECT_SCHEMA, OBJECT_NAME
LIMIT 200
"""

TABLES_WITHOUT_PK = """
SELECT CONCAT(t.TABLE_SCHEMA, '.', t.TABLE_NAME) AS name, t.TABLE_ROWS AS row_estimate
FROM information_schema.TABLES t
LEFT JOIN information_schema.TABLE_CONSTRAINTS c
  ON c.TABLE_SCHEMA = t.TABLE_SCHEMA AND c.TABLE_NAME = t.TABLE_NAME AND c.CONSTRAINT_TYPE = 'PRIMARY KEY'
WHERE t.TABLE_TYPE = 'BASE TABLE' AND t.ENGINE = 'InnoDB' AND c.CONSTRAINT_NAME IS NULL
  AND t.TABLE_SCHEMA NOT IN ('mysql', 'performance_schema', 'sys', 'information_schema')
ORDER BY t.TABLE_ROWS DESC
LIMIT 50
"""

FULL_SCAN_STATEMENTS = """
SELECT SCHEMA_NAME AS db, DIGEST AS digest, LEFT(DIGEST_TEXT, 500) AS digest_text, COUNT_STAR AS calls,
       SUM_NO_INDEX_USED AS no_index_used, SUM_ROWS_EXAMINED AS rows_examined, SUM_ROWS_SENT AS rows_sent,
       SUM_TIMER_WAIT / 1000000000 AS total_ms
FROM performance_schema.events_statements_summary_by_digest
WHERE SUM_NO_INDEX_USED > 0 AND SCHEMA_NAME IS NOT NULL
  AND SCHEMA_NAME NOT IN ('mysql', 'performance_schema', 'sys', 'information_schema')
ORDER BY SUM_ROWS_EXAMINED DESC
LIMIT 20
"""

FRAGMENTED_TABLES = """
SELECT CONCAT(TABLE_SCHEMA, '.', TABLE_NAME) AS name, DATA_LENGTH + INDEX_LENGTH AS size_bytes, DATA_FREE AS free_bytes
FROM information_schema.TABLES
WHERE TABLE_TYPE = 'BASE TABLE' AND DATA_FREE > 268435456 AND DATA_FREE > 0.2 * (DATA_LENGTH + INDEX_LENGTH)
  AND TABLE_SCHEMA NOT IN ('mysql', 'performance_schema', 'sys', 'information_schema')
ORDER BY DATA_FREE DESC
LIMIT 20
"""

TOP_TABLES = """
SELECT TABLE_SCHEMA AS `schema`, TABLE_NAME AS name, ENGINE AS engine, TABLE_ROWS AS row_estimate,
       DATA_LENGTH AS table_bytes, INDEX_LENGTH AS index_bytes, DATA_LENGTH + INDEX_LENGTH AS total_bytes, DATA_FREE AS free_bytes
FROM information_schema.TABLES
WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA NOT IN ('mysql', 'performance_schema', 'sys', 'information_schema')
ORDER BY DATA_LENGTH + INDEX_LENGTH DESC
LIMIT 20
"""

TOTAL_SIZE = """
SELECT COALESCE(SUM(DATA_LENGTH + INDEX_LENGTH), 0) AS size_bytes
FROM information_schema.TABLES
WHERE TABLE_SCHEMA NOT IN ('information_schema', 'performance_schema', 'sys')
"""

FILE_PRIV_HOLDERS = "SELECT GRANTEE AS grantee FROM information_schema.USER_PRIVILEGES WHERE PRIVILEGE_TYPE = 'FILE'"
PLUGINS = "SELECT PLUGIN_NAME AS name, PLUGIN_STATUS AS status FROM information_schema.PLUGINS"
