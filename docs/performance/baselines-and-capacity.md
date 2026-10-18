# Baselines and capacity

## Baselines

`GET /api/v1/servers/{id}/metrics/baseline?metric=…&days=7` computes, from the stored history of one
metric: mean, standard deviation, min, max, p50, p95, p99 and the sample count, and compares the
current value (z-score). With fewer than 60 samples the response says `sufficient_data: false`
instead of returning a misleading baseline.

## Capacity

`GET /api/v1/metrics/capacity` (and `/servers/{id}/metrics/capacity`) estimates storage growth from
the daily last value of `storage.database_bytes` over 30 days with a least-squares fit. A
*days until full* forecast is returned only when:

* the history spans at least 7 days with at least 5 daily points,
* the trend is growing, and
* a capacity (`storage.capacity_bytes`) is known.

Otherwise the reason is returned. The dashboard's Capacity card and the Capacity report use the
same data. Rule `GEN-PERF-010` (storage approaching capacity) uses current utilization.
