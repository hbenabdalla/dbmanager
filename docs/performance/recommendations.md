# Recommendation engine

Recommendations come from **deterministic rules** (`backend/app/advisors/performance/`): no
machine-learning model and no external service. Every rule is scoped to engines (`engines=(...)`,
`*` for common rules) and evaluates an evidence context built by the adapter
(`get_tuning_context()`) plus metric windows and *sustained-condition* checks over the last 6 hours
of time-series history (currently connection utilization above 85 % for 15 minutes, `SUSTAINED_CHECKS` in `app/services/advisor.py`).

A finding carries:

| Field | Meaning |
|---|---|
| Severity | critical, high, medium, low, info |
| Evidence | the observed values and window that triggered it |
| Confidence | 0–1, lower when the evidence is short or indirect |
| Risk | what can go wrong when applying the advice |
| Suggested action | text and, where meaningful, an SQL statement to review |

Findings are de-duplicated by a fingerprint (rule, server, database, object); a finding whose
condition disappears is resolved automatically. Rules run every 15 minutes. Recommendations are
**never executed** by DBManager.

The rule catalog is available at `GET /api/v1/advisor/rules`; the rules per engine are listed on
each [engine page](../database-engines/index.md). Adding rules: [Adding metrics and rules](../development/adding-metrics-and-rules.md).
