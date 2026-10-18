# Rate limiting

DB Manager limits how fast a single caller can use the API, so that one user, script or runaway
dashboard cannot exhaust the platform or flood a managed database with live queries.
Limits apply **per principal**: an interactive session counts against its user, and each
[API token](api-tokens.md) has its own budget.

## Policies

| Policy | What counts | Setting | Default per minute |
|---|---|---|---|
| `api` | every authenticated API request | `DBM_API_RATE_PER_USER_PER_MINUTE` | 600 |
| `target` | live queries against a managed database: databases, schemas, objects, database users, roles, privileges, sessions, locks, replication, configuration, storage | `DBM_API_RATE_TARGET_PER_MINUTE` | 120 |
| `expensive` | SQL workspace execution and CSV export, execution plans, administration actions, session cancel, connection tests (including the staged tests of the wizard), bulk onboarding, immediate collection, notification-channel and identity-provider tests, audit and report exports | `DBM_API_RATE_EXPENSIVE_PER_MINUTE` | 30 |
| `login` | login attempts per source address | `DBM_LOGIN_RATE_PER_IP_PER_MINUTE` | 20 |
| `token_failures` | failed `Authorization: Bearer` authentications per source address | `DBM_API_TOKEN_AUTH_FAILURES_PER_MINUTE` | 30 |

A request can count against two policies: every authenticated request counts against `api`, and a
live query or an expensive operation also counts against `target` or `expensive`. Stored
telemetry (charts, metric history, query statistics, dashboards) only counts against `api`.

Set a policy to `0` to switch it off. Changes take effect when the backend is restarted with the
new value.

The UI stays well below the defaults: the busiest page (a server's *Activity* tab) refreshes
sessions and locks every 10 seconds, 12 live queries per minute.

## When a limit is reached

The API answers `429 Too Many Requests` with the error code `rate_limited`:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 17
RateLimit-Limit: 30
RateLimit-Remaining: 0
RateLimit-Reset: 17
RateLimit-Policy: "expensive";q=30;w=60

{"error": {"code": "rate_limited",
           "message": "Too many expensive operations (SQL execution, plans, exports, tests). Try again in 17 s.",
           "details": {"policy": "expensive", "limit": 30, "window_seconds": 60, "retry_after_seconds": 17}}}
```

`Retry-After` is exact: a client that waits that many seconds (and sends nothing in between) is
served again. Refused requests are not counted, so retrying too early does not extend the wait.
The web UI retries a refused data load once after `Retry-After` and otherwise shows the message.

Scripts should honour `Retry-After`, for example in Python:

```python
import time, httpx

def get(client: httpx.Client, url: str) -> httpx.Response:
    while True:
        r = client.get(url)
        if r.status_code != 429:
            return r
        time.sleep(int(r.headers.get("Retry-After", "5")))
```

## How it works

DB Manager uses a *sliding-window counter*: hits are counted in one-minute windows, and the
current rate is estimated as the previous window's count, weighted by how much of it still
overlaps the last 60 seconds, plus the current window's count. This avoids the burst a plain
per-minute counter allows at a window boundary.

| `DBM_RATE_LIMIT_BACKEND` | Behaviour |
|---|---|
| `database` (default) | Counters live in the `rate_limit_windows` table of DB Manager's own PostgreSQL database. Every backend replica shares them, so the limits hold for the whole installation, not per replica. Each counter update is one short statement through a small pool of its own (`DBM_RATE_LIMIT_POOL_SIZE`, default 5). Each caller keeps at most two rows; idle rows are removed by the retention job. |
| `memory` | Counters in each backend process. For single-instance installations that want to avoid the extra statements; with several replicas each one counts separately. |

If the limiter's storage is unavailable, requests are **allowed** and a warning is logged. Rate
limiting protects capacity; authentication, authorization, account lockout and audit do not
depend on it.

Every refusal increments the Prometheus counter `dbm_rate_limited_total{policy="…"}` on
`/metrics`. Failed logins and failed token authentications are also [audited](audit.md).

## Behind a reverse proxy

`login` and `token_failures` are counted per client address. DB Manager derives that address
from `X-Forwarded-For` only for proxies listed in `DBM_TRUSTED_PROXIES`; otherwise every request
appears to come from the proxy and all users share one login budget. See
[reverse proxy and TLS](../deployment/reverse-proxy-tls.md).

For internet-facing installations, keep a rate limit at the ingress or WAF as well: it stops
traffic before it reaches the application and also covers unauthenticated endpoints.
