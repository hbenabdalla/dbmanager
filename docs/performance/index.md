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
| Statistical baselines and storage growth forecasts | [Baselines and capacity](baselines-and-capacity.md) |
