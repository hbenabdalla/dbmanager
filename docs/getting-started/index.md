# Introduction

DBManager gives database administrators, platform teams and auditors one web application for a
heterogeneous database estate:

* **Fleet inventory** — servers, clusters, databases, schemas, objects, users and grants, organized
  by environment, site, cluster and application.
* **Monitoring** — connections, sessions, locks, throughput, latency, cache efficiency, storage,
  replication, collected on a schedule and stored as time series.
* **Query performance** — top statements from each engine's own statistics, history per statement,
  execution plans (graph, tree and raw vendor plan).
* **Recommendations and security findings** — deterministic, engine-aware rules with evidence,
  confidence and risk; nothing is executed automatically.
* **Alerting** — threshold, z-score, health and collector rules, maintenance windows, webhook and
  e-mail notifications.
* **Administration** — guarded actions (create user/role/schema/index, grant, revoke, cancel or
  terminate a session) only on servers explicitly registered as *Administration enabled*. Servers
  are registered as *Monitoring only* unless you choose otherwise.
* **Backup and restore** — backups of DBManager's own configuration and of managed databases,
  verified after writing, with policies, retention and guided restores.
* **Reports** — CSV and PDF reports, e-mailed on a schedule.
* **Platform** — RBAC with scopes, local accounts, LDAP/Active Directory, OpenID Connect, MFA, API
  tokens, audit log with SIEM forwarding, a simulated demo fleet and guided setup wizards.

## Who it is for

| Role | Typical use |
|---|---|
| DBA | investigate a slow server, find blocking sessions, review plans, run guarded changes |
| Platform / SRE team | watch fleet health, capacity and replication, onboard servers in bulk |
| Security / audit | review privileged accounts, security findings, compliance and the audit trail |
| Backup operator | run and verify database backups, restore a database into a new database |
| Application owner | read-only view of the servers of one application or environment |

## How it works

The browser talks to a FastAPI backend. The backend stores its own state in PostgreSQL (the
*control plane*), metric history in VictoriaMetrics, and connects to your databases through one
adapter per engine. See the [architecture overview](architecture-overview.md).

Next: [Features](features.md) · [Requirements](requirements.md) · [Installation](installation.md)
