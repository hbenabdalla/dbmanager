# Gap analysis

Each gap found by the [capability audit](capability-matrix.md) is assessed for whether it
belongs in the product at all, then classified. Gaps that do not belong are kept in the table
with the reasoning so the question does not have to be re-argued.

## Priority model

| Priority | Meaning |
|---|---|
| **P0** | Release blocker: the project cannot be published or released responsibly without it |
| **P1** | Critical product capability expected of a database operations platform |
| **P2** | Important enhancement |
| **P3** | Future enhancement |
| **P4** | Experimental / long-term |

Scales: business value, technical importance, security impact and operational impact are
**H / M / L**; complexity is **S** (days), **M** (one sprint), **L** (several sprints).

## Status after the current release work

Reconciled for 0.14.0: the closed gaps were re-checked against the code ([roadmap audit](roadmap-audit.md)); the status rows *G-41 – G-46* marked *(audit)* were found by that audit and are not the capability gaps with the same numbers in the table further down. Updated for 0.15.0: the cloud-native capability gaps are closed.


The tables below keep each gap as originally assessed. Progress since the audit:

| Gap | Status | Where |
|---|---|---|
| G-01 Git repository | Closed — published at [hbenabdalla/dbmanager](https://github.com/hbenabdalla/dbmanager) | sprint 0.9.1 |
| G-02 License | Closed — the owner chose Apache-2.0; `LICENSE` added | sprint 0.9.1 |
| G-03 CI | Closed — workflows run on GitHub; first-run failures (Compose health, gitleaks) fixed | sprint 0.9.2 |
| G-04 Version metadata | Closed — `0.16.0`, `/api/v1/version`, `/api/v1/about`, Platform → About | sprint 0.9.4 |
| G-05 Policies | Closed — CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, issue forms, PR template | sprint 0.9.1 |
| G-06 Publishable documentation | Closed — published on Read the Docs ([https://db-manager.readthedocs.io/en/latest/](https://db-manager.readthedocs.io/en/latest/)) | sprint 0.9.3 |
| G-07 Secret scan / ignore rules | Closed — tree scanned (only documented development/test placeholders), `.gitignore` hardened, gitleaks in CI | sprint 0.9.1 |
| G-10 … G-15 Alerting, lifecycle, notifications, SMTP, webhooks, maintenance windows | Closed | sprints 0.10.1–0.10.3 |
| G-18 API tokens | Closed — personal tokens (service accounts later) | sprint 0.11.1 |
| G-19 API rate limiting | Closed — per-principal policies, shared counters, `Retry-After` | sprint 0.11.2 |
| G-20 Backup/restore verification | Closed — `scripts/backup.sh`, `scripts/restore.sh`, automated round-trip test | sprint 0.9.5 |
| G-22 Upgrade & migration testing | Closed — step-by-step upgrade, downgrade, model parity, and upgrade of an installation with data from the first schema and from the published releases 0.12.0, 0.13.0 and 0.14.0 | sprint 0.9.5 (reconciled; 0.14.0 added in 0.15) |
| G-16, G-17 Slack/Teams presets, re-notify | Closed (re-notify; on-call chains stay with PagerDuty/Opsgenie) | sprint 0.10.4 |
| G-20, G-21 Backup/restore, configuration backup | Closed — in-app platform backup and restore by component with verification, plus stack scripts | milestone 0.12 |
| G-23 Collector diagnostics bundle | Closed | sprint 0.10.5 |
| G-33 SIEM integration | Closed — syslog RFC 5424 (UDP/TCP/TLS) and signed webhook | sprint 0.11.4 |
| G-36 External secret managers | Partly closed — HashiCorp Vault; cloud secret stores planned | sprints 0.11.5, 0.11.7 |
| G-38 MFA | Closed — native TOTP + recovery codes, OIDC amr/acr | sprint 0.11.6 |
| G-40 Real identity validation | Closed — OpenLDAP (local 2.6 and osixia/openldap 1.5.0 container), Keycloak 26 (OIDC code flow with PKCE) and Vault 1.17 validated in the Docker integration run of 2026-10-19 | sprint 0.11.3 |
| G-24 Baselines used by alerts | Closed — z-score alert rules against each server's own history | sprint 0.13.1 |
| G-27 Query regression detection | Closed — per-fingerprint latency regressions and plan changes (Oracle, SQL Server), rule `GEN-PERF-011` | sprint 0.13.2 |
| G-25 Capacity forecasting beyond linear trend | Closed for robust trends with confidence bands per server, database and tablespace; seasonality is not modelled | sprint 0.13.3 |
| G-28 Query comparison | Closed — period vs period, and plan vs plan of saved plans | sprint 0.13.4 |
| G-29 Recommendation outcome validation | Closed — before/after measurement of resolved recommendations | sprint 0.13.5 |
| G-32 Security and query-statistics reports | Closed — *Security findings*, *Query statistics*, *Query regressions* and *Capacity forecast* report definitions with parameters | sprint 0.14.1 |
| G-30 Scheduled reports | Closed — schedules e-mailed through e-mail channels with the owner's current access, run history, audit | sprint 0.14.2 |
| G-31 PDF export | Closed — server-side PDF for every report | sprint 0.14.3 |
| G-41 (audit) Client address behind proxies (P0) | Closed — right-to-left `X-Forwarded-For` past trusted proxies; nginx replaces the header | remediation R1 |
| G-42 (audit) Workspace in the selected database on MySQL/MariaDB (P0) | Closed — the connection switches to the selected database and back | remediation R2 |
| G-43 (audit) Secrets in webhook channels and audit (P1) | Closed — URLs and headers encrypted, write-only, never audited; existing channels migrated | remediation R1 |
| G-44 (audit) Backup visibility for scoped users (P1) | Closed — jobs, policies, targets and overview scoped; cancel needs the starting permission | remediation R4 |
| G-45 (audit) Advisor and alert state integrity (P1) | Closed — no auto-resolve without evaluation; out-of-scope alerts resolve; suppression released after windows | remediation R3 |
| G-46 (audit) Adapter depth (P2) | Closed — constraints (SQL Server, Oracle), roles across SQL Server databases, PDB users from root, Db2 grant catalogs, storage contract | remediation R2 |
| G-41 Helm chart | Closed — `deployment/helm/dbmanager`, signed OCI chart, bundled or external stores, OpenShift mode | sprint 0.15.2 |
| G-42 Manifests validated in CI | Closed — kubeconform, restricted Pod Security / restricted-v2 policy check, chart tests, kind install with `helm test` and leader failover (`ci-kubernetes.yml`) | sprint 0.15.3 |
| G-44 HA reference | Closed — multi-replica API and workers, concurrent-start locks, instance registry and Deployment card, leader failover tests ([high availability](../deployment/high-availability.md)) | sprint 0.15.4 |
| G-45 Internal TLS | Closed — `DBM_DATABASE_TLS_MODE` / `_CA_FILE` (tested with `verify-full` in CI), `DBM_TIMESERIES_TLS_CA_FILE`; edge TLS in the [reverse proxy guide](../deployment/reverse-proxy-tls.md) | sprint 0.15.4 |
| Published, signed images | Closed — GHCR images with SBOM, provenance and cosign signatures from `release.yml` | sprint 0.15.1 |
| G-59 Corporate identity | Closed — KBM DBManager name, KBM logo and tokens, themes, reports, documentation, screenshots, videos, brand guidelines and checks | milestone 0.16 |
| G-26 Anomaly detection | Open (P3) — z-score rules are the first building block; no automatic anomaly detection | Future |

## Release blockers (P0)

| ID | Gap | Value | Tech. | Security | Ops | Complexity | Dependencies | Risk if left |
|---|---|---|---|---|---|---|---|---|
| G-01 | No Git repository / remote; nothing is published | H | H | M | H | S | GitHub access for `hbenabdalla/dbmanager` | Work cannot be shared, reviewed or released |
| G-02 | No license chosen (`LICENSE` missing) | H | L | L | L | S | **Owner decision** (Apache-2.0 recommended: patent grant, permissive, common for infrastructure tools) | Code is "all rights reserved"; nobody may legally use or contribute — BLOCKED until decided |
| G-03 | No CI (tests, build, lint, docs) | H | H | M | H | S | G-01 | Regressions reach `main` unnoticed |
| G-04 | Version metadata wrong/absent (`1.0.0` hard-coded for a pre-release; no build, commit or schema version shown) | M | M | L | M | S | — | Users cannot tell what they run; support and upgrades guess |
| G-05 | No contribution, security disclosure or conduct policy | M | L | M | L | S | G-01 | Vulnerabilities reported publicly; contributors without guidance |
| G-06 | Documentation not publishable (no MkDocs / Read the Docs config, docs organized by history rather than audience) | H | M | L | H | M | — | New users cannot install or operate without reading source |
| G-07 | No pre-publication secret scan / `.gitignore` gaps (e.g. `.env.test`, integration volumes, coverage) | M | L | **H** | L | S | — | Credentials pushed to a public repository |

## Product capability gaps

| ID | Gap | Belongs in product? | Priority | Value | Tech. | Security | Ops | Complexity | Dependencies | Risk |
|---|---|---|---|---|---|---|---|---|---|---|
| G-10 | **Alerting engine** (threshold rules on normalized metrics and health, sustained-for windows, severities) | Yes — core expectation; monitoring without alerts requires humans watching dashboards | **P1** | H | H | L | H | M | Time-series query API (exists) | Outages noticed late |
| G-11 | **Alert lifecycle** (firing → acknowledged → resolved, history, de-duplication) | Yes | **P1** | H | H | L | H | M | G-10 | Alert floods, no accountability |
| G-12 | **Notification framework** (channels, routing, retries, test send) | Yes | **P1** | H | H | M (outbound secrets) | H | M | G-10 | Alerts invisible outside the UI |
| G-13 | **SMTP** channel | Yes | **P1** | H | M | M | M | S | G-12 | — |
| G-14 | **Webhooks** (generic JSON, HMAC signature) | Yes; also the integration path for Teams/Slack/PagerDuty | **P1** | H | M | M | M | S | G-12 | — |
| G-15 | **Maintenance windows / alert suppression** | Yes | **P1** | M | M | L | H | S | G-10 | Alert fatigue during planned work |
| G-16 | Teams / Slack native formatting | Yes, as channel presets over webhooks | P2 | M | L | L | M | S | G-14 | — |
| G-17 | Escalation policies (re-notify, on-call chains) | Partly — simple re-notify yes; on-call scheduling belongs to PagerDuty/Opsgenie | P3 | M | M | L | M | M | G-12 | — |
| G-18 | **API tokens** for automation (scoped, hashed, expiring, audited) | Yes | **P1** | H | H | **H** | M | M | RBAC (exists) | Automation shares human passwords |
| G-19 | API rate limiting beyond login | Yes | P2 | M | M | M | M | S | — | Abuse of expensive endpoints |
| G-20 | **Backup/restore verified procedure** (scripted, tested restore of PostgreSQL + master key) | Yes — as a tested runbook and script, not as an in-app feature | **P1** | H | M | M | **H** | S | — | Unrecoverable platform after loss |
| G-21 | Configuration backup / export (settings, inventory, roles as JSON) | Yes | P2 | M | M | M | M | M | — | Re-creating estates by hand |
| G-22 | **Upgrade path & migration testing** (alembic upgrade from previous release in CI, upgrade docs) | Yes | **P1** | H | H | L | H | S | G-03 | Broken upgrades in the field |
| G-23 | Collector diagnostics bundle (per-server last errors, timings, capability snapshot, redacted) | Yes | P2 | M | M | L | H | S | — | Slow support |
| G-24 | Performance baselines used by rules/alerts | Yes (endpoint exists; integrate) | P2 | M | M | L | M | M | G-10 | — |
| G-25 | Capacity forecasting beyond linear trend | Yes (seasonality-aware later) | P2 | M | M | L | M | M | History in TSDB | — |
| G-26 | Anomaly detection | Maybe — only after baselines prove useful | P3 | M | M | L | M | L | G-24 | False positives erode trust |
| G-27 | Query regression detection (per-fingerprint latency/plan change) | Yes | P2 | H | M | L | M | M | Fingerprint history (exists) | — |
| G-28 | Query comparison (period vs period) | Yes | P3 | M | L | L | L | S | G-27 | — |
| G-29 | Recommendation outcome validation (before/after) | Yes | P3 | M | M | L | L | M | Baselines | — |
| G-30 | Scheduled reports (email delivery) | Yes | P2 | M | L | L | M | M | G-13 | — |
| G-31 | PDF export | Yes, after scheduling | P3 | M | L | L | L | M | — | — |
| G-32 | Security and query-statistics report definitions | Yes | P2 | M | L | M | L | S | — | — |
| G-33 | SIEM integration (syslog/CEF or webhook stream of audit events) | Yes | P2 | M | M | **H** | M | M | G-14 | Audit trail isolated from the SOC |
| G-34 | Prometheus re-export of managed-database metrics | Partly — VictoriaMetrics already offers a Prometheus-compatible API; document federation instead of duplicating | P3 | M | L | L | M | S | — | — |
| G-35 | OpenTelemetry traces (OTLP) | Maybe | P4 | L | M | L | M | M | — | — |
| G-36 | External secret managers (HashiCorp Vault, cloud KMS/secret stores) | Yes (`vault` provider reserved) | P2 | H | M | **H** | M | M | — | Credentials only in the app DB/env |
| G-37 | Credential rotation for target databases (scheduled / on demand) | Yes | P3 | M | M | H | M | M | G-36 | — |
| G-38 | MFA | Readiness yes (delegate to OIDC); native TOTP later | P2 (TOTP P3) | M | M | **H** | L | M | — | Local admin accounts with password only |
| G-39 | Configurable password policy (length, history, expiry) | Yes | P3 | L | L | M | L | S | — | — |
| G-40 | Real LDAP / AD / OIDC validation (test IdPs in the integration environment: OpenLDAP, Keycloak) | Yes | P2 | H | M | **H** | L | M | `docker-compose.test.yml` | Identity bugs found by customers |
| G-41 | Helm chart | Yes | P2 | M | M | L | H | M | Container images published | — |
| G-42 | Kubernetes/OpenShift manifests validated in CI (kubeconform, kind) | Yes | P2 | M | M | L | M | S | G-03 | Manifests rot |
| G-43 | Operator | No for now — Helm covers the need; reconsider at scale | P4 | L | M | L | M | L | G-41 | — |
| G-44 | High availability reference architecture (2+ API replicas, worker leader election, external HA PostgreSQL/VM) | Yes, documentation + tests of leader failover | P2 | M | M | L | H | M | — | — |
| G-45 | TLS for platform components / reverse proxy guidance (HTTPS termination, HSTS, internal TLS to PostgreSQL/VM) | Yes | P2 | M | M | **H** | M | S | — | Cleartext sessions on untrusted networks |
| G-46 | Certificate management (ACME) | No — leave to ingress / reverse proxy | NOT APPLICABLE | | | | | | | |
| G-47 | Network database discovery | Maybe (cloud APIs first) | P4 | M | M | M | L | L | — | — |
| G-48 | Plugin / extension framework | Not before 1.0; adapter + rule interfaces are already extension points | P4 | M | M | M | L | L | — | — |
| G-49 | Custom recommendation rules (user-defined, declarative) | Yes, after the rule DSL stabilizes | P3 | M | M | M | L | M | — | — |
| G-50 | Custom metrics (user SQL collectors) | Risky (arbitrary SQL against production) — later, read-only and reviewed | P4 | M | M | H | M | M | — | — |
| G-51 | Custom dashboards | Yes, later | P3 | M | L | L | L | L | — | — |
| G-52 | Data export (full platform data, GDPR-style) | Partly (audit + reports exist) | P3 | L | L | M | L | S | G-21 | — |
| G-53 | Data purge (per-server telemetry deletion) | Yes | P3 | L | L | M | L | S | — | — |
| G-54 | Accessibility audit (axe in CI, WCAG 2.1 AA review) | Yes | P2 | M | L | L | L | S | G-03 | — |
| G-55 | Oracle TCPS real validation | Yes | P2 | M | L | M | L | S | TCPS listener (wallet) in the test environment | — |
| G-56 | Db2 for z/OS real validation and thread/lock monitoring | Yes, partial support is honest today | P3 | M | M | L | M | L | **BLOCKED**: a z/OS subsystem + Db2 Connect | — |
| G-57 | Database logs (alert log, error log) | Yes | P3 | M | M | L | M | M | Engine-specific access paths | — |
| G-58 | Load / scale testing (1,000+ servers) | Yes before 1.0 | P2 | M | H | L | H | M | Simulation profiles (exist) | Unknown limits |
| G-59 | Corporate identity: the product presented as a KBM offering (name, logo, design system, documentation, reports, videos) | Yes | P1 | H | L | L | L | M | KBM brand material | Product not recognisable as KBM's |

## Already covered (no gap)

External PostgreSQL (`DBM_DATABASE_URL`) and external time-series database
(`DBM_TIMESERIES_URL`); data retention policies (monitoring-storage retention API); licensing
policy for Oracle packs; Prometheus metrics of the platform itself; bulk registration.

## Summary

* **P0:** G-01 … G-07 — repository, license (closed: Apache-2.0), CI, version metadata, policies,
  publishable documentation, secret hygiene.
* **P1:** alerting (G-10, G-11), notifications with SMTP and webhooks (G-12 … G-14), maintenance
  windows (G-15), API tokens (G-18), verified backup/restore (G-20), upgrade testing (G-22).
* The [roadmap](roadmap.md) schedules these; [traceability](roadmap-traceability.md) links each gap
  to its sprint, code and tests.
