# Security model

DB Manager holds credentials for production databases, so it is designed so that it never
becomes an easy path to privilege escalation. This document describes the controls and the
remaining risks.

## Threat model (summary)

| Threat | Control |
|---|---|
| Stolen application database dump exposes target passwords | Envelope encryption (AES-256-GCM) with a KEK that is not stored in the database |
| Ciphertext swapped between servers/purposes | Associated data binds each ciphertext to `credential:<server>:<purpose>` |
| API leaks secrets | Credentials are write-only; responses use `secrets.describe()` (username, provider, rotation date) |
| Low-privileged platform user escalates | Scoped RBAC, anti-escalation on assignments, backend enforcement on every route |
| Read-only user modifies data through the SQL workspace | Parser-based classification + `BEGIN READ ONLY` on PostgreSQL + side-effect function deny-list |
| Accidental destructive change | Risk classes, typed confirmation (server name), production reason requirement, platform setting to disable production writes |
| Brute-force login | Per-IP sliding window, per-account lockout stored in the DB, constant-time verification |
| Session theft / CSRF | HttpOnly+Secure+SameSite=Strict session cookie, server-side sessions with idle timeout, double-submit CSRF token compared against a hash |
| XSS | React escaping, no `dangerouslySetInnerHTML`, strict CSP (`script-src 'self'`) |
| SQL injection in the platform | All catalog SQL uses bind parameters; identifiers from users are only passed as bind values |
| Information leakage via audit/logs | Audit `details` are sanitized (keys matching password/secret/token/...); logs redact sensitive keys; workspace history redacts `PASSWORD '...'` clauses |
| Enumeration of servers outside one's scope | Invisible servers return 404, not 403 |
| Monitoring account over-privileged | Separate monitoring/admin credentials; rule PG-SEC-012 flags a superuser monitoring account |

## Secrets

* **KEK**: `DBM_MASTER_KEY` (base64, 32 bytes) with `DBM_MASTER_KEY_ID`. Provide via environment
  / Kubernetes Secret / external secret operator. Back it up separately from database backups.
* **Rotation**: set a new key and id, move the old one to `DBM_PREVIOUS_MASTER_KEYS=old_id:base64`,
  restart, run `python -m app.cli rotate-master-key` (re-wraps DEKs only), then remove the old key.
* **Providers**: `local` (encrypted in DB), `env` (environment variable name), `file` (mounted file
  under an allow-listed directory, `DBM_SECRET_FILE_DIRS`). `vault` is reserved in the schema.
* Identity-provider secrets (LDAP bind password, OIDC client secret) use the same envelope scheme
  (`idp:<id>` AAD) and are never returned by the API.

## Authentication

* Local passwords: Argon2id (t=3, m=64 MiB, p=2), automatic rehash on parameter change, policy of
  12+ characters with three character classes, forced change for bootstrap/reset passwords.
* LDAP/AD: LDAPS or StartTLS is mandatory (unless `allow_insecure` is set explicitly for labs);
  user filters are escaped (`escape_filter_chars`); empty passwords are rejected before bind (prevents
  unauthenticated binds).
* OIDC: authorization code + PKCE (S256), `state` and `nonce` sealed in an encrypted, 10-minute
  HttpOnly cookie, ID token signature checked against JWKS, `iss`/`aud`/`nonce`/`exp` validated.
  External identities are never merged silently into local accounts.
* Sessions: 256-bit random tokens stored as SHA-256 hashes; TTL `DBM_SESSION_TTL_MINUTES` (8h),
  idle timeout `DBM_SESSION_IDLE_TIMEOUT_MINUTES` (30m); deactivating a user or resetting a password
  revokes all sessions; at most 20 concurrent sessions per user.

## Authorization

See [rbac.md](../administration/rbac.md). Every route calls `Authorizer.require(permission, resource)` or
`load_server(..., permission)`; navigation hiding in the UI is a convenience only. Denied mutating
requests are written to the audit log (`access.denied`).

## Safe management actions

| Risk class | Examples | Requirements |
|---|---|---|
| READ | SELECT, SHOW, EXPLAIN (estimate) | `query.execute` / `query.explain`; executed in a READ ONLY transaction |
| LOW_RISK_CHANGE | INSERT/UPDATE/DELETE with WHERE, VACUUM, ANALYZE, SET | `query.modify`, admin credential, audit |
| PRIVILEGED_CHANGE | CREATE, ALTER, GRANT, CALL/DO, side-effect functions | + typed `CONFIRM` (server name in production) |
| DESTRUCTIVE | DROP, TRUNCATE, REVOKE, DELETE/UPDATE without WHERE, ALTER SYSTEM, COPY PROGRAM, session terminate | typed server name; change reason required in production |

Additional rules: one statement per execution; unparseable statements are classified by leading
keyword and default to PRIVILEGED_CHANGE (fail-safe); `EXPLAIN ANALYZE` always runs in a transaction
that is rolled back and requires `query.explain_analyze` (and `query.modify` for DML) plus confirmation;
platform settings can disable writes or EXPLAIN ANALYZE on production entirely. Recommendations are
**never** executed automatically.

## Administration across engines

* Every server is registered as **Monitoring only** (default) or **Administration enabled**. In
  Monitoring-only mode the API refuses every change action (`409 monitoring_only`) and the SQL
  workspace runs with the read-only or monitoring credential, whatever the credential could do.
* Guarded actions (`/servers/{id}/admin/...`): each adapter builds its own vendor statements from
  validated parameters (identifiers checked against `^[A-Za-z_][A-Za-z0-9_$#]*$`, privilege
  allow-lists per engine). The preview shows them with passwords masked; execution requires the
  action's permission on the server, typed confirmation for its risk class (server name for
  destructive or production changes), a reason for destructive production changes, and the
  administration credential. Success and failure are audited with user, engine, server, database,
  action, target, result and request id — never the password.
* Passwords never appear in SQL text where the engine allows it: PostgreSQL receives a SCRAM-SHA-256
  verifier, MySQL/MariaDB a bound parameter, Oracle a bind variable inside an anonymous block; SQL
  Server masks `CREATE LOGIN … PASSWORD` in traces itself. Db2 and Db2 for z/OS manage identities
  outside the database, so no password is ever sent for them.
* Credential profiles per server: monitoring (background collection), administration (guarded
  changes, session termination) and an optional read-only credential (SQL workspace). The collector
  never needs the administration credential.
* Error messages returned to users are normalized (`CONNECTION_FAILED`, `AUTHENTICATION_FAILED`,
  `TLS_ERROR`, `DATABASE_NOT_FOUND`, `INSUFFICIENT_PRIVILEGES`, `QUERY_TIMEOUT`,
  `FEATURE_NOT_AVAILABLE`, `DRIVER_NOT_INSTALLED`) and pass through a filter that masks `PWD=` and
  `password=` fragments of driver messages; stack traces are logged server-side only.

## Network and transport

* Target connections default to `verify-full` TLS; GEN-SEC-001 flags servers registered with weaker modes.
* Every target connection has a connect timeout, a statement/call timeout and an application name;
  PostgreSQL also sets `lock_timeout` and `idle_in_transaction_session_timeout`, MySQL/MariaDB
  `MAX_EXECUTION_TIME`/`max_statement_time`, SQL Server `LOCK_TIMEOUT`, Oracle `call_timeout`,
  Db2 `CURRENT LOCK TIMEOUT` and the CLI query timeout. Blocking drivers run on a worker thread with
  an asyncio deadline, so a hung socket cannot stall the pipeline.
* TLS modes map per engine: PostgreSQL/MySQL `sslmode`/SSL context, SQL Server `Encrypt=Optional|
  Mandatory|Strict` with certificate pinning, Oracle TCPS with DN matching, Db2 `SECURITY=SSL`.
  A private CA is written to a mode-600 temporary file only for drivers that need a path.
* The compose file keeps the application database on an internal network, publishes only the
  frontend on `127.0.0.1` by default, runs containers as non-root with dropped capabilities and a
  read-only root filesystem for the backend.
* Terminate TLS in front of the frontend (ingress / route / load balancer) and keep
  `DBM_COOKIE_SECURE=true`. HSTS is added when the request arrived over HTTPS.

## Residual risks and recommendations

* **Server registration is a network capability**: users with `server.register` can make the backend
  open TCP connections to arbitrary hosts. Grant it narrowly and restrict backend egress with network
  policies to database subnets.
* The in-memory rate limiter is per replica; account lockout (DB-backed) is the cross-replica control.
  Put a WAF/ingress rate limit in front for internet-facing deployments.
* The DBA's admin credential is as powerful as the account you register. Use dedicated accounts with
  only the privileges your operations need.
* Optional AI insights are disabled and cannot be enabled in this release; when implemented they will
  only receive administrator-approved finding metadata, never SQL text, credentials or row data.
