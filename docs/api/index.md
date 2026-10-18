# REST API

The API is versioned under `/api/v1`. The live OpenAPI document is served at `/api/openapi.json`, with
Swagger UI at `/api/docs` and ReDoc at `/api/redoc`. A snapshot is in [openapi.json](../openapi.json).

## Conventions

* **Authentication**: `POST /api/v1/auth/login` sets `dbm_session` (HttpOnly) and `dbm_csrf` cookies. Every
  unsafe request (POST/PUT/PATCH/DELETE) must send header `X-CSRF-Token: <dbm_csrf value>`.
* **Authorization**: every route checks a permission (for example `server.register`, `query.explain`)
  against the caller's role bindings and their scope (platform, environment, site, cluster, server, database, schema); servers
  outside the scope behave as not found. See [RBAC](../administration/rbac.md).
* **Pagination**: `page` (1-based), `page_size` (≤ 500) → `{items, total, page, page_size}`.
* **Sorting**: `sort=field` or `sort=-field` (whitelisted per resource; unknown fields return 422).
* **Filtering**: resource-specific query parameters; repeated parameters for multi-value (`engine=oracle&engine=db2`).
* **Search**: `q` for free-text search where supported.
* **Real / simulated**: fleet-wide lists and the dashboard take `source=all|real|simulated|auto`
  (`auto` = real servers when any are visible, otherwise all). Default `all`, except the dashboard (`auto`).
* **Time ranges**: `range=15m|1h|6h|24h|7d|30d` or `start`/`end` (ISO 8601, ≤ 90 days).
* **Errors**: `{"error": {"code", "message", "details", "request_id"}}`.

| Status | `code` | Meaning |
|---|---|---|
| 401 | `not_authenticated` | missing/expired session |
| 403 | `permission_denied`, `csrf_failed` | RBAC denied (details.permission / missing_permissions) |
| 404 | `not_found` | also returned for servers outside your scope |
| 409 | `conflict`, `simulation_busy`, `simulation_present`, `simulated_server` | duplicate names, resources in use, simulation already running/present, credentials on a simulated server |
| 422 | `validation_failed`, `capability_not_supported`, `target_error`, `target_permission_denied`, `admin_credential_required` | input or capability problems |
| 428 | `confirmation_required` | resend with `confirm` (details.expected says what to type) |
| 429 | `rate_limited` | login or per-user rate limit |
| 502 | `target_unavailable` | the managed database could not be reached |

Every response carries `X-Request-ID`; send your own to correlate with audit records and logs.

* **Metrics**: ranges `15m|30m|1h|6h|12h|24h|7d|30d|90d` or `start`/`end` (ISO-8601, UTC when no
  offset); `agg=avg|min|max|sum|count|last|p50|p95|p99` per step; counters are returned as per-second
  rates; at most `max_points` (default 240) points per series. When the time-series database is
  unreachable, metric reads return **503** with code `timeseries_unavailable` (see
  [Pipeline](../monitoring/pipeline.md)).

## Example: guarded change

```http
POST /api/v1/workspace/execute
{"server_id": "…", "database": "orders", "sql": "DELETE FROM sessions"}
→ 428 {"error": {"code": "confirmation_required", "details": {"expected": "server name", "classification": {"risk": "DESTRUCTIVE", …}}}}

{"server_id": "…", "database": "orders", "sql": "DELETE FROM sessions", "confirm": "pg-orders-prod-01", "reason": "CHG-1234"}
→ 200 {"status_message": "DELETE 1832", "committed": true, …}
```

## Token authentication

Scripts and integrations can authenticate with a personal [API token](../administration/api-tokens.md)
instead of a session cookie:

```http
GET /api/v1/servers
Authorization: Bearer dbm_<prefix>_<secret>
```

* The token is created in the UI (user menu → **API tokens**) or with `POST /api/v1/me/tokens` from an
  interactive session. The secret is returned once.
* Token requests need no CSRF header, because there is no ambient cookie authority.
* Effective access is the owner's current grants intersected with the token's permissions; scope rules
  are unchanged.
* `401 invalid_token`: the token is unknown, revoked or expired, comes from an address outside its
  allow-list, or belongs to a disabled or locked user.
* `403 interactive_only`: the endpoint manages tokens, passwords, users, groups, roles, role assignments
  or identity providers.
* `429 rate_limited`: too many failed token authentications from the address, or the owner's
  per-user limit.

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/me/tokens/policy` | Maximum lifetime and the permissions a new token may carry |
| GET | `/api/v1/me/tokens` | Your tokens (never the secret) |
| POST | `/api/v1/me/tokens` | Create a token: `{name, expires_in_days, scopes?, allowed_cidrs?}` |
| DELETE | `/api/v1/me/tokens/{token_id}` | Revoke one of your tokens |
| GET | `/api/v1/users/{user_id}/tokens` | A user's tokens (`users.manage`) |
| DELETE | `/api/v1/users/{user_id}/tokens/{token_id}` | Revoke a user's token (`users.manage`) |

## Endpoints


### Advisor

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/access/privileged-accounts` | Administrative accounts across the visible fleet |
| GET | `/api/v1/advisor/rules` | Rule catalog of the deterministic advisors |
| GET | `/api/v1/recommendations` | Recommendations |
| PATCH | `/api/v1/recommendations/{item_id}` | Update Recommendation |
| GET | `/api/v1/security/findings` | Security Findings |
| PATCH | `/api/v1/security/findings/{item_id}` | Update Finding |
| GET | `/api/v1/security/posture` | Posture |
| GET | `/api/v1/servers/{server_id}/accounts` | Server Accounts |
| POST | `/api/v1/servers/{server_id}/security-scan` | Run Scan |

### Audit

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/audit` | List Events |
| GET | `/api/v1/audit/export.csv` | Export |

### Auth

| Method | Path | Summary |
|---|---|---|
| POST | `/api/v1/auth/change-password` | Change Password |
| POST | `/api/v1/auth/login` | Sign in with a local or LDAP account |
| POST | `/api/v1/auth/logout` | Logout |
| GET | `/api/v1/auth/me` | Me |
| GET | `/api/v1/auth/oidc/{provider_id}/callback` | OIDC redirect target |
| GET | `/api/v1/auth/oidc/{provider_id}/start` | Begin OIDC sign-in (redirect) |
| GET | `/api/v1/auth/providers` | Enabled single sign-on providers |

### Dashboard

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/dashboard/summary` | Summary |
| GET | `/api/v1/fleet/sources` | Real vs simulated servers visible to the caller |

### Inventory

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/applications` | List applications |
| POST | `/api/v1/applications` | Create application |
| DELETE | `/api/v1/applications/{item_id}` | Delete application |
| PUT | `/api/v1/applications/{item_id}` | Update application |
| GET | `/api/v1/clusters` | List clusters |
| POST | `/api/v1/clusters` | Create cluster |
| DELETE | `/api/v1/clusters/{item_id}` | Delete cluster |
| PUT | `/api/v1/clusters/{item_id}` | Update cluster |
| GET | `/api/v1/environments` | List environments |
| POST | `/api/v1/environments` | Create environment |
| DELETE | `/api/v1/environments/{item_id}` | Delete environment |
| PUT | `/api/v1/environments/{item_id}` | Update environment |
| GET | `/api/v1/sites` | List sites |
| POST | `/api/v1/sites` | Create site |
| DELETE | `/api/v1/sites/{item_id}` | Delete site |
| PUT | `/api/v1/sites/{item_id}` | Update site |

### Metrics

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/metrics/capacity` | Storage growth and days-until-full for visible servers |
| GET | `/api/v1/metrics/catalog` | Normalized metric catalog (names, types, units, intervals) |
| GET | `/api/v1/metrics/current` | Latest value of metrics for many servers in one call |
| GET | `/api/v1/metrics/range` | Range query over one or many servers (optionally aggregated) |
| GET | `/api/v1/platform/monitoring-storage` | Time-series database status, retention and ingestion |
| PUT | `/api/v1/platform/monitoring-storage/retention` | Set the monitoring retention policy |
| GET | `/api/v1/servers/{server_id}/databases/{database}/metrics` | Query telemetry of one database |

### Ops

| Method | Path | Summary |
|---|---|---|
| GET | `/healthz` | Liveness probe |
| GET | `/readyz` | Readiness probe with per-component status |

### Platform

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/collectors/jobs` | Jobs |
| GET | `/api/v1/collectors/status` | Collectors Status |
| GET | `/api/v1/identity-providers` | List Idps |
| POST | `/api/v1/identity-providers` | Create Idp |
| DELETE | `/api/v1/identity-providers/{idp_id}` | Delete Idp |
| PATCH | `/api/v1/identity-providers/{idp_id}` | Update Idp |
| POST | `/api/v1/identity-providers/{idp_id}/mappings` | Add Mapping |
| DELETE | `/api/v1/identity-providers/{idp_id}/mappings/{mapping_id}` | Delete Mapping |
| POST | `/api/v1/identity-providers/{idp_id}/test` | Test Idp |
| GET | `/api/v1/settings` | Get Settings View |
| PUT | `/api/v1/settings` | Put Settings |

### Setup wizard and onboarding

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/platform/setup` | resumable wizard state; without `settings.manage` only `{completed, needs_setup, steps}` |
| PUT | `/api/v1/platform/setup` | save `current_step`, `completed_steps`, per-step `data` (secret-looking keys are masked) |
| POST | `/api/v1/platform/setup/organization` | `{name, timezone}` (IANA time zone) |
| POST | `/api/v1/platform/setup/monitoring` | `{profile: light\|standard\|intensive}` → default collection interval for new servers (300/60/15 s) |
| POST | `/api/v1/platform/setup/complete` | mark the wizard completed |
| GET | `/api/v1/platform/setup/checklist` | post-setup checklist computed from the platform state |
| POST | `/api/v1/servers/test-connection/stream` | staged test, `application/x-ndjson`: validate → driver → dns → tcp → tls → authenticate → version → privileges → capabilities, then `result` |
| POST | `/api/v1/servers/{id}/test-connection/stream` | same for a registered server (its stored credential) |
| GET | `/api/v1/servers/bulk/template.csv` | bulk template — no password column |
| POST | `/api/v1/servers/bulk/validate` | per-row errors/warnings; secret columns, duplicates and unknown environments are errors |
| POST | `/api/v1/servers/bulk/create` | registers valid rows (Monitoring Only unless `management_mode=administration`) |
| GET | `/api/v1/identity-providers/presets` | OIDC presets: Keycloak, Entra ID, Okta, generic |
| POST | `/api/v1/identity-providers/test-draft` | staged test of an unsaved LDAP (dns, tcp, tls, bind, user_search, group_lookup, user_bind) or OIDC (discovery, issuer_match, jwks, endpoints, redirect_uri) configuration |

Bulk `credential` column (never a password): `server:<registered server>` copies that server's
monitoring credential; `env:<VARIABLE>@<user>` / `file:<path>@<user>` reference a secret held by the
backend's environment or an allow-listed secrets directory.

### Queries

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/queries/top` | Top queries across all visible servers |
| POST | `/api/v1/servers/{server_id}/explain` | Explain |
| GET | `/api/v1/servers/{server_id}/query-fingerprints` | Known normalized statements of a server |
| GET | `/api/v1/servers/{server_id}/query-stats` | Query Stats |
| GET | `/api/v1/servers/{server_id}/query-stats/{fingerprint}/history` | Query History |

### Rbac

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/permissions` | Permissions |
| GET | `/api/v1/role-assignments` | List Assignments |
| POST | `/api/v1/role-assignments` | Create Assignment |
| DELETE | `/api/v1/role-assignments/{assignment_id}` | Delete Assignment |
| GET | `/api/v1/roles` | List Roles |
| POST | `/api/v1/roles` | Create Role |
| DELETE | `/api/v1/roles/{role_id}` | Delete Role |
| PATCH | `/api/v1/roles/{role_id}` | Update Role |

### Reports

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/reports` | List Reports |
| GET | `/api/v1/reports/{key}.{fmt}` | Download |

### Server data

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/servers/{server_id}/audit` | Server Audit |
| GET | `/api/v1/servers/{server_id}/configuration` | Configuration |
| GET | `/api/v1/servers/{server_id}/database-users` | Database Users |
| GET | `/api/v1/servers/{server_id}/databases` | Databases |
| GET | `/api/v1/servers/{server_id}/databases/{database}/schemas` | Schemas |
| GET | `/api/v1/servers/{server_id}/databases/{database}/schemas/{schema}/objects` | Objects |
| GET | `/api/v1/servers/{server_id}/databases/{database}/schemas/{schema}/objects/{name}` | Object Detail |
| GET | `/api/v1/servers/{server_id}/locks` | Locks |
| GET | `/api/v1/servers/{server_id}/logs` | Collection Log |
| GET | `/api/v1/servers/{server_id}/metrics` | Metrics |
| GET | `/api/v1/servers/{server_id}/metrics/baseline` | Statistical baseline of one metric and how the current value compares |
| GET | `/api/v1/servers/{server_id}/metrics/capacity` | Storage growth trend and (when the history allows) days until full |
| GET | `/api/v1/servers/{server_id}/metrics/latest` | Latest Metrics |
| GET | `/api/v1/servers/{server_id}/privileges` | Privileges |
| GET | `/api/v1/servers/{server_id}/replication` | Replication |
| GET | `/api/v1/servers/{server_id}/roles` | Db Roles |
| GET | `/api/v1/servers/{server_id}/sessions` | Sessions |
| POST | `/api/v1/servers/{server_id}/sessions/{session_id}/cancel` | Cancel Session |
| GET | `/api/v1/servers/{server_id}/storage` | Storage |

### Servers

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/credentials` | Credential coverage across servers (no secret material) |
| GET | `/api/v1/databases` | Fleet-wide database inventory (from the last discovery) |
| GET | `/api/v1/engines` | Supported engines and their capability profiles |
| GET | `/api/v1/servers` | List Servers |
| POST | `/api/v1/servers` | Register Server |
| POST | `/api/v1/servers/test-connection` | Test connection parameters before registering |
| DELETE | `/api/v1/servers/{server_id}` | Delete Server |
| GET | `/api/v1/servers/{server_id}` | Get Server |
| PATCH | `/api/v1/servers/{server_id}` | Update Server |
| GET | `/api/v1/servers/{server_id}/capabilities` | Capabilities |
| POST | `/api/v1/servers/{server_id}/collect` | Queue an immediate collection |
| DELETE | `/api/v1/servers/{server_id}/credentials/{purpose}` | Delete Credential |
| PUT | `/api/v1/servers/{server_id}/credentials/{purpose}` | Set Credential |
| POST | `/api/v1/servers/{server_id}/test-connection` | Test Server Connection |

### Simulation

| Method | Path | Summary |
|---|---|---|
| DELETE | `/api/v1/simulation` | Remove all simulated data (real resources are not affected) |
| GET | `/api/v1/simulation/cleanup-preview` | What removing the simulation would delete |
| POST | `/api/v1/simulation/generate` | Queue generation of a simulated fleet |
| GET | `/api/v1/simulation/profiles` | Available simulation profiles |
| POST | `/api/v1/simulation/regenerate` | Replace the simulated fleet with a new one |
| GET | `/api/v1/simulation/status` | Simulated fleet status, counts and the active run |

### Users

| Method | Path | Summary |
|---|---|---|
| GET | `/api/v1/groups` | List Groups |
| POST | `/api/v1/groups` | Create Group |
| DELETE | `/api/v1/groups/{group_id}` | Delete Group |
| PUT | `/api/v1/groups/{group_id}/members` | Set Members |
| GET | `/api/v1/users` | List Users |
| POST | `/api/v1/users` | Create User |
| DELETE | `/api/v1/users/{user_id}` | Delete User |
| GET | `/api/v1/users/{user_id}` | Get User |
| PATCH | `/api/v1/users/{user_id}` | Update User |
| GET | `/api/v1/users/{user_id}/effective-permissions` | Effective Permissions |
| POST | `/api/v1/users/{user_id}/reset-password` | Reset Password |

### Workspace

| Method | Path | Summary |
|---|---|---|
| POST | `/api/v1/workspace/classify` | Classify |
| POST | `/api/v1/workspace/execute` | Execute |
| POST | `/api/v1/workspace/export.csv` | Execute a read-only statement and download the result as CSV |
| GET | `/api/v1/workspace/history` | History |
