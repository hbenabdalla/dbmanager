# Features

| Area | What you get | Learn more |
|---|---|---|
| Dashboard | KPIs (servers by health, databases and size, sessions with a 24 h trend, critical findings, open advice, active alerts); fleet map, engines and environments act as filters | [Dashboard](../user-guide/dashboard.md) |
| Fleet | servers, clusters, databases, environments, placement (sites, clusters, applications), tags; bulk onboarding from a grid, CSV or JSON | [Fleet](../user-guide/fleet.md) |
| Topology | interactive Environment → Site → Cluster → Server → Database map; replication diagram | [Topology](../user-guide/topology.md) |
| Monitoring | live metric cards, charts from 15 minutes to 90 days, drag-to-zoom, health status, capacity forecast | [Performance](../user-guide/performance.md) |
| Queries | top statements per engine, statement history, slow queries, regressions, period comparison, graphical execution plans, plan comparison, SQL workspace | [Queries and plans](../user-guide/queries-and-plans.md) |
| Sessions and locks | active sessions, blocking chains, cancel/terminate with confirmation | [Sessions and locks](../user-guide/sessions-and-locks.md) |
| Recommendations | performance, query and configuration advice with evidence (61 rules), outcome measurement after resolution | [Recommendations](../user-guide/recommendations.md) |
| Security | posture score, findings (55 rules), privileged accounts, compliance view with failing servers per rule | [Security](../user-guide/security.md) |
| Alerting | threshold, window, z-score (baseline), health and collector rules; maintenance windows; webhook (generic, Slack, Teams) and e-mail channels | [Alerting](../user-guide/alerting.md) |
| Administration | guarded, previewed, confirmed and audited changes per engine, on servers registered with administration enabled | [Users and privileges](../user-guide/users-and-privileges.md) |
| Backup and restore | platform backups of DBManager's own configuration, database backups with the engines' own tools, verification, policies with retention, storage targets (directory, NFS, S3), restore wizards | [Backup and restore](../user-guide/tasks/backup-restore-database.md) |
| Reports | 13 reports as CSV or PDF, preview, schedules e-mailed with the owner's access | [Reports](../user-guide/reports.md) |
| Identity | local accounts, LDAP/AD, OIDC (Keycloak, Entra ID, Okta, generic), group mapping, TOTP multi-factor authentication, personal API tokens | [Identity providers](../administration/identity-providers.md) |
| RBAC | permissions scoped to platform, environment, site, cluster, server, database or schema | [RBAC](../administration/rbac.md) |
| Audit | every sign-in, change, denied action and administration statement; CSV export; forwarding to a SIEM (syslog, webhook) | [Audit](../user-guide/audit.md) |
| Demo | a clearly labelled, removable simulated fleet | [Demo environment](demo-environment.md) |

Not in the product yet: SAML sign-in, cloud secret managers (AWS, Azure, Google), point-in-time
recovery of database backups, service accounts for API tokens, Db2 for z/OS thread and lock
monitoring. The full list is in the [gap analysis](../product/gap-analysis.md) and the
[capability matrix](../product/capability-matrix.md).
