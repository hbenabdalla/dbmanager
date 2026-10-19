# Audit forwarding (SIEM)

DB Manager keeps its audit log in the control plane (see [Audit](audit.md)). **Platform →
Configuration → Audit forwarding** sends a copy of every audit event to one or more SIEM
destinations, over syslog or a webhook. Configuring destinations needs the platform-scope
permission `audit.forward` (granted to Platform Administrator by default).

## Destinations

| Field | Meaning |
|---|---|
| Name | Unique name of the destination |
| Type | `syslog` or `webhook`; cannot be changed after creation |
| Categories | Audit categories to forward (`auth`, `security`, `admin`, `platform`, `alerting`, `query`, `collector`, `operations`, `report`, or any other category name). None selected = every category |
| Start forwarding from | On creation only: *New events only* (default) or *The whole audit log* |
| Enabled | A disabled destination keeps its position and resumes from it when enabled again |

The list shows for each destination its **backlog** (matching events not forwarded yet), the
number of events forwarded, the last success and the last error.

**Send test event** sends one `siem.test` event (category `security`) directly, without moving the
destination's position, and shows whether the receiver accepted it. Tests, creation, changes and
removal are recorded in the audit log (`siem_destination.*`).

## Syslog (RFC 5424)

| Setting | Default | Notes |
|---|---|---|
| Host | — | Required |
| Transport | `udp` | `udp` (RFC 5426), `tcp` with octet-counting framing (RFC 6587), `tls` (RFC 5425) |
| Port | 514 (`udp`, `tcp`), 6514 (`tls`) | |
| Facility | `audit` (13) | `kern`, `user`, `auth`, `authpriv`, `audit`, `local0`–`local7`, or a number 0–23 |
| APP-NAME | `dbmanager` | |
| Structured-data ID | `dbm@32473` | 32473 is the example enterprise number of RFC 5612; use `name@<your PEN>` if your organisation has one |
| CA certificate (PEM) | system trust store | TLS only: the receiver's certificate and host name are always verified |

Each audit event becomes one message:

```text
<PRI>1 TIMESTAMP HOSTNAME APP-NAME - MSGID [SD-ID id="…" action="…" result="…" category="…" user="…" src="…" server="…" db="…" object="…" risk="…"] BOM{event JSON}
```

* `PRI` = facility × 8 + severity. Severity is 4 (warning) for failed or denied actions, 5
  (notice) for `auth` and `security` events and destructive actions, 6 (informational) otherwise.
* `TIMESTAMP` is UTC with milliseconds; `MSGID` is the audit action (e.g. `auth.login`).
* Structured-data parameters that are empty are left out; values are escaped as RFC 5424
  requires.
* The message body is the complete audit event as JSON (UTF-8, after a BOM).

UDP gives no delivery confirmation and messages longer than 8 KiB are truncated; prefer TCP or TLS
for an audit trail.

## Webhook

Events are POSTed in batches of up to 500:

```json
{"source": "dbmanager", "events": [{"id": 1234, "ts": "2026-10-18T09:00:00+00:00", "action": "auth.login", "category": "auth", "result": "success", "username": "dba", "...": "..."}]}
```

* Any HTTP status below 300 counts as accepted; anything else (or a network error) is a failure.
* With a **signing secret**, each batch carries `X-DBM-Signature: sha256=<hex>`: the HMAC-SHA256 of
  the raw request body with the secret. The secret is encrypted at rest and never shown again;
  when editing, leave it empty to keep it or tick *Remove the stored signing secret*.
* Extra headers are stored unencrypted: an `Authorization` header and secrets in the configuration
  are refused — use the signing secret.
* The same address guard as notification webhooks applies: private, loopback and link-local
  addresses are refused unless `DBM_WEBHOOK_ALLOW_PRIVATE=true`. Redirects are not followed.

## Delivery semantics

The background job `siem_export` runs every 30 seconds. For each enabled destination it reads the
audit events after the destination's position (cursor), oldest first, in batches of 500, once
they are at least 5 seconds old. The cursor only moves after a batch was accepted, so delivery is
**at least once**: a receiver that was unreachable gets the events it missed on a later run, and
the same event can arrive twice after a failure mid-batch. Use the event `id` to de-duplicate.

Audit events never contain passwords or secrets; they pass through the platform's redaction once
more before they leave DB Manager.

## Troubleshooting

| Symptom | Check |
|---|---|
| Backlog grows, *Error … ConnectionRefusedError* | Host, port and transport; a firewall between the backend/worker and the SIEM |
| `CERTIFICATE_VERIFY_FAILED` | The receiver's certificate is signed by a private CA: paste the CA certificate; the host name must match the certificate |
| `receiver answered HTTP 401/403` | The receiver expects authentication: use the signing secret, or an allowed header that is not a credential |
| Webhook refused when saving | The URL points to a private address: set `DBM_WEBHOOK_ALLOW_PRIVATE=true` if this is intended |
| Nothing arrives, no error | The destination is disabled, or its categories exclude the events you expect |
| Events missing in the SIEM over UDP | UDP drops silently; switch to TCP or TLS |

API: `GET/POST /api/v1/platform/siem-destinations`, `PUT/DELETE /api/v1/platform/siem-destinations/{id}`,
`POST /api/v1/platform/siem-destinations/{id}/test`.
