# Query statistics

## Sources per engine

| Engine | Source | Identifier |
|---|---|---|
| PostgreSQL | `pg_stat_statements` (extension required) | `queryid` |
| MySQL / MariaDB | `performance_schema.events_statements_summary_by_digest` | digest |
| SQL Server | `sys.dm_exec_query_stats` + `sys.dm_exec_sql_text`, per statement hash and database | `query_hash` |
| Oracle | `V$SQLSTATS` | `SQL_ID` |
| Db2 LUW | `MON_GET_PKG_CACHE_STMT` | `EXECUTABLE_ID` |
| Db2 for z/OS | not implemented (dynamic statement cache needs `EXPLAIN STMTCACHE`) | — |

These are **cumulative** counters kept by the engine while the statement stays in its cache
(executions, total and max elapsed time, rows, logical/physical reads, CPU, temp usage, lock wait).

## Fingerprints and history

Every 5 minutes the collector reads the top `DBM_QUERY_STATS_TOP_N` statements, stores one
**fingerprint** row per statement and database in the control plane (normalized text, first/last
seen; up to `DBM_QUERY_FINGERPRINTS_MAX_PER_SERVER` per server) and writes the counters to the
time-series database keyed by fingerprint. Deltas between samples give executions per second and
mean latency per interval, which is what the statement history chart shows. Statement text is
parameterized by the engine (or masked by DBManager for roles without `query.view_text`); full
SQL is never used as a metric label.

## Why no P95/P99 per statement

The engines' cumulative statement statistics contain a count, a sum and (on most engines) a
maximum — not a distribution. A percentile cannot be derived from them, so DBManager shows
**mean** and **max** and does not invent percentiles. Percentile aggregations (`agg=p95` / `p99`)
*are* available for metric time series (for example p95 of `query.mean_latency_ms` over a range),
which is a percentile of interval means, not of individual executions.
