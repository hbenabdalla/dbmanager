# API tokens

Personal API tokens let scripts, CI jobs and integrations call the [REST API](../api/index.md) without
an interactive session. A token always acts **as the user who created it**, never with more access.

## Creating a token

Open the user menu (top right) → **API tokens** → **Create token**, then fill in:

* **Name**: what uses the token, for example `grafana-inventory-sync`.
* **Expires after**: required. The longest allowed lifetime is set by `DBM_API_TOKEN_MAX_DAYS`
  (default 365 days).
* **Permissions**:
    * *Same as mine*: the token follows your current role assignments.
    * *Only selected permissions*: pick a subset. Only permissions you hold are offered, and the API
      refuses any you do not hold (403).
* **Allowed source addresses** (optional): CIDRs such as `10.20.0.0/16`. A request from any other
  address is rejected.

The full token (`dbm_<prefix>_<secret>`) is shown **once**, in the dialog that follows. Copy it into
your secret manager straight away; it cannot be displayed again. If it is lost, revoke it and create
a new one.

## How access is evaluated

Every request made with a token gets this access:

> the owner's **current** role grants, each intersected with the token's selected permissions

Scope still applies (environment, site, cluster, server and so on): a token whose owner can see only
Production cannot see Development. Removing a role from the owner takes effect on the token's next
request.

A token stops working when any of these happen:

* it is revoked;
* it expires;
* the owner's account is disabled or locked;
* the request comes from outside the allowed addresses.

All of these return the same `401 invalid_token` response, so a caller cannot tell which check
failed.

### Endpoints a token can never call

Some operations need a person at an interactive session (a password, SSO or LDAP sign-in). A token
gets `403 interactive_only` for:

| Method | Path |
|---|---|
| any | `/api/v1/me/tokens…` (managing tokens with a token) |
| any | `/api/v1/users/{id}/tokens…` |
| any | `/api/v1/auth/change-password`, `/api/v1/auth/logout` |
| any | `/api/v1/users/{id}/reset-password` |
| POST, PUT, PATCH, DELETE | `/api/v1/users…`, `/api/v1/groups…`, `/api/v1/roles…`, `/api/v1/role-assignments…`, `/api/v1/identity-providers…` |

Reading users, groups and roles with a token is allowed when the owner holds the matching
permission.

## Storage and auditing

* Only the SHA-256 hash of the secret is stored. The prefix identifies the token in lists and in the
  audit log; it is not a secret.
* Creating and revoking a token is audited (`api_token.create`, `api_token.revoke`). The record holds
  the name, prefix, permissions, expiry and allowed addresses, never the secret.
* A failed token authentication is audited as `auth.api_token` with result `failure`, the prefix (if
  the value was well formed) and the path.
* Actions taken with a token appear in the audit log under the owner's name with
  auth source `api_token`.
* *Last used* (time and address) is updated at most once a minute.

## Administration

Administrators with `users.manage` see a user's tokens under **Platform → Configuration → Users**: select
the user, then the **API tokens** section of the user dialog. They can revoke a token there, but cannot see its secret or create tokens for someone else.

| Method | Path | Permission |
|---|---|---|
| GET | `/api/v1/users/{id}/tokens` | `users.manage` |
| DELETE | `/api/v1/users/{id}/tokens/{token_id}` | `users.manage` |

Disabling a user's account immediately stops all of that user's tokens. Re-enabling the account
brings back the tokens that have not expired or been revoked.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `DBM_API_TOKEN_MAX_DAYS` | `365` | Longest lifetime a new token may have (1–3650). |
| `DBM_API_TOKEN_AUTH_FAILURES_PER_MINUTE` | `30` | Failed token authentications allowed per source address per minute; after that the address gets `429`. |
| `DBM_API_RATE_PER_USER_PER_MINUTE` | `600` | Requests per minute for each token; a token has its own budget, separate from its owner's sessions. Live and expensive operations have lower limits — see [rate limiting](rate-limiting.md). |

Allowed-address checks use the client address that KBM DBManager derives using `DBM_TRUSTED_PROXIES`.
Behind a reverse proxy, configure the proxy addresses there, or every request will appear to come
from the proxy.

## Limitations

* Tokens are personal. There are no service accounts yet; create a dedicated local user for a shared
  integration and give it a narrow role.
* Successful token authentications are not audited one by one. The actions they perform are audited
  as usual, and *last used* is recorded on the token.
