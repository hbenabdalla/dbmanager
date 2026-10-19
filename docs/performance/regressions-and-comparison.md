# Regressions, comparisons and outcomes

## Query regression detection

`GET /api/v1/servers/{id}/query-regressions` (**Queries → Regressions**, permission `query.view`)
compares, for every statement fingerprint, the mean latency of a recent window with the mean over a
baseline period, both computed from counter increases in the time-series database
(Δ total time ÷ Δ executions — the engines' own cumulative statistics, not samples).

A statement is reported as **slower** when, by default:

| Setting (Platform → Settings) | Default | Meaning |
|---|---|---|
| `performance.regression_recent_hours` | 1 | length of the recent window |
| `performance.baseline_days` | 7 | baseline period before the recent window |
| `performance.regression_threshold_pct` | 50 | minimum relative increase of the mean |
| `performance.regression_min_delta_ms` | 5 | minimum absolute increase (a 0.1 → 0.3 ms statement is not reported) |
| `performance.regression_min_calls` | 20 | minimum executions in **both** windows |

The page can widen the windows per request (`recent_hours`, `baseline_days`). Rule `GEN-PERF-011`
turns each regression into a recommendation (affected object `query:<fingerprint>`), resolved
automatically when the statement is back to normal.

### Plan changes

Plan identity is only reported where the engine's statement statistics carry it:

| Engine | Source | Detected change |
|---|---|---|
| Oracle | `V$SQLSTATS.PLAN_HASH_VALUE`: distinct plans and the plan with most elapsed time (`MAX … KEEP (DENSE_RANK LAST ORDER BY elapsed_time)`) | the dominant plan hash differs from the baseline's |
| SQL Server | `COUNT(DISTINCT query_plan_hash)` per `query_hash` in `sys.dm_exec_query_stats` | more cached plans than the baseline's median |
| PostgreSQL, MySQL, MariaDB, Db2 | — | not detectable: `pg_stat_statements`, `events_statements_summary_by_digest` and the package cache statistics carry no plan identity. The page says so. |

A plan change without a latency regression is listed (flag *plan changed*) but raises no finding.

## Period-vs-period comparison

`GET /api/v1/servers/{id}/query-compare` (**Queries → Compare periods**) compares statement activity
in a reference period **A** with a period **B** under review: executions, total and mean time,
rows, CPU and reads per statement, ranked by the change of total time. Presets:
last hour vs the hour before, last 24 h vs the 24 h before, last 24 h vs the same day a week ago,
last 7 days vs the week before; custom periods are limited to 31 days each.

Each statement is `slower` / `faster` (mean change ≥ 20 %), `similar`, `new` (no executions in A) or
`gone` (none in B). When a period has no statistics at all (before the collected history), the
statements are `unclassified` and the response says why — "new" would be misleading.

## Plan-vs-plan comparison

On **Queries → Execution plans**, *Save plan as* stores the plan of the next EXPLAIN (estimate or
analyze) with a label (`POST /servers/{id}/explain` with `save_label`; audited as
`query.plan_saved`). **Queries → Compare plans** (`GET /api/v1/plans/compare?a=…&b=…`) shows two
saved plans side by side with:

* total cost and its change (only between plans of the same engine — costs of different engines
  are not comparable),
* operator counts and whether the plan shape changed,
* access-path changes per table (for example `orders: Seq Scan → Index Scan (orders_customer_idx)`),
* operations only in A or only in B, plan issues that appeared or were resolved,
* the execution-time change for analyze plans.

Saved plans need `query.explain` on the server to list, view or compare; statement text is masked
without `query.view_text`. The author or a user with `performance.tune` can delete one
(`query.plan_deleted` in the audit log). Saved plans are platform data included in platform
backups (component *findings*).

## Recommendation validation (before / after)

When a recommendation is resolved — by a user, or automatically because its evidence disappeared —
DBManager measures whether the change helped:

| Finding | Measured |
|---|---|
| query findings (affected object `query:<fingerprint>`, e.g. `GEN-PERF-007`, `GEN-PERF-011`) | the statement's mean latency (lower is better) |
| `GEN-PERF-001` connection pressure, `-002` cache hit ratio (higher is better), `-003` blocking, `-004` deadlocks, `-005` replication lag, `-006` long transactions, `-008` temp spills, `-009` CPU, `-010` storage utilization | the triggering metric's average |
| other findings (configuration, security, engine-specific) | not measurable from the monitoring history: shown as such |

The *before* window (`performance.validation_window_hours`, default 24 h before resolution) is
measured at once; the job `validate_recommendations` (every 15 min) measures the *after* window once
it has elapsed and records `improved`, `unchanged` (change under 10 %), `regressed` or
`insufficient_data`. A finding that reappears is marked `reopened`. The result is shown in the
finding's dialog and on **Optimization → Outcomes**
(`GET /api/v1/recommendations/validation-summary`). Before/after measurements describe correlation
in time; other changes in the same window also influence them.
