# Performance

Performance analysis in DB Manager combines metric history, statement statistics, plans, waits and
locks, and deterministic rules. **Engines do not expose identical data** — each page states what
comes from where, and what is missing on a given engine is shown as unavailable, never estimated.

| Topic | Page |
|---|---|
| Statement fingerprints, statistics, why there is no P95/P99 per statement | [Query statistics](query-statistics.md) |
| Estimated and actual plans, plan issues | [Execution plans](execution-plans.md) |
| Wait events and active sessions by wait class | [Waits](waits.md) |
| Blocking chains, deadlocks, cancel and terminate | [Locks and blocking](locks-and-blocking.md) |
| Rule engine, evidence, confidence, risk | [Recommendations](recommendations.md) |
| Statistical baselines, z-score alert rules, storage growth forecasts with confidence bands | [Baselines and capacity](baselines-and-capacity.md) |
| Query regressions and plan changes, period and plan comparison, before/after outcomes of recommendations | [Regressions, comparisons and outcomes](regressions-and-comparison.md) |
