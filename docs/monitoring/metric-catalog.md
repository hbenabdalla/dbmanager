# Metrics catalog

Generated from `backend/app/timeseries/catalog.py` (the single source of truth); regenerate when the catalog changes.

Every sample carries the required labels **`server_id`, `engine`, `data_source`**. Additional labels are listed per metric. Inventory attributes (environment, site, cluster, application, owner) are *not* labels — they are joined from PostgreSQL at query time.

**Types**: `gauge` — a level; `counter` — monotonically increasing total since the engine's statistics reset (charted as a per-second `rate` or an `increase`, never raw); `rate` — per-second value derived by the collector from two counter readings; `distribution` — a statistic of a distribution (mean / max latency). Engine-specific keys not listed here follow the generic rule `db_` + key with `.`→`_` (`.per_sec` → `_per_second`).

**API key vs storage name**: the API and UI use the platform key (`connections.total`); the time-series database stores the name (`db_connections`).

## Server metrics

| Metric | Key (API) | Description | Unit | Type | Engines | Labels | Interval |
|---|---|---|---|---|---|---|---|
| `db_connections` | `connections.total` | Client connections | count | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_connections_active` | `connections.active` | Sessions currently executing | count | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_connections_idle` | `connections.idle` | Connected but idle sessions | count | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_connections_idle_in_transaction` | `connections.idle_in_transaction` | Idle sessions holding an open transaction | count | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_connections_max` | `connections.max` | Configured connection limit | count | gauge | all | server_id, engine, data_source | SLOW (5 min) |
| `db_connections_utilization_percent` | `connections.utilization` | Connections as a share of the limit | % | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_sessions_blocked` | `sessions.blocked` | Sessions waiting on another session's lock | count | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_sessions_waiting` | `sessions.waiting` | Sessions in a lock wait | count | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_locks` | `locks.total` | Lock entries | count | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_locks_waiting` | `locks.waiting` | Ungranted lock requests | count | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_wait_active_sessions` | `wait.active_sessions` | Active sessions per wait class (sampled) | count | gauge | all | server_id, engine, data_source, wait_type | FAST (30 s) |
| `db_transaction_longest_seconds` | `transactions.longest_seconds` | Age of the oldest open transaction | s | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_query_longest_running_seconds` | `query.longest_running_seconds` | Runtime of the longest active statement | s | gauge | all | server_id, engine, data_source | FAST (30 s) |
| `db_transactions_per_second` | `transactions.per_sec` | Commits + rollbacks per second | 1/s | rate | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_commits_per_second` | `commits.per_sec` | Commits per second | 1/s | rate | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_rollbacks_per_second` | `rollbacks.per_sec` | Rollbacks per second | 1/s | rate | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_query_calls_per_second` | `query.calls_per_sec` | Statement executions per second | 1/s | rate | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_io_blocks_read_per_second` | `io.blocks_read_per_sec` | Blocks read from storage per second | 1/s | rate | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_temp_bytes_per_second` | `temp.bytes_per_sec` | Temporary file bytes written per second | B/s | rate | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_rows_returned_per_second` | `rows.returned.per_sec` | Rows returned by scans per second | 1/s | rate | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_rows_modified_per_second` | `rows.modified.per_sec` | Rows inserted/updated/deleted per second | 1/s | rate | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_deadlocks_delta` | `deadlocks.count` | Deadlocks since the previous collection | count | gauge | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_transactions_commits_total` | `transactions.commits` | Committed transactions since the engine's statistics reset | count | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_transactions_rollbacks_total` | `transactions.rollbacks` | Rolled-back transactions since reset | count | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_io_blocks_read_total` | `io.blocks_read` | Blocks read from storage since reset | count | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_io_blocks_hit_total` | `io.blocks_hit` | Block requests served from cache since reset | count | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_temp_bytes_total` | `temp.bytes` | Temporary file bytes written since reset | B | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_temp_files_total` | `temp.files` | Temporary files created since reset | count | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_rows_returned_total` | `rows.returned` | Rows returned by scans since reset | count | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_rows_modified_total` | `rows.modified` | Rows inserted, updated or deleted since reset | count | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_query_calls_engine_total` | `query.calls` | Statement executions reported by the engine's statement statistics | count | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_query_time_engine_ms_total` | `query.total_ms` | Total statement execution time reported by the engine | ms | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_deadlocks_total` | `deadlocks` | Deadlocks since reset | count | counter | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_cache_hit_ratio_percent` | `cache.hit_ratio` | Share of block requests served from the buffer cache | % | gauge | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_query_latency_mean_ms` | `query.mean_latency_ms` | Average statement execution time in the interval | ms | distribution | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_cpu_percent` | `cpu.percent` | Host / instance CPU utilization (engine-reported) | % | gauge | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_memory_used_bytes` | `memory.used_bytes` | Memory used (engine-reported) | B | gauge | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_memory_total_bytes` | `memory.total_bytes` | Memory available to the instance | B | gauge | all | server_id, engine, data_source | SLOW (5 min) |
| `db_up` | `availability.up` | 1 when the last collection connected successfully | bool | gauge | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_uptime_seconds` | `uptime.seconds` | Time since the database server started | s | gauge | all | server_id, engine, data_source | SLOW (5 min) |
| `db_replication_is_replica` | `replication.is_replica` | 1 when the server is a replica | bool | gauge | all | server_id, engine, data_source | SLOW (5 min) |
| `db_replication_lag_seconds` | `replication.lag_seconds` | Replay delay of the replica (or worst replica) | s | gauge | all | server_id, engine, data_source | NORMAL (60 s) |
| `db_replication_replicas` | `replication.replica_count` | Connected replicas | count | gauge | all | server_id, engine, data_source | SLOW (5 min) |
| `db_storage_database_bytes` | `storage.database_bytes` | Total size of all databases | B | gauge | all | server_id, engine, data_source | SLOW (5 min) |
| `db_storage_capacity_bytes` | `storage.capacity_bytes` | Allocated capacity of the data volume / storage | B | gauge | all | server_id, engine, data_source | SLOW (5 min) |
| `db_storage_utilization_percent` | `storage.utilization` | Used share of the data volume / allocated storage | % | gauge | all | server_id, engine, data_source | SLOW (5 min) |
| `db_database_size_bytes` | `storage.db_size_bytes` | Size of one user database (capacity forecasting) | B | gauge | all reporting database sizes | server_id, engine, data_source, database | VERY_SLOW (hourly, inventory discovery) |
| `db_tablespace_used_bytes` | `storage.tablespace_used_bytes` | Used size of one tablespace (top 50 per server) | B | gauge | Oracle, Db2, PostgreSQL, MySQL/MariaDB | server_id, engine, data_source, tablespace | VERY_SLOW (hourly, inventory discovery) |
| `db_tablespace_max_bytes` | `storage.tablespace_max_bytes` | Maximum size of a tablespace where known (Oracle autoextend limit, Db2 non-resizable) | B | gauge | Oracle, Db2 | server_id, engine, data_source, tablespace | VERY_SLOW (hourly, inventory discovery) |
| `db_pg_idle_in_transaction_longest_seconds` | `pg.idle_in_transaction_longest_seconds` | Age of the oldest idle-in-transaction session | s | gauge | postgresql | server_id, engine, data_source | FAST (30 s) |
| `db_oracle_sga_used_bytes` | `oracle.sga_used_bytes` | SGA in use | B | gauge | oracle | server_id, engine, data_source | SLOW (5 min) |
| `db_oracle_pga_used_bytes` | `oracle.pga_used_bytes` | PGA allocated | B | gauge | oracle | server_id, engine, data_source | NORMAL (60 s) |
| `db_db2_bufferpool_hit_ratio_percent` | `db2.bufferpool_hit_ratio` | Buffer pool hit ratio | % | gauge | db2 | server_id, engine, data_source | NORMAL (60 s) |
| `db_db2_lock_escalations_delta` | `db2.lock_escalations` | Lock escalations since the previous collection | count | gauge | db2 | server_id, engine, data_source | NORMAL (60 s) |
| `db_mssql_page_life_expectancy_seconds` | `mssql.page_life_expectancy` | Buffer pool page life expectancy | s | gauge | mssql | server_id, engine, data_source | NORMAL (60 s) |
| `db_mssql_batch_requests_per_second` | `mssql.batch_requests_per_sec` | Batch requests per second | 1/s | rate | mssql | server_id, engine, data_source | NORMAL (60 s) |
| `db_mysql_threads_running` | `mysql.threads_running` | Threads currently running | count | gauge | mysql, mariadb | server_id, engine, data_source | FAST (30 s) |
| `db_mysql_tmp_disk_tables_per_second` | `mysql.tmp_disk_tables_per_sec` | Implicit temporary tables created on disk per second | 1/s | rate | mysql, mariadb | server_id, engine, data_source | NORMAL (60 s) |

## Query telemetry

One series per server × database × fingerprint. The fingerprint is the first 16 hex digits of SHA-256 over the engine name and the literal-free, whitespace-normalized statement; the statement text itself lives only in PostgreSQL (`query_fingerprints`). Only the top `DBM_QUERY_STATS_TOP_N` statements by total time are written per collection, and a server never gets more than `DBM_QUERY_FINGERPRINTS_MAX_PER_SERVER` distinct fingerprints.

| Metric | Description | Unit | Type | Engines | Labels | Interval |
|---|---|---|---|---|---|---|
| `db_query_calls_total` | Executions of the statement | count | counter | all with query statistics | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_exec_time_ms_total` | Total execution time | ms | counter | all with query statistics | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_rows_total` | Rows returned/affected | count | counter | all with query statistics | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_cpu_time_ms_total` | CPU time | ms | counter | all with query statistics | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_logical_reads_total` | Logical (buffer) reads | count | counter | all with query statistics | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_physical_reads_total` | Physical reads | count | counter | all with query statistics | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_temp_blocks_total` | Temporary blocks written | count | counter | all with query statistics | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_lock_wait_ms_total` | Time spent waiting for locks | ms | counter | all with query statistics | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_mean_ms` | Mean execution time as reported in the collection | ms | distribution | all with query statistics | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_max_ms` | Maximum execution time as reported | ms | distribution | all with query statistics | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_plans` | Distinct cached execution plans of the statement | count | gauge | Oracle, SQL Server | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |
| `db_query_plan_id` | Hash of the plan with most elapsed time (`PLAN_HASH_VALUE`), used for plan-change detection | — | gauge | Oracle | server_id, engine, data_source, database, fingerprint | SLOW (5 min) |

## Platform self-observability (`/metrics` on the backend, Prometheus format)

These describe DBManager itself and are **not** written into the time-series database (no recursive collection); scrape them with Prometheus or an OpenTelemetry collector if needed.

| Metric | Type | Meaning |
|---|---|---|
| `dbm_metrics_collected_total` | counter | Samples produced by collectors and normalized |
| `dbm_metrics_written_total` | counter | Samples accepted by the time-series database |
| `dbm_metrics_write_failures_total` | counter | Failed batch writes (retried with backoff) |
| `dbm_metrics_dropped_total{reason}` | counter | Samples dropped: buffer_overflow, label_policy, query_cardinality, deleted |
| `dbm_metrics_buffer_pending` | gauge | Samples waiting in the write buffer |
| `dbm_metrics_batch_size` | histogram | Samples per write request |
| `dbm_metrics_write_latency_seconds` | histogram | Write request latency |
| `dbm_timeseries_query_latency_seconds{op}` | histogram | Time-series query latency by operation |
| `dbm_collector_jobs_total{kind,result}` | counter | Collector jobs and failures |
| `dbm_collector_job_duration_seconds{kind}` | histogram | Collector duration |
