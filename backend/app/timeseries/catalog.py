"""Storage names, types and collection classes of the platform's normalized metrics.

The API and the UI keep using the platform's *metric keys* (``connections.total``); the
time-series storage uses Prometheus-style *metric names* (``db_connections``). This module is
the single mapping between the two. See ``docs/monitoring/metric-catalog.md``.

Naming rules (Prometheus / VictoriaMetrics conventions):

* prefix ``db_`` for telemetry of managed databases (``dbm_`` is reserved for the platform's
  own process metrics on ``/metrics``);
* snake_case, unit suffix (``_bytes``, ``_seconds``, ``_ms``, ``_percent``);
* ``_total`` only for counters; ``_per_second`` for rates derived by the collector.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.timeseries.model import MetricType

G, C, R, D = MetricType.GAUGE, MetricType.COUNTER, MetricType.RATE, MetricType.DISTRIBUTION


class IntervalClass(StrEnum):
    FAST = "fast"  # 15–30 s: connections, sessions, locks, waits
    NORMAL = "normal"  # 60 s: general performance
    SLOW = "slow"  # 5–15 min: storage, capacity, query statistics
    VERY_SLOW = "very_slow"  # hourly+: inventory, security posture


@dataclass(frozen=True)
class MetricDef:
    key: str
    name: str
    type: MetricType
    unit: str
    interval: IntervalClass
    labels: tuple[str, ...] = ()
    engines: str = "all"


_DEFS: list[MetricDef] = [
    # connections & sessions (fast)
    MetricDef("connections.total", "db_connections", G, "count", IntervalClass.FAST),
    MetricDef("connections.active", "db_connections_active", G, "count", IntervalClass.FAST),
    MetricDef("connections.idle", "db_connections_idle", G, "count", IntervalClass.FAST),
    MetricDef("connections.idle_in_transaction", "db_connections_idle_in_transaction", G, "count", IntervalClass.FAST),
    MetricDef("connections.max", "db_connections_max", G, "count", IntervalClass.SLOW),
    MetricDef("connections.utilization", "db_connections_utilization_percent", G, "%", IntervalClass.FAST),
    MetricDef("sessions.blocked", "db_sessions_blocked", G, "count", IntervalClass.FAST),
    MetricDef("sessions.waiting", "db_sessions_waiting", G, "count", IntervalClass.FAST),
    MetricDef("locks.total", "db_locks", G, "count", IntervalClass.FAST),
    MetricDef("locks.waiting", "db_locks_waiting", G, "count", IntervalClass.FAST),
    MetricDef("wait.active_sessions", "db_wait_active_sessions", G, "count", IntervalClass.FAST, ("wait_type",)),
    MetricDef("transactions.longest_seconds", "db_transaction_longest_seconds", G, "s", IntervalClass.FAST),
    MetricDef("query.longest_running_seconds", "db_query_longest_running_seconds", G, "s", IntervalClass.FAST),
    # throughput (normal) — rates derived from counters, plus the raw counters
    MetricDef("transactions.per_sec", "db_transactions_per_second", R, "1/s", IntervalClass.NORMAL),
    MetricDef("commits.per_sec", "db_commits_per_second", R, "1/s", IntervalClass.NORMAL),
    MetricDef("rollbacks.per_sec", "db_rollbacks_per_second", R, "1/s", IntervalClass.NORMAL),
    MetricDef("query.calls_per_sec", "db_query_calls_per_second", R, "1/s", IntervalClass.NORMAL),
    MetricDef("io.blocks_read_per_sec", "db_io_blocks_read_per_second", R, "1/s", IntervalClass.NORMAL),
    MetricDef("temp.bytes_per_sec", "db_temp_bytes_per_second", R, "B/s", IntervalClass.NORMAL),
    MetricDef("rows.returned.per_sec", "db_rows_returned_per_second", R, "1/s", IntervalClass.NORMAL),
    MetricDef("rows.modified.per_sec", "db_rows_modified_per_second", R, "1/s", IntervalClass.NORMAL),
    MetricDef("deadlocks.count", "db_deadlocks_delta", G, "count", IntervalClass.NORMAL),
    MetricDef("transactions.commits", "db_transactions_commits_total", C, "count", IntervalClass.NORMAL),
    MetricDef("transactions.rollbacks", "db_transactions_rollbacks_total", C, "count", IntervalClass.NORMAL),
    MetricDef("io.blocks_read", "db_io_blocks_read_total", C, "count", IntervalClass.NORMAL),
    MetricDef("io.blocks_hit", "db_io_blocks_hit_total", C, "count", IntervalClass.NORMAL),
    MetricDef("temp.bytes", "db_temp_bytes_total", C, "B", IntervalClass.NORMAL),
    MetricDef("temp.files", "db_temp_files_total", C, "count", IntervalClass.NORMAL),
    MetricDef("rows.returned", "db_rows_returned_total", C, "count", IntervalClass.NORMAL),
    MetricDef("rows.modified", "db_rows_modified_total", C, "count", IntervalClass.NORMAL),
    MetricDef("query.calls", "db_query_calls_engine_total", C, "count", IntervalClass.NORMAL),
    MetricDef("query.total_ms", "db_query_time_engine_ms_total", C, "ms", IntervalClass.NORMAL),
    MetricDef("deadlocks", "db_deadlocks_total", C, "count", IntervalClass.NORMAL),
    # efficiency & latency (normal)
    MetricDef("cache.hit_ratio", "db_cache_hit_ratio_percent", G, "%", IntervalClass.NORMAL),
    MetricDef("query.mean_latency_ms", "db_query_latency_mean_ms", D, "ms", IntervalClass.NORMAL),
    # host / instance resources (normal)
    MetricDef("cpu.percent", "db_cpu_percent", G, "%", IntervalClass.NORMAL),
    MetricDef("memory.used_bytes", "db_memory_used_bytes", G, "B", IntervalClass.NORMAL),
    MetricDef("memory.total_bytes", "db_memory_total_bytes", G, "B", IntervalClass.SLOW),
    # availability & replication
    MetricDef("availability.up", "db_up", G, "bool", IntervalClass.NORMAL),
    MetricDef("uptime.seconds", "db_uptime_seconds", G, "s", IntervalClass.SLOW),
    MetricDef("replication.is_replica", "db_replication_is_replica", G, "bool", IntervalClass.SLOW),
    MetricDef("replication.lag_seconds", "db_replication_lag_seconds", G, "s", IntervalClass.NORMAL),
    MetricDef("replication.replica_count", "db_replication_replicas", G, "count", IntervalClass.SLOW),
    # storage & capacity (slow)
    MetricDef("storage.database_bytes", "db_storage_database_bytes", G, "B", IntervalClass.SLOW),
    MetricDef("storage.capacity_bytes", "db_storage_capacity_bytes", G, "B", IntervalClass.SLOW),
    MetricDef("storage.utilization", "db_storage_utilization_percent", G, "%", IntervalClass.SLOW),
    # per database / tablespace sizes, written by the hourly inventory discovery (capacity forecasting)
    MetricDef("storage.db_size_bytes", "db_database_size_bytes", G, "B", IntervalClass.VERY_SLOW, ("database",)),
    MetricDef(
        "storage.tablespace_used_bytes", "db_tablespace_used_bytes", G, "B", IntervalClass.VERY_SLOW, ("tablespace",)
    ),
    MetricDef(
        "storage.tablespace_max_bytes", "db_tablespace_max_bytes", G, "B", IntervalClass.VERY_SLOW, ("tablespace",)
    ),
    # engine specific
    MetricDef(
        "pg.idle_in_transaction_longest_seconds",
        "db_pg_idle_in_transaction_longest_seconds",
        G,
        "s",
        IntervalClass.FAST,
        engines="postgresql",
    ),
    MetricDef("oracle.sga_used_bytes", "db_oracle_sga_used_bytes", G, "B", IntervalClass.SLOW, engines="oracle"),
    MetricDef("oracle.pga_used_bytes", "db_oracle_pga_used_bytes", G, "B", IntervalClass.NORMAL, engines="oracle"),
    MetricDef(
        "db2.bufferpool_hit_ratio", "db_db2_bufferpool_hit_ratio_percent", G, "%", IntervalClass.NORMAL, engines="db2"
    ),
    MetricDef("db2.lock_escalations", "db_db2_lock_escalations_delta", G, "count", IntervalClass.NORMAL, engines="db2"),
    MetricDef(
        "mssql.page_life_expectancy",
        "db_mssql_page_life_expectancy_seconds",
        G,
        "s",
        IntervalClass.NORMAL,
        engines="mssql",
    ),
    MetricDef(
        "mssql.batch_requests_per_sec",
        "db_mssql_batch_requests_per_second",
        R,
        "1/s",
        IntervalClass.NORMAL,
        engines="mssql",
    ),
    MetricDef(
        "mysql.threads_running", "db_mysql_threads_running", G, "count", IntervalClass.FAST, engines="mysql, mariadb"
    ),
    MetricDef(
        "mysql.tmp_disk_tables_per_sec",
        "db_mysql_tmp_disk_tables_per_second",
        R,
        "1/s",
        IntervalClass.NORMAL,
        engines="mysql, mariadb",
    ),
]

# query telemetry (one series per server × database × fingerprint), written every query-stats
# collection (slow class). Cumulative counters as reported by the engine plus two distribution
# statistics; per-interval values are computed at query time with increase()/rate().
QUERY_METRICS: list[MetricDef] = [
    MetricDef("calls", "db_query_calls_total", C, "count", IntervalClass.SLOW, ("database", "fingerprint")),
    MetricDef("total_ms", "db_query_exec_time_ms_total", C, "ms", IntervalClass.SLOW, ("database", "fingerprint")),
    MetricDef("rows", "db_query_rows_total", C, "count", IntervalClass.SLOW, ("database", "fingerprint")),
    MetricDef("cpu_ms", "db_query_cpu_time_ms_total", C, "ms", IntervalClass.SLOW, ("database", "fingerprint")),
    MetricDef(
        "logical_reads", "db_query_logical_reads_total", C, "count", IntervalClass.SLOW, ("database", "fingerprint")
    ),
    MetricDef(
        "physical_reads", "db_query_physical_reads_total", C, "count", IntervalClass.SLOW, ("database", "fingerprint")
    ),
    MetricDef("temp_blocks", "db_query_temp_blocks_total", C, "count", IntervalClass.SLOW, ("database", "fingerprint")),
    MetricDef("lock_wait_ms", "db_query_lock_wait_ms_total", C, "ms", IntervalClass.SLOW, ("database", "fingerprint")),
    MetricDef("mean_ms", "db_query_mean_ms", D, "ms", IntervalClass.SLOW, ("database", "fingerprint")),
    MetricDef("max_ms", "db_query_max_ms", D, "ms", IntervalClass.SLOW, ("database", "fingerprint")),
    # plan identity (Oracle, SQL Server): cached plan count and the dominant plan's hash
    MetricDef("plans", "db_query_plans", G, "count", IntervalClass.SLOW, ("database", "fingerprint")),
    MetricDef("plan_id", "db_query_plan_id", G, "", IntervalClass.SLOW, ("database", "fingerprint")),
]

BY_KEY: dict[str, MetricDef] = {d.key: d for d in _DEFS}
BY_NAME: dict[str, MetricDef] = {d.name: d for d in _DEFS}
QUERY_BY_FIELD: dict[str, MetricDef] = {d.key: d for d in QUERY_METRICS}
QUERY_BY_NAME: dict[str, MetricDef] = {d.name: d for d in QUERY_METRICS}


def name_for(key: str) -> str:
    """Storage name of a metric key; unknown (engine-specific) keys follow the generic rule."""
    d = BY_KEY.get(key)
    if d:
        return d.name
    base = key.lower()
    if base.endswith(".per_sec"):
        base = base.removesuffix(".per_sec") + ".per_second"
    return "db_" + "".join(ch if ch.isalnum() else "_" for ch in base)


def key_for(name: str) -> str:
    d = BY_NAME.get(name)
    if d:
        return d.key
    return name.removeprefix("db_").replace("_", ".", 1)


def definition(key: str) -> MetricDef:
    return BY_KEY.get(key) or MetricDef(key, name_for(key), G, "", IntervalClass.NORMAL)


def all_definitions() -> list[MetricDef]:
    return [*_DEFS, *QUERY_METRICS]
