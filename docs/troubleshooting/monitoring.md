# Monitoring problems

| Symptom | Check |
|---|---|
| No metrics for a new server | the first collection runs within one interval (plus jitter); **Collect now** queues one immediately. Overview → Collection status shows each component's last result |
| Some charts empty, others fine | the engine or the account does not provide that metric — it is reported as *unavailable* with a reason rather than estimated ([Metric catalog](../monitoring/metric-catalog.md)) |
| Rates and cache hit ratio missing right after start | rates need two consecutive samples; after a gap of more than an hour or a counter reset, the next sample starts over |
| *Collection paused after repeated failures* | circuit breaker after 3 consecutive failures ([Collectors](../monitoring/collectors.md#circuit-breaker)) |
| No collection at all | **Platform → Collectors**: is a worker running and does one hold the scheduler lock? With `DBM_PROCESS_ROLE=api` no worker runs in that process — start `python -m app.worker` |
| Charts and history fail with `timeseries_unavailable` (503) | the time-series database is down or unreachable: `docker compose ps timeseries`, `docker compose logs timeseries`; `/readyz` reports it `degraded`. Latest values and health keep working; writes are buffered and retried ([Pipeline](../monitoring/pipeline.md)) |
| Ingestion shows dropped samples | **Platform → Monitoring storage**: the in-memory write buffer (`DBM_METRIC_BUFFER_MAX`, 200 000 samples, oldest dropped first) overflowed during a long outage; check storage capacity and `dbm_metrics_dropped_total` |
| History shorter than expected | retention in effect (`DBM_TIMESERIES_RETENTION`) vs. policy on **Platform → Monitoring storage** ([Retention](../administration/retention.md)) |
| No recommendations yet | performance rules need history: they run every 15 minutes over the last 6 hours and require a minimum number of samples |
| Security findings missing | the security scan runs every 6 hours; trigger one with `POST /api/v1/servers/{id}/security-scan` |
