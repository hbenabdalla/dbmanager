# System architecture

## Component overview

```mermaid
flowchart LR
  B[Browser] -->|HTTPS| RP["Reverse proxy / ingress<br/>(TLS, yours)"]
  RP -->|HTTP :8080| F["frontend nginx<br/>SPA · proxies /api/ and /healthz<br/>security headers · client address"]
  F -->|/api/| API

  subgraph BE["backend (FastAPI, one image)"]
    API["REST API /api/v1<br/>auth · CSRF · RBAC · rate limits · audit"]
    JOBS["scheduler leader + workers<br/>collection, discovery, advisors,<br/>alerts, backups, reports, SIEM"]
    AD["adapter layer<br/>one adapter per engine"]
    API --- AD
    JOBS --- AD
  end

  API --> CP
  JOBS --> CP
  CP[("PostgreSQL control plane<br/>inventory · identities · roles · credentials (encrypted)<br/>findings · alerts · backups catalog · audit · settings · job queue")]
  API --> TS
  JOBS --> TS
  TS[("VictoriaMetrics<br/>metric history, query telemetry")]

  AD --> DBS[("target databases<br/>PostgreSQL · MySQL · MariaDB · SQL Server<br/>Oracle · Db2 LUW · Db2 for z/OS")]
  JOBS --> SMTP[SMTP]
  JOBS --> WH[webhooks<br/>generic · Slack · Teams]
  JOBS --> SIEM[SIEM<br/>syslog · signed webhook]
  JOBS --> STO[backup storage<br/>directory · NFS · S3]
  API --> LDAP[LDAP / AD]
  API --> OIDC[OIDC provider]
  AD -.credentials.-> VAULT[HashiCorp Vault]
  PROM[Prometheus] -.->|"/metrics :8000<br/>(not via nginx)"| BE
```

PostgreSQL and VictoriaMetrics sit on the internal `data` network; neither is reachable from the
browser. Every metric read goes through the API, which authorizes the caller first. `/metrics` is
unauthenticated and only served on the backend port ([Diagnostics](../administration/diagnostics.md)).

| Component | Responsibility | Code | More |
|---|---|---|---|
| Frontend | single-page application; nginx serves it, proxies `/api/` and `/healthz`, sets security headers and the verified client address | `frontend/` | [Reverse proxy and TLS](../deployment/reverse-proxy-tls.md) |
| REST API | versioned endpoints, validation, error model, CSRF, rate limits, OpenAPI | `backend/app/api/` | [REST API](../api/index.md) |
| Authentication | local accounts (Argon2id, TOTP MFA), LDAP / Active Directory, OIDC, API tokens; server-side sessions in PostgreSQL | `app/services/auth.py`, `ldap_auth.py`, `oidc.py`, `mfa.py`, `api_tokens.py` | [Identity providers](../administration/identity-providers.md) |
| Authorization | permission checks against role assignments at scopes (platform … schema), filtered in SQL | `app/services/authz.py` | [RBAC](../administration/rbac.md) |
| Audit | append-only `audit_events`; optional forwarding to a SIEM | `app/services/audit.py`, `siem.py` | [Audit](../administration/audit.md) |
| Adapter layer | one adapter per engine behind a common contract and capability model | `app/adapters/` | [Database engines](../database-engines/index.md) |
| Scheduler and workers | one leader schedules (advisory lock on a dedicated connection), workers claim jobs (`SKIP LOCKED`) | `app/jobs/` | [Scaling](../deployment/scaling.md#several-workers-scheduler-leadership) |
| Metric pipeline | normalization, rate derivation, batch writes, health | `app/services/collection.py`, `metric_catalog.py`, `app/timeseries/` | [Pipeline](../monitoring/pipeline.md) |
| Recommendation engine | deterministic performance rules over evidence and history (61 rules) | `app/advisors/performance/` | [Recommendations](../performance/recommendations.md) |
| Security engine | deterministic security rules (55 rules), privileged-account snapshots | `app/advisors/security/` | [Security](../user-guide/security.md) |
| Alerting | rules → alert instances → notifications, maintenance windows, retries | `app/services/alerting.py`, `notify.py` | [Alerting architecture](alerting.md) |
| Backup and restore | platform and database backups, verification, retention, restore with trial run | `app/backup/` | [Backup & Restore](../backup-restore/index.md) |
| Reports | report definitions, CSV/PDF, scheduled e-mail delivery | `app/services/reports.py`, `report_pdf.py`, `report_schedules.py` | [Reports](../user-guide/reports.md) |
| Secret providers | encrypted-at-rest credentials, `env:` / `file:` references, Vault | `app/services/secrets.py`, `vault.py` | [Secret providers](../administration/secret-providers.md) |
| Control plane | PostgreSQL: inventory, identities, roles, credentials (encrypted with the master key), findings, alerts, backup catalog, audit, settings, job queue | `app/models/`, `backend/alembic/` | [Design document](design-document.md#4-data-model) |
| Time-series database | VictoriaMetrics: all metric history and query-statistics counters | `app/timeseries/` | [Monitoring architecture](../monitoring/architecture.md) |
| Simulation | generates a simulated fleet that never connects anywhere | `app/simulation/` | [Simulation](../administration/simulation.md) |
| Onboarding | setup wizard, database onboarding, bulk import, IdP wizard | `app/services/onboarding.py` | [Onboarding](../user-guide/onboarding/index.md) |

## Collection pipeline

```mermaid
flowchart LR
  SCH["scheduler leader<br/>interval classes + jitter"] -->|enqueue, deduplicated| Q[(collector_jobs)]
  Q -->|FOR UPDATE SKIP LOCKED| W["worker<br/>DBM_COLLECTOR_CONCURRENCY"]
  W --> CB{"circuit breaker<br/>per server"}
  CB -->|closed| A["adapter<br/>connect + statement timeouts"]
  A --> T[(target database)]
  A --> C["collector<br/>counters → rates, health"]
  C --> PGC[("PostgreSQL<br/>latest_metrics · health ·<br/>collection status")]
  C --> N["normalizer<br/>names · bounded labels · fingerprints"]
  N --> BW["batch writer<br/>buffer · retry · backoff"]
  BW --> TS[(VictoriaMetrics)]
  TS --> ADV["advisors<br/>windows · baselines · forecasts"]
  PGC --> ADV
  ADV --> F[("recommendations ·<br/>security findings")]
```

Job kinds per server: `collect_metrics` (the server's interval), `collect_query_stats` (slow
class), `discover_inventory` (very slow class), `generate_recommendations` (15 min) and
`security_scan` (6 h). Global jobs run on the leader: `evaluate_alerts`, `notification_retries`,
`siem_export`, `backup_scheduler`, `report_scheduler`, `validate_recommendations`, `retention` and
`timeseries_deletes` (`app/jobs/handlers.py`). A missing privilege limits one component; it never
takes the server offline. Details: [Pipeline](../monitoring/pipeline.md),
[Collectors](../monitoring/collectors.md).

## Request and authentication flow

```mermaid
sequenceDiagram
  participant C as Browser / API client
  participant P as Reverse proxy
  participant N as frontend nginx
  participant A as backend API
  participant DB as PostgreSQL
  C->>P: HTTPS request
  P->>N: HTTP, X-Forwarded-For appended
  Note over N: trusts X-Forwarded-For only from DBM_OUTER_PROXY<br/>then replaces it with one address
  N->>A: /api/... with X-Forwarded-For: client
  Note over A: client_ip: read right to left past DBM_TRUSTED_PROXIES
  alt Bearer API token
    A->>DB: look up token hash, expiry, CIDR allow-list
    Note over A: no CSRF (no ambient cookie)<br/>permissions = owner's grants ∩ token scopes<br/>interactive-only endpoints refused
  else session cookie
    A->>DB: resolve dbm_session (hash, expiry, idle timeout)
    Note over A: unsafe methods need X-CSRF-Token<br/>matching the session's CSRF hash<br/>MFA enrollment enforced when required
  end
  A->>A: rate limit per principal (api, target, expensive classes)
  A->>A: Authorizer.require(permission, scope)
  A->>DB: query, audit event (with client address)
  A-->>C: JSON response, or an error object with code, message and request_id
```

Sign-in (`POST /api/v1/auth/login` for local and LDAP accounts, or the OIDC code flow with PKCE)
creates the server-side session and sets the `dbm_session` (HttpOnly) and `dbm_csrf` cookies; see
[design document §2.4](design-document.md#24-authentication-architecture). The client address is
used for the audit trail, per-IP sign-in limits and API-token allow-lists, which is why it is
derived only through trusted proxies ([Reverse proxy and TLS](../deployment/reverse-proxy-tls.md#how-the-client-address-is-determined)).

## Alert evaluation and notification

```mermaid
flowchart LR
  EV["evaluate_alerts<br/>every DBM_ALERT_EVALUATION_SECONDS"] --> R[enabled rules]
  R --> S["servers in scope<br/>(monitoring enabled)"]
  S --> IN{"input"}
  IN -->|latest| LM[("servers.latest_metrics")]
  IN -->|avg / min / max / z-score| TSA[("VictoriaMetrics window")]
  IN -->|health / collector| H[("health state,<br/>collection status")]
  LM --> COND{condition}
  TSA --> COND
  H --> COND
  COND -->|no data| KEEP[alert unchanged]
  COND -->|holds| AL["pending → firing<br/>(one active alert per rule + server)"]
  COND -->|clears| RES[resolved, auto]
  AL --> MW{"maintenance<br/>window?"}
  MW -->|yes| SUP["suppressed: skipped delivery,<br/>notified after the window"]
  MW -->|no| ROUTE["routing: rule channels + subscribe_all,<br/>min severity, environment/tag/engine filters"]
  RES --> ROUTE
  ROUTE --> DEL["deliver: webhook / e-mail<br/>retry 60 s, 5 min, 15 min, 1 h"]
```

Alerts of servers that left a rule's scope are resolved (`resolved_by = scope`). Backup events use
the same channels and delivery code but are **not alerts**: they never appear on the Alerts page
([Backup notifications](../backup-restore/policies.md#notifications)). Details:
[Alerting architecture](alerting.md).

## Backup and restore flow

```mermaid
flowchart TB
  REQ["request: Run now, policy schedule<br/>(backup_scheduler), Verify, Restore"] --> JOB[(backup_jobs<br/>QUEUED)]
  JOB --> W["worker: DBM_BACKUP_CONCURRENCY,<br/>heartbeat every minute"]
  W --> KIND{kind}
  KIND -->|platform| EXP["export tables in one<br/>REPEATABLE READ snapshot"]
  EXP --> CK1{{cancel checkpoint}}
  CK1 --> ARC[build tar archive + manifest]
  ARC --> CK2{{cancel checkpoint}}
  KIND -->|database| ASSET["per asset (server, database):<br/>cancel checkpoint, engine tool or<br/>server-side BACKUP"]
  CK2 --> ENC[age encryption]
  ASSET --> ENC
  ENC --> STORE[(storage target)]
  STORE --> VER["read back: SHA-256,<br/>contents when decryptable"]
  VER --> OK[SUCCESS / CORRUPTED]
  W -->|StorageError| SU["FAILED:<br/>Backup storage unavailable"]
  OK --> RET[retention for policy backups]
  W -.events.-> NOTIF["notification channels<br/>(policy, or backup.notification_channels)"]

  RQ[restore request] --> PREV["preview (audited):<br/>manifest, integrity, compatibility,<br/>trial restore rolled back"]
  PREV --> CONF["typed confirmation<br/>platform: safety backup first<br/>(422 safety_backup_required if skipped)"]
  CONF --> APPLY["apply in one transaction /<br/>restore into a new database"]
  APPLY --> VAL[validate row counts]
```

Visibility of backups, jobs, policies and targets follows the caller's `backup.view` scope.
Details: [Backup architecture](../backup-restore/architecture.md),
[Restore](../backup-restore/restore.md).

## Process roles

The backend image runs as `DBM_PROCESS_ROLE=all` (API, scheduler and workers in one process; the
Docker Compose default), `api` or `worker` (`python -m app.worker`). Kubernetes deployments run
separate API and worker Deployments — see [Deployment](../deployment/index.md#kubernetes-openshift)
and [Scaling](../deployment/scaling.md).

## Further reading

* [Design document](design-document.md) — requirements, decisions and the full architecture
* [Alerting architecture](alerting.md)
* [UX audit](ux-audit.md)
