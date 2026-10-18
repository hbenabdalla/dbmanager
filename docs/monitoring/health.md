# Health calculation

Each collection computes a server's health from its latest metrics
(`app/services/metric_catalog.py: compute_health`):

| Condition | Warning | Critical |
|---|---|---|
| Server unreachable (`availability.up` < 1) | — | **unavailable** |
| Connection utilization | ≥ 80 % | ≥ 95 % |
| Replication lag | ≥ 60 s | ≥ 300 s |
| Blocked sessions | ≥ 1 | ≥ 5 |
| Buffer cache hit ratio | < 90 % | — |
| Storage utilization | ≥ 90 % | ≥ 95 % |
| CPU | ≥ 85 % | ≥ 95 % |

Any critical condition makes the server **critical**, otherwise any warning makes it **warning**,
otherwise **healthy**. *Unknown* means no successful collection yet. The reasons are stored with the
status and shown on the dashboard, in the server list and on the server header. Metrics the engine
does not provide (for example CPU on PostgreSQL) simply do not take part.
