"""T-SQL used by the SQL Server adapter (SQL Server 2016+, Azure SQL Managed Instance;
Azure SQL Database where noted). Placeholders use the ODBC ``?`` style.

Most server-wide DMVs need ``VIEW SERVER STATE`` (2022: ``VIEW SERVER PERFORMANCE STATE``);
catalog views in a database need ``VIEW DEFINITION`` to show other principals' objects.
"""

SERVER_INFO = """
SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
       CAST(SERVERPROPERTY('ProductLevel') AS nvarchar(128)) AS product_level,
       CAST(SERVERPROPERTY('ProductUpdateLevel') AS nvarchar(128)) AS update_level,
       CAST(SERVERPROPERTY('Edition') AS nvarchar(128)) AS edition,
       CAST(SERVERPROPERTY('EngineEdition') AS int) AS engine_edition,
       CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) AS machine_name,
       CAST(SERVERPROPERTY('ServerName') AS nvarchar(256)) AS server_name,
       CAST(SERVERPROPERTY('InstanceName') AS nvarchar(128)) AS instance_name,
       CAST(SERVERPROPERTY('IsClustered') AS int) AS is_clustered,
       CAST(SERVERPROPERTY('IsHadrEnabled') AS int) AS is_hadr_enabled,
       CAST(SERVERPROPERTY('IsIntegratedSecurityOnly') AS int) AS integrated_security_only,
       CAST(SERVERPROPERTY('Collation') AS nvarchar(128)) AS collation_name,
       @@VERSION AS version_full, SUSER_SNAME() AS login_name, DB_NAME() AS current_db,
       IS_SRVROLEMEMBER('sysadmin') AS is_sysadmin,
       HAS_PERMS_BY_NAME(NULL, NULL, 'VIEW SERVER STATE') AS view_server_state,
       HAS_PERMS_BY_NAME(NULL, NULL, 'VIEW ANY DEFINITION') AS view_any_definition
"""
START_TIME = "SELECT sqlserver_start_time AS started, cpu_count, physical_memory_kb FROM sys.dm_os_sys_info"
START_TIME_TEMPDB = "SELECT create_date AS started FROM sys.databases WHERE name = 'tempdb'"

# --- inventory -------------------------------------------------------------------------------
DATABASES = """
SELECT d.name, d.database_id, SUSER_SNAME(d.owner_sid) AS owner_name, d.state_desc, d.recovery_model_desc,
       d.compatibility_level, d.collation_name, d.is_read_only, d.user_access_desc, d.create_date,
       d.is_encrypted, d.is_trustworthy_on, d.is_auto_shrink_on, d.is_auto_close_on, d.log_reuse_wait_desc,
       d.is_read_committed_snapshot_on, d.snapshot_isolation_state_desc, {query_store}
       HAS_DBACCESS(d.name) AS accessible
FROM sys.databases d
ORDER BY d.name
"""
DATABASE_FILES = """
SELECT database_id, type_desc, SUM(CAST(size AS bigint)) * 8192 AS bytes,
       SUM(CASE WHEN max_size = -1 THEN NULL ELSE CAST(max_size AS bigint) END) * 8192 AS max_bytes
FROM sys.master_files
GROUP BY database_id, type_desc
"""
SCHEMAS = """
SELECT s.name, USER_NAME(s.principal_id) AS owner_name, s.schema_id,
       SUM(CASE WHEN o.type = 'U' THEN 1 ELSE 0 END) AS tables,
       SUM(CASE WHEN o.type = 'V' THEN 1 ELSE 0 END) AS views,
       SUM(CASE WHEN o.type IN ('P', 'PC') THEN 1 ELSE 0 END) AS procedures,
       SUM(CASE WHEN o.type IN ('FN', 'IF', 'TF', 'FS', 'FT') THEN 1 ELSE 0 END) AS functions,
       SUM(CASE WHEN o.type = 'TR' THEN 1 ELSE 0 END) AS triggers,
       SUM(CASE WHEN o.type = 'SO' THEN 1 ELSE 0 END) AS sequences,
       SUM(CASE WHEN o.type = 'SN' THEN 1 ELSE 0 END) AS synonyms
FROM sys.schemas s
LEFT JOIN sys.objects o ON o.schema_id = s.schema_id AND o.is_ms_shipped = 0
GROUP BY s.name, s.principal_id, s.schema_id
ORDER BY s.name
"""
OBJECTS = """
SELECT o.name, o.type, o.type_desc, o.create_date, o.modify_date, OBJECT_NAME(o.parent_object_id) AS parent_name,
       o.object_id
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE s.name = ? AND o.is_ms_shipped = 0 AND o.type IN ({types})
ORDER BY o.name
"""
TABLE_SIZES = """
SELECT ps.object_id, SUM(ps.used_page_count) * 8192 AS used_bytes,
       SUM(CASE WHEN ps.index_id IN (0, 1) THEN ps.row_count ELSE 0 END) AS row_count
FROM sys.dm_db_partition_stats ps
JOIN sys.objects o ON o.object_id = ps.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE s.name = ?
GROUP BY ps.object_id
"""
INDEXES = """
SELECT i.name, OBJECT_NAME(i.object_id) AS table_name, i.type_desc, i.is_unique, i.is_primary_key,
       i.is_disabled, i.has_filter, i.fill_factor,
       STUFF((SELECT ', ' + c.name FROM sys.index_columns ic JOIN sys.columns c
              ON c.object_id = ic.object_id AND c.column_id = ic.column_id
              WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0
              ORDER BY ic.key_ordinal FOR XML PATH('')), 1, 2, '') AS key_columns,
       STUFF((SELECT ', ' + c.name FROM sys.index_columns ic JOIN sys.columns c
              ON c.object_id = ic.object_id AND c.column_id = ic.column_id
              WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 1
              FOR XML PATH('')), 1, 2, '') AS included_columns
FROM sys.indexes i
JOIN sys.objects o ON o.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE s.name = ? AND i.index_id > 0 AND i.name IS NOT NULL AND o.is_ms_shipped = 0 {table_filter}
ORDER BY OBJECT_NAME(i.object_id), i.name
"""
COLUMNS = """
SELECT c.name, t.name AS type_name, c.max_length, c.precision, c.scale, c.is_nullable, c.column_id,
       OBJECT_DEFINITION(c.default_object_id) AS default_value, c.is_identity, c.is_computed
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID(?)
ORDER BY c.column_id
"""
OBJECT_ROW = """
SELECT o.object_id, o.type, o.type_desc, o.create_date, o.modify_date, OBJECT_DEFINITION(o.object_id) AS definition_text
FROM sys.objects o WHERE o.object_id = OBJECT_ID(?)
"""
CONSTRAINTS = """
SELECT kc.name, kc.type_desc AS constraint_type, OBJECT_NAME(kc.parent_object_id) AS table_name
FROM sys.key_constraints kc WHERE kc.parent_object_id = OBJECT_ID(?)
UNION ALL
SELECT fk.name, 'FOREIGN_KEY_CONSTRAINT', OBJECT_NAME(fk.parent_object_id)
FROM sys.foreign_keys fk WHERE fk.parent_object_id = OBJECT_ID(?)
UNION ALL
SELECT cc.name, 'CHECK_CONSTRAINT', OBJECT_NAME(cc.parent_object_id)
FROM sys.check_constraints cc WHERE cc.parent_object_id = OBJECT_ID(?)
"""
TRIGGERS_OF = """
SELECT t.name, t.is_disabled, t.is_instead_of_trigger, t.create_date
FROM sys.triggers t WHERE t.parent_id = OBJECT_ID(?)
"""

# --- principals --------------------------------------------------------------------------------
LOGINS = """
SELECT p.name, p.principal_id, p.type, p.type_desc, p.is_disabled, p.create_date, p.modify_date,
       p.default_database_name, CONVERT(varchar(85), p.sid, 1) AS sid_hex,
       sl.is_policy_checked, sl.is_expiration_checked,
       CAST(LOGINPROPERTY(p.name, 'PasswordLastSetTime') AS datetime2) AS password_last_set,
       CAST(LOGINPROPERTY(p.name, 'IsLocked') AS int) AS is_locked,
       CAST(LOGINPROPERTY(p.name, 'IsExpired') AS int) AS is_expired
FROM sys.server_principals p
LEFT JOIN sys.sql_logins sl ON sl.principal_id = p.principal_id
WHERE p.type IN ('S', 'U', 'G', 'E', 'X', 'C', 'K')
ORDER BY p.name
"""
SERVER_ROLE_MEMBERS = """
SELECT r.name AS role_name, m.name AS member_name
FROM sys.server_role_members rm
JOIN sys.server_principals r ON r.principal_id = rm.role_principal_id
JOIN sys.server_principals m ON m.principal_id = rm.member_principal_id
"""
SERVER_ROLES = "SELECT name, principal_id, is_fixed_role, create_date FROM sys.server_principals WHERE type = 'R'"
SERVER_PERMISSIONS = """
SELECT pr.name AS grantee, pe.permission_name, pe.state_desc, pe.class_desc,
       CASE WHEN pe.class = 101 THEN SUSER_NAME(pe.major_id) WHEN pe.class = 105 THEN (SELECT name FROM sys.endpoints e WHERE e.endpoint_id = pe.major_id) END AS object_name
FROM sys.server_permissions pe
JOIN sys.server_principals pr ON pr.principal_id = pe.grantee_principal_id
"""
DB_PRINCIPALS = """
SELECT dp.name, dp.type, dp.type_desc, dp.authentication_type_desc, dp.create_date, dp.default_schema_name,
       CONVERT(varchar(85), dp.sid, 1) AS sid_hex, dp.is_fixed_role,
       CASE WHEN dp.type IN ('S', 'U', 'G') AND dp.authentication_type = 1 AND dp.sid IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM sys.server_principals sp WHERE sp.sid = dp.sid) THEN 1 ELSE 0 END AS orphaned
FROM sys.database_principals dp
WHERE dp.type IN ('S', 'U', 'G', 'E', 'X', 'C', 'K', 'R', 'A')
ORDER BY dp.name
"""
DB_ROLE_MEMBERS = """
SELECT r.name AS role_name, m.name AS member_name
FROM sys.database_role_members rm
JOIN sys.database_principals r ON r.principal_id = rm.role_principal_id
JOIN sys.database_principals m ON m.principal_id = rm.member_principal_id
"""
DB_PERMISSIONS = """
SELECT pr.name AS grantee, pe.permission_name, pe.state_desc, pe.class_desc,
       CASE pe.class WHEN 1 THEN OBJECT_SCHEMA_NAME(pe.major_id) + '.' + OBJECT_NAME(pe.major_id)
                     WHEN 3 THEN SCHEMA_NAME(pe.major_id)
                     WHEN 0 THEN DB_NAME() END AS object_name,
       CASE WHEN pe.class = 1 AND pe.minor_id > 0 THEN COL_NAME(pe.major_id, pe.minor_id) END AS column_name,
       USER_NAME(pe.grantor_principal_id) AS grantor
FROM sys.database_permissions pe
JOIN sys.database_principals pr ON pr.principal_id = pe.grantee_principal_id
WHERE NOT (pe.class = 0 AND pe.permission_name = 'CONNECT' AND pr.name IN ('dbo', 'guest') AND pe.state_desc = 'REVOKE')
"""
DIRECT_GRANT_COUNTS = """
SELECT pr.name AS grantee, COUNT(*) AS grants
FROM sys.database_permissions pe
JOIN sys.database_principals pr ON pr.principal_id = pe.grantee_principal_id
WHERE pe.class = 1 AND pr.type IN ('S', 'U', 'G', 'E', 'X')
GROUP BY pr.name
"""

# --- sessions & locks ---------------------------------------------------------------------------
SESSIONS = """
SELECT s.session_id, s.login_name, DB_NAME(COALESCE(r.database_id, s.database_id)) AS database_name, s.program_name,
       s.host_name, c.client_net_address, s.status AS session_status, r.status AS request_status, r.command,
       r.wait_type, r.wait_resource, r.wait_time, r.blocking_session_id, r.start_time, s.login_time,
       s.last_request_start_time, r.total_elapsed_time, s.open_transaction_count, c.encrypt_option, s.is_user_process,
       LEFT(SUBSTRING(t.text, (r.statement_start_offset / 2) + 1,
            ((CASE r.statement_end_offset WHEN -1 THEN DATALENGTH(t.text) ELSE r.statement_end_offset END
              - r.statement_start_offset) / 2) + 1), 4000) AS statement_text,
       tx.transaction_begin_time
FROM sys.dm_exec_sessions s
LEFT JOIN sys.dm_exec_requests r ON r.session_id = s.session_id
OUTER APPLY (SELECT TOP 1 cn.client_net_address, cn.encrypt_option FROM sys.dm_exec_connections cn
             WHERE cn.session_id = s.session_id) c
OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
OUTER APPLY (SELECT MIN(at.transaction_begin_time) AS transaction_begin_time
             FROM sys.dm_tran_session_transactions st
             JOIN sys.dm_tran_active_transactions at ON at.transaction_id = st.transaction_id
             WHERE st.session_id = s.session_id) tx
WHERE s.session_id <> @@SPID
ORDER BY CASE WHEN r.session_id IS NULL THEN 1 ELSE 0 END, r.total_elapsed_time DESC
"""
LOCKS = """
SELECT TOP (1000) l.request_session_id, l.resource_type, DB_NAME(l.resource_database_id) AS database_name,
       CASE WHEN l.resource_type = 'OBJECT' THEN OBJECT_SCHEMA_NAME(l.resource_associated_entity_id, l.resource_database_id)
                 + '.' + OBJECT_NAME(l.resource_associated_entity_id, l.resource_database_id) END AS object_name,
       l.resource_description, l.request_mode, l.request_status, w.blocking_session_id, w.wait_duration_ms
FROM sys.dm_tran_locks l
LEFT JOIN sys.dm_os_waiting_tasks w ON w.resource_address = l.lock_owner_address
WHERE l.request_session_id <> @@SPID AND NOT (l.resource_type = 'DATABASE' AND l.request_mode = 'S' AND l.request_status = 'GRANT')
ORDER BY CASE l.request_status WHEN 'WAIT' THEN 0 ELSE 1 END, l.request_session_id
"""

# --- metrics -------------------------------------------------------------------------------------
SESSION_COUNTS = """
SELECT COUNT(*) AS total,
       SUM(CASE WHEN r.session_id IS NOT NULL THEN 1 ELSE 0 END) AS active,
       SUM(CASE WHEN r.blocking_session_id > 0 THEN 1 ELSE 0 END) AS blocked,
       SUM(CASE WHEN r.session_id IS NULL AND s.open_transaction_count > 0 THEN 1 ELSE 0 END) AS idle_in_txn,
       MAX(CASE WHEN r.session_id IS NOT NULL THEN r.total_elapsed_time END) / 1000.0 AS longest_query_s,
       SUM(CASE WHEN c.encrypt_option = 'FALSE' THEN 1 ELSE 0 END) AS unencrypted
FROM sys.dm_exec_sessions s
LEFT JOIN sys.dm_exec_requests r ON r.session_id = s.session_id AND r.session_id <> @@SPID
OUTER APPLY (SELECT TOP 1 cn.encrypt_option FROM sys.dm_exec_connections cn WHERE cn.session_id = s.session_id) c
WHERE s.is_user_process = 1
"""
PERF_COUNTERS = """
SELECT RTRIM(object_name) AS object_name, RTRIM(counter_name) AS counter_name, RTRIM(instance_name) AS instance_name,
       cntr_value, cntr_type
FROM sys.dm_os_performance_counters
WHERE (object_name LIKE '%:General Statistics%' AND counter_name IN ('User Connections', 'Processes blocked', 'Logins/sec'))
   OR (object_name LIKE '%:SQL Statistics%' AND counter_name IN ('Batch Requests/sec', 'SQL Compilations/sec', 'SQL Re-Compilations/sec'))
   OR (object_name LIKE '%:Buffer Manager%' AND counter_name IN ('Page life expectancy', 'Page lookups/sec', 'Page reads/sec', 'Page writes/sec', 'Lazy writes/sec', 'Checkpoint pages/sec'))
   OR (object_name LIKE '%:Databases%' AND instance_name = '_Total' AND counter_name IN ('Transactions/sec', 'Log Flushes/sec', 'Log Bytes Flushed/sec'))
   OR (object_name LIKE '%:Locks%' AND instance_name = '_Total' AND counter_name IN ('Lock Waits/sec', 'Number of Deadlocks/sec', 'Lock Timeouts/sec'))
   OR (object_name LIKE '%:Memory Manager%' AND counter_name IN ('Total Server Memory (KB)', 'Target Server Memory (KB)', 'Memory Grants Pending'))
   OR (object_name LIKE '%:Access Methods%' AND counter_name IN ('Full Scans/sec', 'Index Searches/sec', 'Page Splits/sec'))
"""
CPU_RING_BUFFER = """
SELECT TOP 1
       record.value('(./Record/SchedulerMonitorEvent/SystemHealth/ProcessUtilization)[1]', 'int') AS sql_cpu,
       record.value('(./Record/SchedulerMonitorEvent/SystemHealth/SystemIdle)[1]', 'int') AS system_idle
FROM (SELECT [timestamp], CONVERT(xml, record) AS record
      FROM sys.dm_os_ring_buffers
      WHERE ring_buffer_type = N'RING_BUFFER_SCHEDULER_MONITOR' AND record LIKE N'%<SystemHealth>%') AS rb
ORDER BY [timestamp] DESC
"""
AZURE_RESOURCE_STATS = """
SELECT TOP 1 avg_cpu_percent, avg_data_io_percent, avg_log_write_percent, avg_memory_usage_percent
FROM sys.dm_db_resource_stats ORDER BY end_time DESC
"""
CURRENT_WAITS = """
SELECT TOP (10) COALESCE(r.wait_type, 'CPU') AS wait_type, COUNT(*) AS n
FROM sys.dm_exec_requests r
JOIN sys.dm_exec_sessions s ON s.session_id = r.session_id
WHERE s.is_user_process = 1 AND r.session_id <> @@SPID
GROUP BY COALESCE(r.wait_type, 'CPU')
ORDER BY COUNT(*) DESC
"""
DATA_SIZE = "SELECT SUM(CAST(size AS bigint)) * 8192 AS bytes FROM sys.master_files WHERE type = 0"
LOG_SPACE = "DBCC SQLPERF(LOGSPACE) WITH NO_INFOMSGS"
CONFIG = """
SELECT name, CAST(value AS nvarchar(64)) AS configured_value, CAST(value_in_use AS nvarchar(64)) AS value_in_use,
       CAST(minimum AS nvarchar(64)) AS min_value, CAST(maximum AS nvarchar(64)) AS max_value,
       description, is_dynamic, is_advanced
FROM sys.configurations ORDER BY name
"""
CONFIG_VALUES = (
    "SELECT name, CAST(value_in_use AS bigint) AS value_in_use FROM sys.configurations WHERE name IN ({names})"
)

# --- query statistics (plan cache) --------------------------------------------------------------
QUERY_STATS = """
SELECT TOP (?) CONVERT(varchar(34), qs.query_hash, 1) AS query_hash,
       SUM(qs.execution_count) AS calls,
       SUM(qs.total_elapsed_time) / 1000.0 AS total_ms,
       SUM(qs.total_worker_time) / 1000.0 AS cpu_ms,
       MAX(qs.max_elapsed_time) / 1000.0 AS max_ms,
       SUM(qs.total_logical_reads) AS logical_reads,
       SUM(qs.total_physical_reads) AS physical_reads,
       SUM(qs.total_logical_writes) AS logical_writes,
       SUM(qs.total_rows) AS rows_count,
       COUNT(DISTINCT qs.query_plan_hash) AS plans,
       {spills}
       MAX(qs.last_execution_time) AS last_execution_time,
       MIN(qs.creation_time) AS first_cached,
       DB_NAME(CONVERT(int, pa.value)) AS database_name,
       MIN(LEFT(SUBSTRING(st.text, (qs.statement_start_offset / 2) + 1,
            ((CASE qs.statement_end_offset WHEN -1 THEN DATALENGTH(st.text) ELSE qs.statement_end_offset END
              - qs.statement_start_offset) / 2) + 1), 8000)) AS statement_text
FROM sys.dm_exec_query_stats qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
OUTER APPLY (SELECT value FROM sys.dm_exec_plan_attributes(qs.plan_handle) WHERE attribute = 'dbid') pa
GROUP BY qs.query_hash, CONVERT(int, pa.value)
ORDER BY SUM(qs.total_elapsed_time) DESC
"""
QUERY_TOTALS = """
SELECT SUM(execution_count) AS calls, SUM(total_elapsed_time) / 1000.0 AS total_ms FROM sys.dm_exec_query_stats
"""

# --- replication (Always On availability groups) ------------------------------------------------
AG_REPLICAS = """
SELECT ag.name AS ag_name, ar.replica_server_name, ars.role_desc, ars.operational_state_desc,
       ars.connected_state_desc, ars.synchronization_health_desc, ar.availability_mode_desc, ar.failover_mode_desc,
       ars.is_local
FROM sys.availability_groups ag
JOIN sys.availability_replicas ar ON ar.group_id = ag.group_id
LEFT JOIN sys.dm_hadr_availability_replica_states ars ON ars.replica_id = ar.replica_id
"""
AG_DATABASES = """
SELECT DB_NAME(drs.database_id) AS database_name, ar.replica_server_name, drs.is_local, drs.synchronization_state_desc,
       drs.synchronization_health_desc, drs.log_send_queue_size, drs.redo_queue_size, drs.secondary_lag_seconds,
       drs.last_commit_time
FROM sys.dm_hadr_database_replica_states drs
JOIN sys.availability_replicas ar ON ar.replica_id = drs.replica_id
"""

# --- security evidence --------------------------------------------------------------------------
SA_LOGIN = "SELECT name, is_disabled FROM sys.server_principals WHERE sid = 0x01"
SECURITY_CONFIG = """
SELECT name, CAST(value_in_use AS int) AS value_in_use FROM sys.configurations
WHERE name IN ('xp_cmdshell', 'clr enabled', 'clr strict security', 'Ad Hoc Distributed Queries',
               'Ole Automation Procedures', 'remote admin connections', 'cross db ownership chaining',
               'Database Mail XPs', 'contained database authentication', 'remote access', 'scan for startup procs')
"""
FORCE_ENCRYPTION = """
SELECT CAST(value_data AS int) AS force_encryption FROM sys.dm_server_registry
WHERE registry_key LIKE N'%SuperSocketNetLib' AND value_name = N'ForceEncryption'
"""
SERVER_AUDITS = """
SELECT a.name, a.is_state_enabled, s.status_desc
FROM sys.server_audits a LEFT JOIN sys.dm_server_audit_status s ON s.audit_id = a.audit_id
"""
LOGINS_NO_POLICY = "SELECT name FROM sys.sql_logins WHERE is_policy_checked = 0 AND is_disabled = 0"
GUEST_CONNECT = """
SELECT COUNT(*) AS n FROM sys.database_permissions
WHERE grantee_principal_id = DATABASE_PRINCIPAL_ID('guest') AND permission_name = 'CONNECT' AND state IN ('G', 'W')
"""
ORPHANED_USERS = """
SELECT dp.name FROM sys.database_principals dp
WHERE dp.type IN ('S', 'U', 'G') AND dp.authentication_type = 1 AND dp.sid IS NOT NULL AND dp.principal_id > 4
  AND NOT EXISTS (SELECT 1 FROM sys.server_principals sp WHERE sp.sid = dp.sid)
"""

# --- tuning evidence ----------------------------------------------------------------------------
TOP_WAITS = """
SELECT TOP (15) wait_type, wait_time_ms, waiting_tasks_count,
       100.0 * wait_time_ms / NULLIF(SUM(wait_time_ms) OVER (), 0) AS pct
FROM sys.dm_os_wait_stats
WHERE wait_time_ms > 0 AND wait_type NOT IN (
  'BROKER_EVENTHANDLER','BROKER_RECEIVE_WAITFOR','BROKER_TASK_STOP','BROKER_TO_FLUSH','BROKER_TRANSMITTER','CHECKPOINT_QUEUE',
  'CHKPT','CLR_AUTO_EVENT','CLR_MANUAL_EVENT','CLR_SEMAPHORE','DBMIRROR_DBM_EVENT','DBMIRROR_EVENTS_QUEUE','DBMIRROR_WORKER_QUEUE',
  'DBMIRRORING_CMD','DIRTY_PAGE_POLL','DISPATCHER_QUEUE_SEMAPHORE','EXECSYNC','FSAGENT','FT_IFTS_SCHEDULER_IDLE_WAIT','FT_IFTSHC_MUTEX',
  'HADR_CLUSAPI_CALL','HADR_FILESTREAM_IOMGR_IOCOMPLETION','HADR_LOGCAPTURE_WAIT','HADR_NOTIFICATION_DEQUEUE','HADR_TIMER_TASK',
  'HADR_WORK_QUEUE','KSOURCE_WAKEUP','LAZYWRITER_SLEEP','LOGMGR_QUEUE','MEMORY_ALLOCATION_EXT','ONDEMAND_TASK_QUEUE',
  'PARALLEL_REDO_DRAIN_WORKER','PARALLEL_REDO_LOG_CACHE','PARALLEL_REDO_TRAN_LIST','PARALLEL_REDO_WORKER_SYNC',
  'PARALLEL_REDO_WORKER_WAIT_WORK','PREEMPTIVE_OS_FLUSHFILEBUFFERS','PREEMPTIVE_XE_GETTARGETSTATE','PVS_PREALLOCATE',
  'PWAIT_ALL_COMPONENTS_INITIALIZED','PWAIT_DIRECTLOGCONSUMER_GETNEXT','PWAIT_EXTENSIBILITY_CLEANUP_TASK','QDS_ASYNC_QUEUE',
  'QDS_CLEANUP_STALE_QUERIES_TASK_MAIN_LOOP_SLEEP','QDS_PERSIST_TASK_MAIN_LOOP_SLEEP','QDS_SHUTDOWN_QUEUE','REDO_THREAD_PENDING_WORK',
  'REQUEST_FOR_DEADLOCK_SEARCH','RESOURCE_QUEUE','SERVER_IDLE_CHECK','SLEEP_BPOOL_FLUSH','SLEEP_DBSTARTUP','SLEEP_DCOMSTARTUP',
  'SLEEP_MASTERDBREADY','SLEEP_MASTERMDREADY','SLEEP_MASTERUPGRADED','SLEEP_MSDBSTARTUP','SLEEP_SYSTEMTASK','SLEEP_TASK',
  'SLEEP_TEMPDBSTARTUP','SNI_HTTP_ACCEPT','SOS_WORK_DISPATCHER','SP_SERVER_DIAGNOSTICS_SLEEP','SQLTRACE_BUFFER_FLUSH',
  'SQLTRACE_INCREMENTAL_FLUSH_SLEEP','SQLTRACE_WAIT_ENTRIES','UCS_SESSION_REGISTRATION','VDI_CLIENT_OTHER','WAIT_FOR_RESULTS',
  'WAITFOR','WAITFOR_TASKSHUTDOWN','WAIT_XTP_RECOVERY','WAIT_XTP_HOST_WAIT','WAIT_XTP_OFFLINE_CKPT_NEW_LOG','WAIT_XTP_CKPT_CLOSE',
  'XE_DISPATCHER_JOIN','XE_DISPATCHER_WAIT','XE_TIMER_EVENT','XE_LIVE_TARGET_TVF','SOS_SCHEDULER_YIELD_IDLE')
ORDER BY wait_time_ms DESC
"""
MISSING_INDEXES = """
SELECT TOP (20) d.statement AS [table], d.equality_columns, d.inequality_columns, d.included_columns,
       s.user_seeks, s.user_scans, s.avg_user_impact, s.avg_total_user_cost, s.last_user_seek,
       DB_NAME(d.database_id) AS database_name
FROM sys.dm_db_missing_index_details d
JOIN sys.dm_db_missing_index_groups g ON g.index_handle = d.index_handle
JOIN sys.dm_db_missing_index_group_stats s ON s.group_handle = g.index_group_handle
ORDER BY s.avg_user_impact * (s.user_seeks + s.user_scans) DESC
"""
TEMPDB_FILES = "SELECT COUNT(*) AS n FROM tempdb.sys.database_files WHERE type = 0"
UNUSED_INDEXES = """
SELECT TOP (50) OBJECT_SCHEMA_NAME(i.object_id) AS schema_name, OBJECT_NAME(i.object_id) AS table_name, i.name AS index_name,
       u.user_updates, u.user_seeks, u.user_scans, u.user_lookups
FROM sys.indexes i
JOIN sys.objects o ON o.object_id = i.object_id AND o.type = 'U' AND o.is_ms_shipped = 0
LEFT JOIN sys.dm_db_index_usage_stats u ON u.object_id = i.object_id AND u.index_id = i.index_id AND u.database_id = DB_ID()
WHERE i.index_id > 1 AND i.is_primary_key = 0 AND i.is_unique_constraint = 0
  AND COALESCE(u.user_seeks, 0) + COALESCE(u.user_scans, 0) + COALESCE(u.user_lookups, 0) = 0
ORDER BY COALESCE(u.user_updates, 0) DESC
"""
STALE_STATISTICS = """
SELECT TOP (20) OBJECT_SCHEMA_NAME(s.object_id) AS schema_name, OBJECT_NAME(s.object_id) AS table_name, s.name AS stats_name,
       sp.last_updated, sp.rows, sp.modification_counter
FROM sys.stats s
JOIN sys.objects o ON o.object_id = s.object_id AND o.type = 'U' AND o.is_ms_shipped = 0
CROSS APPLY sys.dm_db_stats_properties(s.object_id, s.stats_id) sp
WHERE sp.modification_counter > 1000 AND sp.modification_counter > 0.2 * sp.rows
ORDER BY sp.modification_counter DESC
"""
