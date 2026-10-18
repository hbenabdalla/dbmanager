# Health checks, metrics and logs

| Endpoint | Use |
|---|---|
| `GET /healthz` | liveness (process up) |
| `GET /readyz` | readiness (application DB reachable, migrations applied → `schema_version`) with per-component status `application`, `postgresql`, `timeseries`; a time-series outage reports `degraded: true` without making the backend unready |
| `GET /metrics` | Prometheus: `dbm_http_requests_total`, `dbm_http_request_duration_seconds`, `dbm_collector_jobs_total`, `dbm_collector_job_duration_seconds`, `dbm_collector_inflight`, `dbm_target_connections_open`, `dbm_scheduler_leader`, `dbm_app_db_pool_checked_out`, `dbm_login_attempts_total`, and the telemetry pipeline: `dbm_metrics_collected_total`, `dbm_metrics_written_total`, `dbm_metrics_write_failures_total`, `dbm_metrics_dropped_total`, `dbm_metrics_buffer_pending`, `dbm_metrics_batch_size`, `dbm_metrics_write_latency_seconds`, `dbm_timeseries_query_latency_seconds` |

Logs are JSON lines on stdout with `request_id` and `user`; ship them with your log collector.
An OpenTelemetry collector can scrape `/metrics` with its Prometheus receiver.

**Platform → Monitoring storage** shows the time-series database: status, version, series and
samples, oldest/newest metric, retention in effect vs. policy, ingestion (written, buffered,
failed, dropped, last success/failure) and pending deletions.

**Platform → Collectors** shows the scheduler leader, pending jobs, failures per job kind and servers
whose collection is paused by the circuit breaker (after 3 consecutive connection failures, back-off
grows from 1 minute to 1 hour; updating its credentials or **Collect now** resets it).

| Where | Liveness | Readiness |
|---|---|---|
| Backend image (`HEALTHCHECK`) | `/healthz` | — |
| Docker Compose backend service | — | `/readyz` (the frontend waits for it) |
| Kubernetes backend / worker | `/healthz` | `/readyz` |
| Frontend (image, Compose, Kubernetes) | `/nginx-health` | `/nginx-health` |
| VictoriaMetrics | `/health` | `/health` |
Per-component health is also shown in **Platform → Diagnostics** ([Diagnostics](../administration/diagnostics.md)).
