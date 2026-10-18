# Notification channels

**Platform → Configuration → Notification channels** (permission `channels.manage`) defines where [alert](../user-guide/alerting.md) notifications go. Rules choose their channels; a channel only sends alerts at or above its **minimum severity**.

Secrets (webhook signing secret, SMTP password) are envelope-encrypted at rest with the platform keyring, are write-only in the API and UI (the API only reports `has_signing_secret` / `has_smtp_password`), and never appear in logs or audit events. Omitting a secret on update keeps the stored value; sending an empty string removes it. Secret-looking keys (`password`, `secret`, `token`) and `Authorization` headers are refused in the non-encrypted configuration.

## Webhook

A JSON `POST` to the configured URL:

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
    "url": "https://dbm.example.com/alerts?id=…"
  }
}
```

`event` is `firing`, `resolved` or `test` (`alert` is `null` for a test).

- **Signature** — with a signing secret, the request carries `X-DBM-Signature: sha256=<hex>`, the HMAC-SHA256 of the raw body. Verify it with a constant-time comparison.
- **Extra headers** — optional, stored unencrypted (no credentials).
- **Timeout** — `DBM_WEBHOOK_TIMEOUT_SECONDS` (default 10). Redirects are not followed. Non-2xx answers count as failures; 5xx answers and network errors are retried once, 4xx answers are not.
- **SSRF guard** — URLs whose host resolves to a private, loopback, link-local, carrier-grade NAT, multicast or reserved address are refused, as are URLs with embedded credentials. Set `DBM_WEBHOOK_ALLOW_PRIVATE=true` to allow internal receivers (for example an on-premises chat bridge). The address is checked at send time before connecting; DNS answers that change between the check and the connection are not detected.

## Email (SMTP)

Recipients are set per channel. SMTP settings come from the environment and can be overridden per channel (host, port, user, from address, STARTTLS, password):

| Variable | Default |
|---|---|
| `DBM_SMTP_HOST` | empty (email disabled unless the channel sets a host) |
| `DBM_SMTP_PORT` | `587` |
| `DBM_SMTP_USER` | empty (no authentication) |
| `DBM_SMTP_PASSWORD` | empty |
| `DBM_SMTP_FROM` | empty (falls back to the SMTP user) |
| `DBM_SMTP_STARTTLS` | `true` |

STARTTLS uses the system trust store. The subject is `[DB Manager] SEVERITY: title`.

## Simulated servers

Alerts on simulated servers are not sent unless the channel enables **Also send for simulated servers**; skipped deliveries are logged with the reason.

## Testing and the delivery log

**Test** sends a `test` event immediately and shows the result (HTTP status or error, attempts). The delivery log (history icon) lists the last attempts with event, result (*sent*, *failed*, *skipped*), attempts, response code and error.

## API

| Method & path | Purpose |
|---|---|
| `GET/POST /api/v1/notification-channels` | list / create (`name`, `kind` webhook\|email, `enabled`, `min_severity`, `send_for_simulated`, `config`, write-only `signing_secret` / `smtp_password`) |
| `PUT/DELETE /api/v1/notification-channels/{id}` | update / delete |
| `POST /api/v1/notification-channels/{id}/test` | `{status, response_code, error, attempts}` |
| `GET /api/v1/notification-channels/{id}/deliveries` | delivery log (`limit`) |

All changes and tests are audited. See also [About and product links](about.md).
