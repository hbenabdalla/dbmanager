# Alerting

DBManager raises alerts when a rule's condition holds on a monitored server, shows them under **Operations → Alerts**, and optionally sends notifications through [notification channels](../administration/notifications.md).

## How evaluation works

A background job (`evaluate_alerts`) runs every `DBM_ALERT_EVALUATION_SECONDS` (default 60, minimum 15). For every enabled rule and every monitored server in the rule's scope it checks a metric, the server health state, or the collector state (details: [alerting architecture](../architecture/alerting.md)):

| Rule type | Fires when |
|---|---|
| Metric threshold | `metric operator threshold` holds, e.g. `connections.utilization >= 90`. Metrics are picked from the metric catalog. **Evaluate** chooses the value: the *latest collected value* (a value older than three collection intervals counts as *no data*), or the *average*, *minimum* or *maximum* over a window of history (at least one minute) read from the time-series database — e.g. `avg(cpu.percent, 15m) > 80` — or the *deviation from baseline (z-score)*: how many standard deviations the latest value (or a window average) is from the server's own history over *Baseline (days)*, e.g. `zscore(cpu.percent, 7d baseline) > 3`. For a server-scoped z-score rule the last step previews the baseline. Details: [baselines](../performance/baselines-and-capacity.md#baseline-relative-alert-rules-z-score). No data neither fires nor resolves. |
| Server health state | the server health is in the chosen states (e.g. `unavailable`, `critical`). |
| Collector failing | a collector component reports `failed`/`unavailable`, or collection failed the given number of times in a row (default: the circuit-breaker threshold). |

With **For (seconds)** the condition must hold for that long: the alert is *pending* first and becomes *firing* once the duration has elapsed. A pending alert whose condition clears is dropped without notification.

## Lifecycle

`pending → firing → acknowledged → resolved`

- There is at most one active alert per rule and server (de-duplication); repeated evaluations update its value and message.
- **Acknowledge** (permission `alerts.acknowledge`) records who is handling it, with an optional comment. It stays active but no further notifications (and no re-notifications) are sent.
- **Re-notify every N minutes** (rule setting) repeats the *firing* notification while the alert is firing and unacknowledged; the message is marked *still firing*.
- **Comments**: open an alert (click its title) to see its timeline, comments and notification history, and to add a comment (permission `alerts.acknowledge`).
- **Resolve** closes it manually; it fires again at the next evaluation if the condition still holds.
- Alerts **auto-resolve** (`resolved_by = auto`) when the condition clears; a *resolved* notification is sent when the rule has *Notify on resolve* and the alert had been notified.
- Disabling or deleting a rule closes its alerts.

Alerts on simulated servers are marked **Simulated**; channels do not send them unless the channel explicitly enables *Also send for simulated servers*.

## Maintenance windows

**Operations → Alerts → Maintenance windows** schedules (and edits) a window for all servers, one environment, one server or the servers with a tag. A window happens once, **every day** or **every week** on chosen weekdays, optionally until a date. Recurring windows follow the chosen **time zone** (default: your browser's): a window at 02:00 Europe/Berlin stays at 02:00 local time across daylight-saving changes. An occurrence lasts at most 24 hours.

Alerts on servers inside an active window are still raised but marked **Suppressed (maintenance)** and not notified (the delivery log records them as *skipped*). If such an alert is **still firing when the window ends**, its firing notification is sent then; an alert that clears during the window is never notified.

The server page shows the server's active alerts and the maintenance windows that apply to it, and an alert badge next to its name; the fleet server list shows the badge too.

## Rules

**Operations → Alerts → Alert rules** (manage with `alerts.manage`): the dialog walks through *Condition* (type, metric, operator, threshold, evaluation and window, duration, severity), *Scope* (all servers, one environment, one server, or a tag `key` / `key=value`; enabled) and *Notify* (channels, notify on resolve, re-notify interval). Channels that subscribe to all alerts receive the rule's alerts even when the rule lists no channel (see [routing](../administration/notifications.md#routing)).

Example rules:

- Connection utilization `>= 90` for 300 s, severity high, all servers.
- Health in `unavailable` for 60 s, severity critical, environment *Production*.
- Replication lag `> 120` s for 300 s, severity medium.
- Average CPU over 15 minutes `> 80` %, servers tagged `team=payments`, re-notify every 30 minutes.

## Permissions

| Permission | Allows | Default roles |
|---|---|---|
| `alerts.view` | see alerts of servers in the user's scope, rules and windows | all built-in roles |
| `alerts.acknowledge` | acknowledge, resolve and comment on alerts of servers in scope | Platform Administrator, DBA Administrator, Database Operator, Performance Engineer |
| `alerts.manage` (platform) | create/edit rules and maintenance windows, evaluate now | Platform Administrator |
| `channels.manage` (platform) | manage notification channels | Platform Administrator |

Users only see alerts of servers they can see; users whose access is limited to some servers see only the rules and windows that can apply to those servers. The top-bar bell and the dashboard *Active alerts* tile show the count of firing + acknowledged alerts.

## API

| Method & path | Purpose |
|---|---|
| `GET /api/v1/alerts` | paginated alerts; `status` (repeatable, default firing+acknowledged), `severity`, `server_id`, `rule_id`, `include_demo`, `q`, `sort` |
| `GET /api/v1/alerts/summary` | `{active, unacknowledged, by_severity, visible}`; `by_server=true` adds per-server counts |
| `GET /api/v1/alerts/{id}` | one alert with `comments` and `notifications` |
| `POST /api/v1/alerts/{id}/comments` | body `{"body": "…"}` |
| `POST /api/v1/alerts/{id}/acknowledge` · `/resolve` | body `{"comment": "…"}` |
| `POST /api/v1/alerts/evaluate` | evaluate all rules now |
| `GET/POST /api/v1/alert-rules`, `PUT/DELETE /api/v1/alert-rules/{id}` | rules |
| `GET/POST /api/v1/maintenance-windows`, `PUT/DELETE /api/v1/maintenance-windows/{id}` | windows (`include_ended`, `server_id`; fields `recurrence`, `weekdays`, `time_zone`, `repeat_until`) |

Rules accept `aggregation` (`latest`, `avg`, `min`, `max`), `window_seconds` and `renotify_minutes`; `scope_type` may be `tag`.

Every change (rules, windows, acknowledgements, resolutions, comments, manual evaluations) is written to the audit log.

## Limitations

- Range rules use average, minimum or maximum over a window; rate-of-change and baseline-relative conditions are not available yet.
- Re-notification repeats to the same channels; there is no multi-level escalation chain.
