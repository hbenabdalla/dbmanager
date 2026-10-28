# Roadmap

KBM DBManager **0.16.0** is the release being prepared: the product's KBM corporate identity. **0.15.3** is the latest
published release, and **0.12.0** was the first. The roadmap is organised by milestone and promises no dates.

- [Full roadmap](docs/product/roadmap.md): sprints, acceptance criteria, Definition of Done.
- [Roadmap audit](docs/product/roadmap-audit.md): every claim checked against code, tests and
  documentation (the evidence lives there, not here).
- [Test validation](docs/product/test-validation.md): the clean validation of this release, with
  before/after numbers and the root cause of every failure.
- [Capability matrix](docs/product/capability-matrix.md),
  [engine matrix](docs/product/engine-matrix.md),
  [real-engine validation](docs/product/real-engine-validation.md).

## Released and verified

The 0.14.0 roadmap reconciliation re-audited every milestone below and did not take earlier DONE
statuses on trust. 62 claims were reopened because they were broken, partial, untested, or
present on only one side (UI or backend). All of them were fixed and verified again before
0.14.0.

| Milestone | Theme | Status |
|---|---|---|
| Baseline | Seven engine adapters, telemetry on VictoriaMetrics, query analysis, 116 advisor rules (61 performance, 55 security), scoped RBAC, audit, guarded administration, simulation, topology, wizards | VERIFIED COMPLETE (reconciled) |
| 0.9 | Release foundation: repository, CI, documentation site, version metadata, upgrade and restore drills | VERIFIED COMPLETE (reconciled) |
| 0.10 | Operations & alerting: alert engine, notifications, maintenance windows, alert inbox, diagnostics | VERIFIED COMPLETE (reconciled) |
| 0.11 | Enterprise integration: API tokens, rate limiting, LDAP/OIDC/Vault validation, SIEM forwarding, Vault, MFA | VERIFIED COMPLETE (reconciled); cloud secret stores PLANNED (0.11.7) |
| 0.12 | Asset protection: backup and restore of the platform and of managed databases | VERIFIED COMPLETE (reconciled); native Azure/GCS (0.12.6) and PITR (0.12.7) PLANNED |
| 0.13 | Performance intelligence: z-score rules, regressions, forecasts, comparisons, outcome validation | VERIFIED COMPLETE (reconciled) |
| 0.14 | Reporting (definitions, preview, PDF, schedules) and the former DBManager branding (replaced by the KBM identity in 0.16) | VERIFIED COMPLETE (release 0.14.0, published) |
| 0.15 | Cloud-native deployment: Helm chart, signed images with SBOM, manifest validation and kind install in CI, HA (concurrent start, instance registry, leader failover), internal TLS, dependency majors | VERIFIED COMPLETE (published as 0.15.1 – 0.15.3) |
| 0.16 | **KBM Corporate Rebranding & Visual Identity**: official name KBM DBManager, KBM logo and design tokens, light and dark KBM themes, KBM-styled reports, documentation theme, recaptured screenshots, rebuilt videos, brand guidelines and checks | VERIFIED COMPLETE (release 0.16.0) |

## Remediation

| Sprint | Scope | Status |
|---|---|---|
| R1 – R7 | Security, engine correctness, findings and alerts, backup & restore, runtime and CI, user interface, documentation and screenshots | VERIFIED COMPLETE, part of 0.14.0 ([audit](docs/product/roadmap-audit.md#remediation-sprints)) |

## Current

**0.16.0 release**: the KBM corporate rebrand — the workstream *KBM Corporate Rebranding & Visual
Identity* ([current milestone](docs/product/current-milestone.md),
[brand guidelines](docs/branding/kbm-dbmanager-brand-guidelines.md)). No API, configuration or data
changes. What remains is the publishing step: push and tag.

## Next

| Milestone | Theme | Status |
|---|---|---|
| 1.0 | Production release: scale and security testing, accessibility, upgrade drill from every 0.x schema, deferred dependency majors (Recharts 3, TypeScript 7, Python 3.14), release candidate | NEXT, not started |

## Future

| Milestone | Theme | Status |
|---|---|---|
| — | Anomaly detection beyond z-score rules, custom rules and dashboards, database logs, cloud discovery, OpenTelemetry | FUTURE |

## External dependencies

| Item | Needs | Status |
|---|---|---|
| Db2 for z/OS real validation; thread, lock and subsystem monitoring | A z/OS subsystem with Db2 Connect. Db2 LUW results are never used as z/OS evidence | BLOCKED (EXTERNAL VALIDATION REQUIRED) |
| Db2 LUW database restore | Runs on the Db2 host, outside KBM DBManager | EXTERNAL VALIDATION REQUIRED |
| Oracle TCPS | A TCPS listener in the test environment | FUTURE |
| Cloud secret stores (AWS, Azure, Google) | Cloud accounts to validate against | PLANNED (0.11.7) |

**Versioning:** the product version follows the milestones. 0.16.0 is a minor version under
semantic versioning (0.x): a customer-facing change of identity with visible text changes, no
migration and no removed endpoints. Changed behaviour is listed in the [changelog](CHANGELOG.md).
