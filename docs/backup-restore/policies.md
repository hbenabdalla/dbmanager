# Policies

A policy runs backups on a schedule, encrypts them, applies [retention](retention.md) and notifies
about problems. Create them under **Backup policies** (permission `backup.policy.manage`); **Run
now** needs `backup.create`.

| Setting | Values |
|---|---|
| Kind | platform (components, optional telemetry) or database (servers, environments, sites, tags, databases) |
| Storage target | any configured target |
| Schedule | manual, daily, weekly (weekday), monthly (day 1–28) at HH:MM in a time zone |
| Encryption | age recipient, passphrase (scheduled: `env:NAME` or `file:/path` reference), or none |
| Retention | keep last N, keep N days, daily / weekly / monthly generations |
| Notifications | notification channels, and the "no successful backup" age (0 = from the schedule) |

Schedules use wall-clock time in the policy's time zone, so a daily 02:00 run stays at 02:00 across
daylight-saving changes (a time that occurs twice runs once, at the first occurrence). Cron
expressions are not offered. A policy never runs twice concurrently.

## Notifications

When a policy has notification channels, DBManager sends:

| Event | When |
|---|---|
| Backup failed / partially failed | a policy run ends `FAILED` or `PARTIAL_SUCCESS` (or is cancelled) |
| Backup storage unavailable | a run fails because the target cannot be written or read |
| Backup verification failed | a verification of one of its backups fails |
| Restore failed | a restore of one of its backups fails |
| Retention cleanup failed | stored files of an expired backup could not be deleted |
| No successful backup within policy | the newest successful backup is older than the policy allows (daily 26 h, weekly 8 days, monthly 32 days, or the configured hours); sent once per period |

Notifications use the existing channels (webhook with generic/Slack/Teams payloads, email) with
their retry behaviour.
