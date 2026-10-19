# Notification channels

**Platform → Configuration → Notification channels** (permission `channels.manage`) defines where [alert](../user-guide/alerting.md) notifications go. Rules choose their channels, and channels can also subscribe to alerts (see [Routing](#routing)); a channel only sends alerts at or above its **minimum severity**.

Secrets (webhook signing secret, SMTP password) are envelope-encrypted at rest with the platform keyring, are write-only in the API and UI (the API only reports `has_signing_secret` / `has_smtp_password`), and never appear in logs or audit events. Omitting a secret on update keeps the stored value; sending an empty string removes it. Secret-looking keys (`password`, `secret`, `token`) and `Authorization` headers are refused in the non-encrypted configuration.

## Routing

A notification goes to every enabled channel that is listed by the alert's rule **or** has
**Receive alerts of every rule** (`routes.subscribe_all`) turned on, and that matches its filters:

* minimum severity;
* **Only environments** (`routes.environment_ids`), **Only engines** (`routes.engines`) and
  **Only servers with tags** (`routes.tags`, `key` or `key=value`, any of them) — empty filters
  match every server.

Example: a channel "Production DBA" with *Receive alerts of every rule*, minimum severity *high*
and environment *Production* gets every high or critical alert of production servers, whatever
the rule.

## Webhook

The **payload format** (`preset`) is *DBManager JSON* (`generic`, below), *Slack* (`slack`, an
incoming-webhook message with blocks and an *Open in DBManager* button) or *Microsoft Teams*
(`teams`, a message with an Adaptive Card, for a Teams workflow "Post to a channel when a webhook
request is received"). The generic format is a JSON `POST` to the configured URL:

```json
{
  "source": "dbmanager",
  "event": "firing",
  "sent_at": "2026-10-18T12:00:00+00:00",
  "alert": {
    "id": "…", "rule_id": "…", "rule": "Connection pressure", "status": "firing", "severity": "high",
    "title": "…", "message": "connections.utilization = 96 (>= 90)",
    "metric": "connections.utilization", "operator": ">=", "value": 96, "threshold": 90,
    "server_id": "…", "server_name": "PG-PROD-01", "engine": "postgresql", "environment": "Production",
    "is_demo": false, "fired_at": "…", "resolved_at": null,
    "renotify": false, "notify_count": 1,
    "url": "https://dbm.example.com/alerts?id=…"
  }
}
```

`event` is `firing`, `resolved` or `test` (`alert` is `null` for a test). A repeated firing
notification (rule *Re-notify every N minutes*) has `renotify: true`.

- **Signature** — with a signing secret, the request carries `X-DBM-Signature: sha256=<hex>`, the HMAC-SHA256 of the raw body. Verify it with a constant-time comparison.
- **Extra headers** — optional, stored unencrypted (no credentials).
- **Timeout** — `DBM_WEBHOOK_TIMEOUT_SECONDS` (default 10). Redirects are not followed. Non-2xx answers count as failures; see [Retries](#retries).
- **SSRF guard** — URLs whose host resolves to a private, loopback, link-local, carrier-grade NAT, multicast or reserved address are refused, as are URLs with embedded credentials. Set `DBM_WEBHOOK_ALLOW_PRIVATE=true` to allow internal receivers (for example an on-premises chat bridge). The address is checked at send time before connecting; DNS answers that change between the check and the connection are not detected.

## Email (SMTP)

Recipients are set per channel. SMTP settings come from the environment and can be overridden per channel (host, port, user, from address, security, password):

| Variable | Default |
|---|---|
| `DBM_SMTP_HOST` | empty (email disabled unless the channel sets a host) |
| `DBM_SMTP_PORT` | `587` |
| `DBM_SMTP_USER` | empty (no authentication) |
| `DBM_SMTP_PASSWORD` | empty |
| `DBM_SMTP_FROM` | empty (falls back to the SMTP user) |
| `DBM_SMTP_SECURITY` | `starttls` (submission, port 587), `tls` (implicit TLS / SMTPS, port 465) or `none` (plain; internal relays only). Empty = derived from the deprecated `DBM_SMTP_STARTTLS` (`true` → `starttls`, `false` → `none`) |
| `DBM_SMTP_CA_FILE` | empty = system trust store; path of a CA bundle for an internal CA |
| `DBM_SMTP_TIMEOUT_SECONDS` | `15` |
| `DBM_REPORT_MAX_ATTACHMENT_MB` | `10` — largest report file a [scheduled report](../user-guide/reports.md#scheduled-reports) attaches |

The server certificate is always verified (hostname and chain) for `starttls` and `tls`. The
subject is `[DBManager] SEVERITY: title` (`(still firing)` is appended to re-notifications).
E-mail channels also deliver [scheduled reports](../user-guide/reports.md#scheduled-reports) as
attachments, with the subject `[DBManager] report title — schedule name`.

## Retries

The first attempt is made when the alert fires (or resolves). Failures that may be temporary —
network errors, timeouts, HTTP 408, 425, 429 and 5xx answers, SMTP errors — are retried in the
background by the `notification_retries` job with increasing delays; the delivery shows as
*retrying* with its next attempt time. Other 4xx answers fail at once.

| Variable | Default | Meaning |
|---|---|---|
| `DBM_NOTIFY_MAX_ATTEMPTS` | `5` | attempts per notification, first try included (1–20) |
| `DBM_NOTIFY_RETRY_BACKOFF_SECONDS` | `60,300,900,3600` | delay before each retry; the last value repeats |

A channel that is disabled or deleted before a retry ends the delivery as *failed*.

## Simulated servers

Alerts on simulated servers are not sent unless the channel enables **Also send for simulated servers**; skipped deliveries are logged with the reason.

## Testing and the delivery log

**Test** sends a `test` event immediately — one attempt, no background retries — and shows the result (HTTP status or error). The delivery log (history icon) lists the last deliveries with event, result (*sent*, *retrying*, *failed*, *skipped*), attempts, response code, error and the next attempt time of a retrying delivery.

## API

| Method & path | Purpose |
|---|---|
| `GET/POST /api/v1/notification-channels` | list / create (`name`, `kind` webhook\|email, `enabled`, `min_severity`, `send_for_simulated`, `preset` generic\|slack\|teams, `routes` {`subscribe_all`, `environment_ids`, `tags`, `engines`}, `config` incl. email `security`, write-only `signing_secret` / `smtp_password`) |
| `PUT/DELETE /api/v1/notification-channels/{id}` | update / delete |
| `POST /api/v1/notification-channels/{id}/test` | `{status, response_code, error, attempts}` |
| `GET /api/v1/notification-channels/{id}/deliveries` | delivery log (`limit`) |

All changes and tests are audited. See also [About and product links](about.md).
