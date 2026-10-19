# Baselines and capacity

## Baselines

`GET /api/v1/servers/{id}/metrics/baseline?metric=…&days=7` computes, from the stored history of one
metric: mean, standard deviation, min, max, p50, p95, p99 and the sample count, and compares the
current value (z-score). With fewer than 60 samples the response says `sufficient_data: false`
instead of returning a misleading baseline.

### Baseline-relative alert rules (z-score)

A metric alert rule with **Evaluate → Deviation from baseline (z-score)** (`aggregation: "zscore"`)
compares a server with *its own* history instead of a fixed number:

| Field | Meaning |
|---|---|
| `baseline_days` | days of history the baseline (mean, standard deviation) is computed from, 1–90 (default 7) |
| `window_seconds` | `0` compares the latest collected value; ≥ 60 compares the average over that window |
| `operator`, `threshold` | applied to the z-score: `> 3` fires on unusually high values, `< -3` on unusually low ones |

* The recent window is excluded from the baseline, so the condition being tested does not dilute
  its own baseline.
* With fewer than 60 baseline samples, or no variation (standard deviation 0), the rule reports
  *no data* and keeps the alert state unchanged: nothing fires from an untrustworthy baseline.
* Baselines are computed by the time-series database for all servers of a rule in one query each
  (mean, standard deviation, count) and cached for 10 minutes; they move slowly.
* The alert's value is the z-score; its message gives the current value and the baseline
  (`z-score of avg(cpu.percent, 10m) = 4.2 (> 3): current 92, baseline 41 ± 12 over 7d`).

The rule wizard shows the baseline the rule would use for a server-scoped rule before it is saved.
See [Alerting](../user-guide/alerting.md).

## Capacity forecasting

`GET /api/v1/servers/{id}/capacity-forecast?scope=server|database|tablespace&days=30&horizon_days=90`
and the fleet view `GET /api/v1/capacity/forecast?scope=…` (**Monitoring → Capacity forecast**)
forecast storage growth from the daily last value of each size series:

| Scope | Size series | Capacity basis |
|---|---|---|
| `server` | `storage.database_bytes` (storage collector) | `storage.capacity_bytes`, where the engine or host reports it |
| `database` | `storage.db_size_bytes{database}` (hourly inventory discovery) | the database's size plus the server's free capacity: the date it would fill the shared volume if it were the only one growing |
| `tablespace` | `storage.tablespace_used_bytes{tablespace}` (hourly discovery) | `storage.tablespace_max_bytes` where the engine reports a maximum (Oracle autoextend limit) or the tablespace cannot grow (Db2 without automatic resize) |

The trend is a **Theil–Sen** estimate (median of pairwise slopes), which a one-off bulk load or
purge does not drag the way a least-squares line would. The result carries:

* `growth_bytes_per_day` and its **90 % interval** (`growth_low`, `growth_high`, Sen's rank-based
  confidence interval of the slope);
* a forecast **band** over the horizon: the slope interval widened by the 90th percentile of the
  absolute residuals around the trend;
* `days_to_full`: `earliest` (upper slope bound and band — the cautious planning date), `likely`
  (median slope) and `latest` (lower slope bound; empty when the lower bound does not grow).

Nothing is forecast from less than 7 days of history (at least 5 daily points), from a series that
is not growing, or without a capacity: the reason is returned instead. Rule `GEN-PERF-012` raises a
finding when the earliest reading falls within `performance.forecast_warning_days` (default 30).

Tablespace sizes come from `get_storage_metrics()` during discovery and need the same privileges as
the Storage tab (Oracle `DBA_TABLESPACE_USAGE_METRICS`, Db2 `MON_GET_TABLESPACE`); at most 50
tablespaces per server are recorded (MySQL file-per-table spaces can be numerous). SQL Server
reports no tablespaces (filegroups are not recorded); MySQL and PostgreSQL report no tablespace
maximum.

The older `GET /api/v1/metrics/capacity` (server scope, least-squares fit, used by the dashboard's
Capacity card and the Capacity report) is unchanged. Rule `GEN-PERF-010` (storage approaching
capacity) uses current utilization.
