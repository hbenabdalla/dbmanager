# Current milestone

| | |
|---|---|
| **Milestone** | 0.13 — Performance Intelligence |
| **Status** | DONE — released as **0.13.0** |
| **Release target** | 0.13.0 (this release). Next: milestone 0.14 — Reporting (NEXT) |

## Objective

Turn the monitoring history DB Manager already stores into answers: is this server behaving
unusually *for itself*, which statement got slower and did its plan change, when will storage
fill, what changed between two periods or two plans, and did a resolved recommendation actually
help. Everything is computed from the time-series history and statement statistics; nothing is
extrapolated from thin data, and every result says why it is absent.

## Sprints

| Sprint | Scope | Status |
|---|---|---|
| 0.13.1 Baselines in alerts | z-score alert rules against each server's own history (baseline days, minimum samples, recent window excluded, cached baselines), wizard with baseline preview | DONE |
| 0.13.2 Query regression detection | Per-fingerprint mean latency, recent window vs baseline with thresholds in platform settings; plan changes for Oracle (plan hash) and SQL Server (cached plan count); rule `GEN-PERF-011`; Queries → Regressions | DONE |
| 0.13.3 Capacity forecasting | Theil–Sen trend, Sen 90 % interval, residual band, days to full (earliest / likely / latest) per server, database and tablespace; sizes recorded by the hourly discovery; rule `GEN-PERF-012`; Monitoring → Capacity forecast | DONE |
| 0.13.4 Query comparison | Period vs period (presets, custom periods) and plan vs plan of saved plans (audited save and delete) | DONE |
| 0.13.5 Recommendation validation | Before/after measurement of the triggering metric or statement latency when a recommendation is resolved; Optimization → Outcomes | DONE |

## Dependencies

* Time-series history (VictoriaMetrics) — baselines, regressions, forecasts and validations need
  the history to be retained for at least the baseline / forecast period.
* Statement statistics with plan identity — only Oracle (`V$SQLSTATS`) and SQL Server
  (`sys.dm_exec_query_stats`) expose it; plan changes are not claimed for the other engines.
* Hourly inventory discovery for per-database and per-tablespace sizes (history starts with this
  release: forecasts appear after 7 days).
* Migration `0011` (applied automatically on upgrade).

## Acceptance criteria

| Criterion | Evidence |
|---|---|
| A z-score rule fires on a value far from the server's own baseline and reports *no data* below 60 samples or without variation | `tests/api/test_performance.py::test_zscore_alert_rule`, `tests/unit/test_perf_intel.py` |
| A statement slower than its baseline (relative and absolute thresholds, minimum executions) is detected and raises `GEN-PERF-011`; a plan change is reported where the engine exposes plan identity | unit tests with plan ids, API test through the advisor; real-engine assertions for Oracle and SQL Server plan identity |
| Forecasts are robust to outliers, carry a 90 % band and refuse thin history, flat trends and unknown capacity with a reason | `test_theil_sen_ignores_a_one_off_outlier`, `test_forecast_refuses_thin_history_and_reports_why`, `test_capacity_forecast_per_database_and_tablespace` |
| Period comparison classifies statements, refuses to classify when a period has no data; plan comparison shows access-path changes and does not compare costs across engines | `test_compare_periods_classifies_statements`, `test_compare_plans_reports_access_path_changes`, API tests |
| A resolved recommendation gets a before/after verdict; unmeasurable findings say so; a reopened finding is marked | `test_query_recommendation_validation_before_and_after`, `test_recommendation_validation_lifecycle` |
| RBAC and audit: regressions and comparisons need `query.view`; forecasts `performance.view`; saved plans `query.explain`, deletion by the author or `performance.tune`; plan save/delete audited | `test_query_regressions_and_rbac`, `test_saved_plans_compare_and_delete`, `test_capacity_forecasts` |
| Upgrade from 0.12.0 keeps all data | `tests/migrations/test_migrations.py::test_existing_installation_upgrades_to_head_with_its_data` |

## Next

Milestone **0.14 — Reporting** (NEXT): security-findings and query-statistics reports, scheduled
delivery by SMTP, PDF export. See the [roadmap](roadmap.md).
