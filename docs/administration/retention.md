# Retention and downsampling

## Retention

| Data | Where | Kept for | Enforced by |
|---|---|---|---|
| Metric history, query telemetry | time-series DB | `DBM_TIMESERIES_RETENTION` (default 90 days) | VictoriaMetrics `-retentionPeriod` (drops whole monthly/daily partitions; no delete load) |
| Query fingerprint metadata | PostgreSQL `query_fingerprints` | while seen within the retention policy | `retention` job deletes rows not seen for `monitoring.retention_days` |
| Legacy metric tables | PostgreSQL | `DBM_METRICS_RETENTION_DAYS` (35) | `retention` job (until phase F drops them) |
| Collector job history | PostgreSQL | `DBM_JOB_HISTORY_RETENTION_DAYS` | `retention` job |
| Recommendations, findings, audit | PostgreSQL | not time-limited | — |

The **policy** is stored in PostgreSQL (`app_settings.monitoring.retention_days`, audited,
editable under Platform → Configuration → Monitoring storage or Platform → Configuration → Settings). It is **enforced**
natively by the time-series database, which takes its retention as a start-up flag. The
status page shows both values; when they differ it says so and how to apply it:

```bash
# .env
DBM_TIMESERIES_RETENTION=180d
docker compose up -d timeseries     # recreates only the time-series container; data is kept
```

Valid values: `30d`, `90d`, `26w`, `1y`, … Shortening the retention deletes older
partitions at the next cleanup; lengthening keeps everything that still exists.

## Downsampling strategy

The open-source VictoriaMetrics stores raw samples only (stored downsampling is an
Enterprise feature). KBM DBManager therefore downsamples **at query time**:

* every range request picks a step so that a series has at most 240 points (configurable
  10–1,000 per request); the database computes the aggregation of all raw samples inside
  each step (`avg_over_time`, `max_over_time`, `quantile_over_time`, `rate`, …);
* the result is identical in meaning to pre-aggregated rollups — a 90-day chart shows
  12-hour averages (or p95, max …) computed from every raw sample;
* cost: a 90-day query for one server scans ~130 k samples per metric, answered in under
  30 ms in this project's measurements; fleet-wide 90-day aggregates scan proportionally
  more but remain sub-second at 1,000 servers.

Why raw-only is acceptable: at 1–3 bytes per sample a full year of 1-minute data for 1,000
servers is ~70–220 GB.

If storage must shrink further, options in order of effort:

1. Shorter retention for the raw store plus a second VictoriaMetrics instance with long
   retention fed by `vmagent` stream aggregation (e.g. 5-minute `avg`/`max`/`p95`), and a
   repository that routes ranges over 30 days to it — the repository interface already
   isolates this.
2. VictoriaMetrics Enterprise `-downsampling.period=30d:5m,180d:1h`.
3. Raise `collection_interval_s` for non-production servers.
