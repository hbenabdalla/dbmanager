# Alerting

DB Manager raises alerts when a rule's condition holds on a monitored server, shows them under **Operations → Alerts**, and optionally sends notifications through [notification channels](../administration/notifications.md).

## How evaluation works

A background job (`evaluate_alerts`) runs every `DBM_ALERT_EVALUATION_SECONDS` (default 60, minimum 15). For every enabled rule and every monitored server in the rule's scope it checks the **latest collected values** (the collector's per-server cache), the server health state, or the collector state:

| Rule type | Fires when |
|---|---|
| Metric threshold | `metric operator threshold` holds, e.g. `connections.utilization >= 90`. Metrics are picked from the metric catalog. A value older than three collection intervals counts as *no data* and neither fires nor resolves. |
| Server health state | the server health is in the chosen states (e.g. `unavailable`, `critical`). |
| Collector failing | a collector component reports `failed`/`unavailable`, or collection failed the given number of times in a row (default: the circuit-breaker threshold). |

With **For (seconds)** the condition must hold for that long: the alert is *pending* first and becomes *firing* once the duration has elapsed. A pending alert whose condition clears is dropped without notification.

## Lifecycle

`pending → firing → acknowledged → resolved`

- There is at most one active alert per rule and server (de-duplication); repeated evaluations update its value and message.
- **Acknowledge** (permission `alerts.acknowledge`) records who is handling it, with an optional comment. It stays active but no further notifications are sent.
- **Resolve** closes it manually; it fires again at the next evaluation if the condition still holds.
- Alerts **auto-resolve** (`resolved_by = auto`) when the condition clears; a *resolved* notification is sent when the rule has *Notify on resolve*.
- Disabling or deleting a rule closes its alerts.

Alerts on simulated servers are marked **Simulated**; channels do not send them unless the channel explicitly enables *Also send for simulated servers*.

## Maintenance windows

**Operations → Alerts → Maintenance windows** schedules a window for all servers, one environment or one server. Alerts on servers inside an active window are still raised but marked **Suppressed (maintenance)**, and no notifications are sent (the delivery log records them as *skipped*).

## Rules

**Operations → Alerts → Alert rules** (manage with `alerts.manage`): the dialog walks through *Condition* (type, metric, operator, threshold, duration, severity), *Scope* (all servers, one environment, one server; enabled) and *Notify* (channels, notify on resolve).

Example rules:

- Connection utilization `>= 90` for 300 s, severity high, all servers.
- Health in `unavailable` for 60 s, severity critical, environment *Production*.
- Replication lag `> 120` s for 300 s, severity medium.

## Permissions

| Permission | Allows | Default roles |
|---|---|---|
| `alerts.view` | see alerts of servers in the user's scope, rules and windows | all built-in roles |
| `alerts.acknowledge` | acknowledge and resolve alerts of servers in scope | Platform Administrator, DBA Administrator, Database Operator, Performance Engineer |
| `alerts.manage` (platform) | create/edit rules and maintenance windows, evaluate now | Platform Administrator |
| `channels.manage` (platform) | manage notification channels | Platform Administrator |

Users only see alerts of servers they can see. The top-bar bell and the dashboard *Active alerts* tile show the count of firing + acknowledged alerts.

## API

| Method & path | Purpose |
|---|---|
| `GET /api/v1/alerts` | paginated alerts; `status` (repeatable, default firing+acknowledged), `severity`, `server_id`, `rule_id`, `include_demo`, `q`, `sort` |
| `GET /api/v1/alerts/summary` | `{active, unacknowledged, by_severity, visible}` |
| `POST /api/v1/alerts/{id}/acknowledge` · `/resolve` | body `{"comment": "…"}` |
| `POST /api/v1/alerts/evaluate` | evaluate all rules now |
| `GET/POST /api/v1/alert-rules`, `PUT/DELETE /api/v1/alert-rules/{id}` | rules |
| `GET/POST /api/v1/maintenance-windows`, `DELETE /api/v1/maintenance-windows/{id}` | windows (`include_ended`) |

Every change (rules, windows, acknowledgements, resolutions, manual evaluations) is written to the audit log.

## Limitations

- Evaluation uses the latest collected value, not range queries over the time-series store (no rate/average-over-window rules yet).
- Notifications are sent inline during evaluation with one retry; there is no escalation or repeat-notification policy yet.
