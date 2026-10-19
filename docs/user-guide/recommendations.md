# Recommendations

**Optimization → Recommendations** lists performance, query and configuration advice produced by
deterministic rules over each server's metrics and evidence (see
[Performance recommendations](../performance/recommendations.md)).

Each recommendation shows severity, confidence, risk, the **evidence** it is based on (values and
time window), the suggested change (for example an SQL statement to review) and links to the
server. Status: *open*, *acknowledged*, *dismissed* (with a reason), *resolved* (set automatically
when the condition disappears).

Recommendations are **never executed automatically**. Applying one is a normal guarded change by a
DBA, subject to RBAC, confirmation and audit.

## Outcomes

When a recommendation is resolved, DB Manager measures the metric (or the statement latency) that
triggered it for a window before and after the resolution and shows the outcome — *improved*,
*unchanged*, *regressed* — in the recommendation's dialog and on **Optimization → Outcomes**. See
[recommendation validation](../performance/regressions-and-comparison.md#recommendation-validation-before-after).
