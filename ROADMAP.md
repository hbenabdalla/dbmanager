# Roadmap

DB Manager is a feature-rich **pre-release** (target 0.9.0, public preview). The roadmap is
milestone-based — no dates are promised. The full roadmap with sprint objectives, acceptance
criteria and Definition of Done is in [docs/product/roadmap.md](docs/product/roadmap.md); the
evidence behind it is in the [capability matrix](docs/product/capability-matrix.md) and the
[gap analysis](docs/product/gap-analysis.md).

| Milestone | Theme | Status |
|---|---|---|
| Baseline | Seven engine adapters (six real-engine validated), telemetry on VictoriaMetrics, query analysis, 114 advisor rules, scoped RBAC, audit, guarded administration, simulation, topology, wizards | DONE |
| **0.9** | **Release foundation** — repository & governance, CI, Read the Docs site, version metadata & About page, upgrade and backup/restore verification | IN PROGRESS — all sprints done except publishing (push, Read the Docs import) and the license decision |
| **0.10** | **Operations & alerting** — alert engine, SMTP/webhook notifications, maintenance windows, alert UX, collector diagnostics | IN PROGRESS — engine, SMTP/webhook channels and maintenance windows DONE; alert UX presets and collector diagnostics PLANNED |
| 0.11 | Enterprise integration — API tokens, rate limiting, real LDAP/OIDC validation, SIEM export, Vault, MFA readiness | IN PROGRESS — API tokens DONE; next: rate limiting, real LDAP/OIDC validation |
| 0.12 | Performance intelligence — baselines in alerts, query regression detection, capacity forecasting, comparisons | PLANNED |
| 0.13 | Reporting — more report types, scheduled delivery, PDF | PLANNED |
| 0.14 | Cloud-native — published images, Helm chart, manifest validation, HA and TLS reference | PLANNED |
| 1.0 | Production release — scale and security testing, accessibility, upgrade drill, release candidate | PLANNED |

**Blocked:** license selection (owner decision) · Db2 for z/OS real validation (needs a z/OS
subsystem with Db2 Connect).

Known limitations of the current pre-release are listed at the end of the
[full roadmap](docs/product/roadmap.md#known-limitations-of-the-current-pre-release).
