# Roadmap

DB Manager **0.12.0** is the first public release. The roadmap is milestone-based, and no dates
are promised.

- The full roadmap, with sprint objectives, acceptance criteria and Definition of Done, is in [docs/product/roadmap.md](docs/product/roadmap.md).
- The task-by-task check of each milestone against the code and tests is in the [roadmap audit](docs/product/roadmap-audit.md).
- The evidence behind both is in the [capability matrix](docs/product/capability-matrix.md).

| Milestone | Theme | Status |
|---|---|---|
| Baseline | Seven engine adapters (six real-engine validated), telemetry on VictoriaMetrics, query analysis, 114 advisor rules, scoped RBAC, audit, guarded administration, simulation, topology, wizards | VERIFIED COMPLETE |
| 0.9 | **Release foundation**: repository and governance, CI, Read the Docs site, version metadata and About page, upgrade and recovery verification | VERIFIED COMPLETE |
| 0.10 | **Operations & alerting**: alert engine, SMTP/webhook (Slack, Teams) notifications with retries and routing, recurring maintenance windows, alert inbox and re-notification, diagnostic bundles | VERIFIED COMPLETE |
| 0.11 | **Enterprise integration**: API tokens, rate limiting, real LDAP validation, SIEM forwarding, HashiCorp Vault, MFA | VERIFIED COMPLETE. Keycloak/Vault real runs are BLOCKED until they run in the Docker integration environment. Cloud secret managers are PLANNED (0.11.7). |
| 0.12 | **Asset protection**: backup and restore of the platform and of managed databases, policies, retention, verification, disaster recovery | VERIFIED COMPLETE. Real runs for SQL Server, Oracle, Db2 LUW and MySQL are BLOCKED until they run in the Docker integration environment. Native Azure/GCS targets and PITR are PLANNED. |
| 0.13 | Performance intelligence: baselines in alerts, query regression detection, capacity forecasting, comparisons | PLANNED |
| 0.14 | Reporting: more report types, scheduled delivery, PDF | PLANNED |
| 0.15 | Cloud-native: published images, Helm chart, manifest validation, HA and TLS reference | PLANNED |
| 1.0 | Production release: scale and security testing, accessibility, upgrade drill, release candidate | PLANNED |

**Versioning:** the product version follows the roadmap. `0.12.0` contains milestones 0.9 to 0.12.
Versions 0.9 to 0.11 were development states and were never released.

**Blocked:** Db2 for z/OS real validation, which needs a z/OS subsystem with Db2 Connect.

The known limitations of this release are listed at the end of the
[full roadmap](docs/product/roadmap.md#known-limitations-of-the-current-release).
