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
