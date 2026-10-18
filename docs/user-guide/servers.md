# Servers

A server page has a header (health, *Simulated* / *Monitoring only* / *Administration enabled*
badges, engine, version, environment, cluster role, last collection), **live metric cards** for
the last hour, and tabs. Tabs the server's engine or your role cannot use are disabled with the
reason (for example *Requires privilege: grant VIEW SERVER STATE*).

| Tab | Content |
|---|---|
| Overview | key metrics, 24 h charts, collection status per component, capabilities found by discovery |
| Performance | all metric charts with time range, aggregation and auto-refresh — [Performance](performance.md) |
| Databases / Schemas | inventory and object browser — [Databases, schemas and objects](databases-and-objects.md) |
| Sessions / Locks | [Sessions and locks](sessions-and-locks.md) |
| Queries | top statements of this server — [Queries and plans](queries-and-plans.md) |
| Replication / Storage | [Replication](replication.md), [Storage](storage.md) |
| Users / Privileges | [Users and privileges](users-and-privileges.md) |
| Recommendations / Security | findings for this server |
| Configuration | engine parameters with defaults and pending changes |
| Administration | guarded actions, only on *Administration enabled* servers |
| Audit | audit events about this server |
| Settings | connection, TLS, credentials (write-only), placement, management mode, delete |

**Collect now** queues an immediate collection (permission `collectors.manage`) and resets a
paused circuit breaker.
