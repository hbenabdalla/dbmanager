"""Prometheus metrics describing the platform itself (not the managed fleet)."""

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("dbm_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "dbm_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
COLLECTOR_JOBS = Counter("dbm_collector_jobs_total", "Collector jobs processed", ["kind", "result"])
COLLECTOR_DURATION = Histogram(
    "dbm_collector_job_duration_seconds",
    "Collector job duration",
    ["kind"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
COLLECTOR_INFLIGHT = Gauge("dbm_collector_inflight", "Collector jobs currently executing")
TARGET_CONNECTIONS = Gauge("dbm_target_connections_open", "Open connections to managed databases")
SCHEDULER_LEADER = Gauge("dbm_scheduler_leader", "1 if this process holds the scheduler leadership")
APP_DB_POOL = Gauge("dbm_app_db_pool_checked_out", "Application DB connections checked out")
LOGIN_ATTEMPTS = Counter("dbm_login_attempts_total", "Login attempts", ["source", "result"])
RATE_LIMITED = Counter("dbm_rate_limited_total", "Requests refused by a rate limit", ["policy"])

# --- monitoring telemetry pipeline (time-series storage) ----------------------------------------
METRICS_COLLECTED = Counter("dbm_metrics_collected_total", "Samples produced by collectors and normalized")
METRICS_WRITTEN = Counter("dbm_metrics_written_total", "Samples accepted by the time-series storage")
METRICS_WRITE_FAILURES = Counter("dbm_metrics_write_failures_total", "Failed batch writes (retried)")
METRICS_DROPPED = Counter("dbm_metrics_dropped_total", "Samples dropped (buffer overflow or label policy)", ["reason"])
METRICS_BUFFERED = Gauge("dbm_metrics_buffer_pending", "Samples waiting in the write buffer")
METRICS_BATCH_SIZE = Histogram(
    "dbm_metrics_batch_size", "Samples per batch write", buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000, 10000)
)
METRICS_WRITE_LATENCY = Histogram(
    "dbm_metrics_write_latency_seconds",
    "Batch write latency",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
TIMESERIES_QUERY_LATENCY = Histogram(
    "dbm_timeseries_query_latency_seconds",
    "Latency of requests to the time-series storage",
    ["op"],
    buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
