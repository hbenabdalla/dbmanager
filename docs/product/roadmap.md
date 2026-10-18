# Product roadmap

Milestone-based; no calendar dates are promised. Every item traces back to a gap in the
[gap analysis](gap-analysis.md) and forward to code and tests in the
[traceability matrix](roadmap-traceability.md). An item is **DONE** only with a working
implementation and automated tests — never because a menu entry, a static endpoint or a page
of documentation exists.

| Status | Meaning |
|---|---|
| DONE | Implemented, tested, documented |
| IN PROGRESS | Being implemented in the current iteration |
| NEXT | First in line once in-progress work lands |
| PLANNED | Scheduled in a milestone, not started |
| FUTURE | Accepted direction, not yet scheduled |
| BLOCKED | Waiting for an external dependency or decision |

## Where the product stands

The current codebase is a feature-rich **pre-release (target version 0.9.0, "public preview")**.
It already contains the capability groups below; they are listed as the baseline, not as past
releases.

| Baseline capability group | Status |
|---|---|
| Platform core: FastAPI backend, PostgreSQL control plane, Alembic, bootstrap, settings, health, Prometheus self-metrics | DONE |
| Identity & access: local auth with lockout, sessions + CSRF, LDAP/AD and OIDC (not real-validated), group mapping, scoped RBAC, audit with CSV export | DONE |
| Seven engine adapters behind one contract with capability discovery; six real-engine validated (2026-10-18); Db2 for z/OS partial | DONE |
| Telemetry: collectors, scheduler with leader election, circuit breaker, normalized metric catalog, VictoriaMetrics storage, retention | DONE |
| Query performance: statistics, fingerprints, history, graphical execution plans | DONE |
| Advisors: 59 performance + 55 security rules with evidence, confidence, lifecycle; security posture | DONE |
| Guarded administration (Monitoring Only default, preview → confirm → execute) | DONE |
| Simulation: deterministic demo fleets, isolated from real data | DONE |
| UX: command-center dashboard, topology, replication topology, wizards (setup, LDAP, OIDC, onboarding, bulk), command palette, dark mode | DONE |
| Real-engine test environment (`docker-compose.test.yml`, `scripts/integration-test`) | DONE |

---

## Milestone 0.9 — Release Foundation

*Goal: a public, installable, documented, legally usable pre-release.* Closes P0 gaps G-01 … G-07.

### Sprint 0.9.1 — Repository & governance · DONE
- **Objective:** publishable repository for `hbenabdalla/dbmanager`.
- **Features:** Git history with logical commits, README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, CHANGELOG, issue forms, PR template, `.gitignore` hardening, secret scan.
- **Technical tasks:** `git init`, `origin` remote, ignore rules for env files, volumes, coverage and results; scan tree for credentials.
- **Dependencies:** GitHub access (push). License decided by the owner: Apache-2.0.
- **Tests:** secret-scan script exits clean; CI runs on the first push.
- **Documentation:** README, contribution guide, release process.
- **Acceptance criteria:** clean clone builds and tests; no secrets in history; templates render on GitHub.
- **Definition of Done:** pushed to `main` (or exact push steps documented if no credentials), Apache-2.0 `LICENSE` committed.

### Sprint 0.9.2 — Continuous integration · DONE
- **Objective:** every pull request validated automatically.
- **Features:** workflows for backend (ruff + pytest unit/API against a PostgreSQL service), frontend (typecheck, vitest, production build), docs (`mkdocs build --strict`), Docker build, dependency audit.
- **Technical tasks:** `.github/workflows/ci-*.yml`, caching, PostgreSQL service container, no secrets required.
- **Dependencies:** 0.9.1.
- **Tests:** workflows green on `main`.
- **Documentation:** CONTRIBUTING "Testing" section.
- **Acceptance criteria:** a failing test blocks merge; real-engine suite stays a manual/dispatch workflow (no Oracle/Db2 z/OS in PR CI).
- **Definition of Done:** branch protection documented; badges in README.

### Sprint 0.9.3 — Documentation site · DONE
- **Objective:** Read the Docs–ready, audience-organized documentation.
- **Features:** MkDocs Material, `.readthedocs.yaml` (v2), pinned `docs/requirements.txt`, getting started, user/admin guides, engine pages, monitoring, security, architecture, API, development, operations, troubleshooting, product pages.
- **Dependencies:** none (RTD import needs the owner's account).
- **Tests:** `mkdocs build --strict` in CI.
- **Acceptance criteria:** a new user installs and onboards a database using only the docs.
- **Definition of Done:** strict build passes; published at [https://db-manager.readthedocs.io/en/latest/](https://db-manager.readthedocs.io/en/latest/).

### Sprint 0.9.4 — Version & product metadata · DONE
- **Objective:** users and support know exactly what is running.
- **Features:** single version source (0.9.0), build/commit and schema version via API, About page (version, engines, backend status, docs/GitHub/roadmap links, license), configurable `DBM_DOCUMENTATION_URL`, `DBM_GITHUB_URL`, `DBM_ROADMAP_URL`, Documentation entry in the app menu.
- **Dependencies:** none.
- **Tests:** API test for the version endpoint (no sensitive build details); UI test for the About page and the documentation link.
- **Acceptance criteria:** no guessed Read the Docs URL — the link was set once the project `db-manager` existed; installations can override it or hide it (`off`).
- **Definition of Done:** CHANGELOG entry, capability matrix updated.

### Sprint 0.9.5 — Upgrade & recovery verification · DONE
- **Objective:** upgrades and restores are proven, not assumed.
- **Features:** CI job applying all migrations from an empty database and from the previous release's schema; `scripts/backup.sh` / `restore.sh` (PostgreSQL dump, VictoriaMetrics snapshot, master key reminder); restore drill test.
- **Dependencies:** 0.9.2.
- **Tests:** migration upgrade/downgrade test; scripted backup → wipe → restore → login + inventory intact.
- **Documentation:** operations guide: upgrade, backup, restore.
- **Acceptance criteria:** restore reproduces users, servers, encrypted credentials (with the same master key).
- **Definition of Done:** runbook verified end to end in CI or in the test environment.

---

## Milestone 0.10 — Operations & Alerting

*Goal: problems reach people without someone watching the dashboard.* Closes G-10 … G-17, G-23.

### Sprint 0.10.1 — Alert engine · DONE
- **Objective:** evaluate alert rules against normalized metrics and server health.
- **Features:** rule model (metric, scope by server/environment/tag, comparison, threshold, sustained-for duration, severity), evaluator job, de-duplicated alert instances.
- **Technical tasks:** Alembic migration, scheduler job, time-series queries, RBAC permissions (`alerts.view`, `alerts.manage`), audit events.
- **Dependencies:** time-series query API (exists).
- **Tests:** evaluator unit tests (thresholds, sustained windows, recovery), API tests incl. RBAC.
- **Documentation:** user guide "Alerts", architecture page.
- **Acceptance criteria:** a rule fires once, stays firing while the condition holds, resolves automatically.
- **Definition of Done:** works on simulated and real servers; no static/demo-only data paths.

### Sprint 0.10.2 — Notification channels (SMTP, webhook) · DONE
- **Objective:** deliver alerts outside the UI.
- **Features:** channel model, SMTP (STARTTLS/TLS), generic JSON webhook with HMAC signature, routing rules → channels, test-send, retry with backoff, delivery log.
- **Dependencies:** 0.10.1.
- **Tests:** SMTP against a local test server, webhook against a local receiver, secret masking.
- **Documentation:** administration guide "Notifications".
- **Acceptance criteria:** channel secrets encrypted and never returned; delivery failures visible.
- **Definition of Done:** audited configuration changes.

### Sprint 0.10.3 — Maintenance windows & suppression · DONE
- **Objective:** no alert noise during planned work.
- **Features:** one-off and recurring windows scoped to servers/environments/tags; suppressed alerts recorded but not notified.
- **Dependencies:** 0.10.1.
- **Tests:** window boundaries, scope matching, time zones.
- **Acceptance criteria:** alerts raised in a window are marked suppressed and notify after it ends only if still firing.
- **Definition of Done:** visible on server pages.

### Sprint 0.10.4 — Alert UX & chat presets · PLANNED
- **Features:** alert inbox with acknowledge/resolve/comment, dashboard and server badges, Teams and Slack webhook presets, simple re-notify escalation.
- **Dependencies:** 0.10.1–0.10.3.
- **Tests:** UI tests for the lifecycle; payload snapshot tests per preset.
- **Acceptance criteria:** acknowledging stops re-notification and is audited.

### Sprint 0.10.5 — Collector diagnostics · PLANNED
- **Features:** per-server diagnostic bundle (last errors, timings, capability snapshot, driver versions), redacted download.
- **Tests:** redaction tests (no credentials, no connection strings with secrets).

---

## Milestone 0.11 — Enterprise Integration

*Closes G-18, G-19, G-33, G-36, G-38, G-40.*

| Sprint | Scope | Status |
|---|---|---|
| 0.11.1 API tokens | Scoped, hashed, expiring personal tokens; `Authorization: Bearer`; audited; UI to create/revoke (service accounts later) | DONE |
| 0.11.2 API rate limiting | Per-principal limits on expensive endpoints, `429` with `Retry-After`, limiter shared across backend instances | NEXT |
| 0.11.3 Identity validation | OpenLDAP + Keycloak in `docker-compose.test.yml`; real login, group mapping, OIDC flow tests | PLANNED |
| 0.11.4 SIEM export | Audit event stream via webhook and syslog (RFC 5424) | PLANNED |
| 0.11.5 Secret managers | HashiCorp Vault provider (KV v2), then cloud secret stores | PLANNED |
| 0.11.6 MFA readiness | Enforce MFA through OIDC `amr`/`acr` claims; native TOTP for local admins | PLANNED |

Each sprint follows the same template: objective, features, technical tasks, dependencies, tests,
documentation, acceptance criteria (as above), Definition of Done = code + tests + docs +
capability matrix + changelog.

## Milestone 0.12 — Performance Intelligence

| Sprint | Scope | Status |
|---|---|---|
| 0.12.1 Baselines in alerts/rules | Baseline-relative alert conditions (z-score) | PLANNED |
| 0.12.2 Query regression detection | Per-fingerprint latency and plan-change detection, findings | PLANNED |
| 0.12.3 Capacity forecasting | Robust trend with confidence bands, days-to-full per tablespace/database | PLANNED |
| 0.12.4 Query comparison | Period-vs-period and plan-vs-plan comparison views | PLANNED |
| 0.12.5 Recommendation validation | Before/after measurement when a finding is resolved | PLANNED |

## Milestone 0.13 — Reporting

| Sprint | Scope | Status |
|---|---|---|
| 0.13.1 Report coverage | Security findings and query-statistics report definitions | PLANNED |
| 0.13.2 Scheduled reports | Schedules delivered by SMTP (requires 0.10.2) | PLANNED |
| 0.13.3 PDF export | Server-side PDF rendering of reports | PLANNED |

## Milestone 0.14 — Cloud-Native Deployment

| Sprint | Scope | Status |
|---|---|---|
| 0.14.1 Container images | Published, versioned, signed images; SBOM | PLANNED |
| 0.14.2 Helm chart | Chart with external PostgreSQL/VictoriaMetrics options, migrations hook | PLANNED |
| 0.14.3 Manifest validation | kubeconform + kind smoke test in CI; OpenShift restricted-v2 check | PLANNED |
| 0.14.4 HA & TLS reference | Multi-replica API, worker leader failover test, TLS termination and internal TLS guide | PLANNED |

## Milestone 1.0 — Production Release

| Sprint | Scope | Status |
|---|---|---|
| 1.0.1 Scale testing | 1,000+ simulated servers: collector throughput, UI responsiveness, TSDB cardinality | PLANNED |
| 1.0.2 Security review | Threat model review, dependency audit, authenticated DAST, fixes | PLANNED |
| 1.0.3 Accessibility | axe in CI, WCAG 2.1 AA fixes | PLANNED |
| 1.0.4 Upgrade & recovery drill | Upgrade from every 0.x schema; backup/restore drill | PLANNED |
| 1.0.5 Release candidate | Freeze, RC builds, documentation review, `v1.0.0` tag | PLANNED |

## Future

| Item | Status |
|---|---|
| Anomaly detection on baselines | FUTURE |
| Custom recommendation rules (declarative) | FUTURE |
| Custom dashboards | FUTURE |
| Custom read-only metric collectors | FUTURE |
| Database logs (alert/error logs) | FUTURE |
| Cloud-API database discovery | FUTURE |
| OpenTelemetry traces | FUTURE |
| Plugin framework, operator | FUTURE |
| Oracle TCPS real validation | PLANNED (needs a TCPS listener in the test environment) |
| Db2 for z/OS real validation; thread, lock and subsystem monitoring | BLOCKED (z/OS subsystem + Db2 Connect) |

## Known limitations of the current pre-release

- Alerting has no Teams/Slack presets or re-notification escalation yet (sprint 0.10.4).
- LDAP, Active Directory and OIDC are implemented but not validated against real identity providers.
- Reports export CSV only and cannot be scheduled.
- API requests are not yet rate-limited per token or user beyond authentication failures (sprint 0.11.2).
- Kubernetes/OpenShift manifests are provided but not validated in CI; no Helm chart.
- Db2 for z/OS support is partial; Oracle TCPS has not been exercised against a real listener.
