# User guide

The user guide has two parts:

* **[Task guides](tasks/index.md)** — step-by-step instructions for the common jobs: register a
  database, onboard many servers, investigate a slow query, respond to an alert, review security,
  back up and restore, schedule a report, give a colleague access.
* **Page reference** — what each page of the application shows, following the sidebar.

What you see depends on your role: pages and actions you have no permission for are hidden, and
servers outside your scope do not exist for you.

## Sidebar

The sidebar has five groups. Groups and sections can be collapsed; **Collapse navigation** at the
bottom switches the sidebar to an icon rail.

| Group | Section → pages | Reference |
|---|---|---|
| Overview | Dashboard · Topology · Fleet (All servers, Clusters, Databases, Environments, Placement, Bulk onboarding) · Reports | [Dashboard](dashboard.md), [Topology](topology.md), [Fleet](fleet.md), [Reports](reports.md) |
| Operations | Alerts (Alerts, Alert rules, Maintenance windows) · Monitoring (Overview, Performance, Capacity forecast, Sessions, Locks, Replication, Replication topology) · Queries (Query performance, Slow queries, Regressions, Compare periods, Execution plans, Compare plans, SQL workspace) · Administration (Databases, Schemas, Objects, Database users, Roles & privileges) | [Alerting](alerting.md), [Performance](performance.md), [Storage](storage.md), [Sessions and locks](sessions-and-locks.md), [Replication](replication.md), [Queries and plans](queries-and-plans.md), [Databases and objects](databases-and-objects.md), [Users and privileges](users-and-privileges.md) |
| Optimization | Recommendations (Performance, Query, Configuration, Outcomes) | [Recommendations](recommendations.md) |
| Security | Security (Security posture, Findings, Privileged accounts, Compliance) · Audit logs | [Security](security.md), [Audit](audit.md) |
| Platform | Setup (Setup wizard, Setup checklist) · Backup & Restore (Overview, Backup history, Jobs, Backup policies, Storage targets, Engine support, Create backup) · Configuration (Users, Roles, Identity providers, Identity wizard, OIDC, LDAP, Credentials, Collectors, Monitoring storage, Engine support, Database drivers, Settings, Simulation, Notification channels, Audit forwarding, About) | [Setup wizards](onboarding/index.md), [Backup and restore](../backup-restore/index.md), [Administration](../administration/index.md) |

Paths in this documentation are written as *Group → Section → Page*, for example
**Platform → Configuration → Settings**.

Every server has its own page with tabs — see [Servers](servers.md). Use **Ctrl/Cmd + K** to jump
anywhere ([Search and command palette](search-and-command-palette.md)).

## Real and simulated data

When a [simulated fleet](simulation.md) exists, simulated servers carry a *Simulated* badge and a
hatched pattern, and the source filter on fleet pages switches between *Real*, *Simulated* and *All*.
