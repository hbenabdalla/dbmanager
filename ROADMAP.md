# Roadmap

DB Manager **0.13.0** is the current release (**0.12.0** was the first public release). The
roadmap is milestone-based, and no dates are promised.

- The full roadmap, with sprint objectives, acceptance criteria and Definition of Done, is in [docs/product/roadmap.md](docs/product/roadmap.md).
- The milestone of this release: [docs/product/current-milestone.md](docs/product/current-milestone.md).
- The task-by-task check of each milestone against the code and tests is in the [roadmap audit](docs/product/roadmap-audit.md).
- The evidence behind both is in the [capability matrix](docs/product/capability-matrix.md) and the [real-engine validation report](docs/product/real-engine-validation.md).

| Status | Meaning |
|---|---|
| DONE | Implemented, tested (tests executed), documented |
| CURRENT | Being worked on |
| NEXT | Starts after the current milestone |
| PLANNED | Scheduled in a later milestone |
| FUTURE | Accepted, not yet scheduled |
| BLOCKED | Waiting for a named external dependency |

| Milestone | Theme | Status |
|---|---|---|
| Baseline | Seven engine adapters (six real-engine validated), telemetry on VictoriaMetrics, query analysis, advisor rules, scoped RBAC, audit, guarded administration, simulation, topology, wizards | DONE |
| 0.9 | **Release foundation**: repository and governance, CI, Read the Docs site, version metadata and About page, upgrade and recovery verification | DONE |
| 0.10 | **Operations & alerting**: alert engine, SMTP/webhook (Slack, Teams) notifications with retries and routing, recurring maintenance windows, alert inbox and re-notification, diagnostic bundles | DONE |
| 0.11 | **Enterprise integration**: API tokens, rate limiting, real LDAP / Keycloak (OIDC) / Vault validation, SIEM forwarding, HashiCorp Vault, MFA | DONE. Cloud secret managers are PLANNED (0.11.7). |
| 0.12 | **Asset protection**: backup and restore of the platform and of managed databases, policies, retention, verification, disaster recovery | DONE. Database backups and restores real-validated for all six engines (Db2 LUW restore is an external step). Native Azure/GCS targets and PITR are PLANNED. |
| 0.13 | **Performance intelligence**: z-score alert rules, query regression and plan-change detection, capacity forecasting with confidence bands, period and plan comparison, recommendation outcome validation | DONE (0.13.0) |
| 0.14 | Reporting: more report types, scheduled delivery, PDF | NEXT |
| 0.15 | Cloud-native: published images, Helm chart, manifest validation, HA and TLS reference | PLANNED |
| 1.0 | Production release: scale and security testing, accessibility, upgrade drill, release candidate | PLANNED |
| Future | Anomaly detection beyond z-score rules, custom rules and dashboards, database logs, cloud discovery, Oracle TCPS validation | FUTURE |

**Versioning:** the product version follows the roadmap. `0.13.0` adds milestone 0.13 to
`0.12.0`, which contained milestones 0.9 to 0.12. Versions 0.9 to 0.11 were development states and
were never released.

**Blocked:** Db2 for z/OS real validation, which needs a z/OS subsystem with Db2 Connect
(EXTERNAL VALIDATION REQUIRED).

The known limitations of this release are listed at the end of the
[full roadmap](docs/product/roadmap.md#known-limitations-of-the-current-release).
