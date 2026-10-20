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

## Protection and freshness

A server counts as **Protected** when an enabled database policy covers it and its newest
successful backup is younger than the policy's *freshness limit*: the configured "no successful
backup" age, otherwise derived from the schedule (daily 26 h, weekly 8 days, monthly 32 days). A
**manual** policy without a configured age uses **8 days** for this status only; it never raises
an overdue notification. A covered server whose backup is older is *At risk*; a server no policy
covers is *Not protected* (`GET /api/v1/servers/{id}/backup-protection`, field
`freshness_limit_hours`).

## Notifications

Backup events are sent as **notifications** through notification channels. They are **not
alerts**: they do not appear on the **Alerts** page, have no acknowledge/resolve lifecycle and are
not re-notified. The delivery log of each channel records them (**Platform → Configuration → Notification
channels**), and the backup itself shows its state and error in **Backup & Restore**.

Where an event goes:

* events of a backup made by a policy (and of its verifications and restores) go to the
  **policy's notification channels**;
* events of backups that **no policy owns** — manual backups, their verification and restores — go
  to the channels named in the platform setting **`backup.notification_channels`** (channel names,
  comma separated, under **Platform → Configuration → Settings**). Empty: nothing is sent; the event
  is only visible in the app.

| Title | Severity | When |
|---|---|---|
| Backup failed | high | a platform or database backup job ends `FAILED` |
| Backup partially failed | high | a database backup job ends `PARTIAL_SUCCESS` (some assets failed) |
| Backup storage unavailable | high | the job failed because the storage target could not be written or read; classified from the storage error type, not from the message text (it replaces the titles above) |
| Backup job cancelled | medium | a backup, verification or restore job was cancelled |
| Backup verification failed | high | a verification job ends with a failed check |
| Restore failed | high | a platform or database restore ends `FAILED` |
| Retention cleanup failed | high | stored files of an expired backup could not be deleted |
| No successful backup within policy | high | the newest successful backup of a policy with a freshness limit is older than allowed; sent once per period |

The message carries the error (redacted) and, for bulk backups, how many assets succeeded. Channels
use their normal payload presets (generic, Slack, Teams, email) and retry behaviour; the event's
`rule` field is `backup policy <name>` or `backup (no policy)` and its link opens the Backup &
Restore page.
