# Adding metrics and rules

## Metrics

1. **Define the metric** in `backend/app/timeseries/catalog.py`:

    ```python
    MetricDef("connections.total", "db_connections", G, "count", IntervalClass.FAST),
    ```

    Fields: platform key, storage name (Prometheus style, `db_` prefix, unit suffix, `_total` only
    for counters, `_per_second` for derived rates), type (gauge, counter, rate, distribution), unit,
    interval class (`FAST`, `NORMAL`, `SLOW`, `VERY_SLOW`), extra labels and engines.
2. **Describe it** for the UI in `backend/app/services/metric_catalog.py` (`CATALOG`: label, unit,
    category, description).
3. **Report it** from the adapter's `get_performance_metrics()` in base units. Report cumulative
    counters raw; `derive()` turns them into `<key>.per_sec` rates (or the names in
    `RATE_DERIVATIONS` / `DELTA_DERIVATIONS`), and computes `cache.hit_ratio`, `cpu.percent` and
    `query.mean_latency_ms` from their counters. Counters in `SILENT_COUNTERS` are inputs only.
    A counter that goes backwards is treated as a reset; gaps over an hour produce no rate.
4. **Document it** in the [metric catalog](../monitoring/metric-catalog.md).

`dbm_` names are reserved for the platform's own process metrics on `/metrics`. Never put SQL text,
user names or other unbounded values into labels.

## Rules

Rules are plain functions registered with a decorator (`backend/app/advisors/engine.py`):

```python
from app.advisors.engine import EvidenceContext, RuleFinding, rule

@rule("PG-PERF-099", engines=("postgresql",), kind="performance",
      category="vacuum", severity="medium", title="Short title")
def my_rule(ctx: EvidenceContext) -> list[RuleFinding]:
    """Description shown in the rule catalog."""
    m = ctx.m("connections.utilization")      # MetricWindow: avg/min/max/last/count/p95
    if not m or m.count < 3:
        return []                              # not enough evidence: no finding
    return [RuleFinding(
        title="…", finding="…", evidence={"peak_pct": m.max},
        recommendation="…", risk="…", confidence=0.7,
    )]
```

| Input on `EvidenceContext` | Source |
|---|---|
| `metrics` / `m(name)` | windows over the analysis period (6 h) from the time-series database |
| `latest` | latest snapshot values |
| `sustained` | results of `SUSTAINED_CHECKS` (`app/services/advisor.py`) |
| `query_stats` | latest statement statistics |
| `tuning` / `security` | adapter evidence (`get_tuning_context()` / `get_security_context()`) |
| `capabilities`, `environment_tier`, `tls_mode`, `monitoring_username` | server facts |

Rules must:

* return nothing when the evidence is missing (`None` means "not visible", not "zero");
* put the observed values in `evidence` and set an honest `confidence` (0–1) and `risk`;
* never change the target database; `remediation` SQL is shown for review only;
* use `affected_object` / `database` so the fingerprint (`sha256(rule|server|database|object)`)
  identifies one finding.

A rule that raises is logged and skipped; the others still run. Add the module to the package's
imports (`app/advisors/performance/__init__.py` or `security/__init__.py`) so the decorator
registers, add unit tests in `tests/unit/test_rules.py`, and list the rule on the engine page.
