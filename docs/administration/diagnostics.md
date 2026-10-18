# Diagnostics

| Where | What |
|---|---|
| `GET /healthz` | liveness |
| `GET /readyz` | readiness with per-component status (`application`, `postgresql` with `schema_version`, `timeseries`) |
| `GET /metrics` | Prometheus metrics of the platform itself |
| Platform → Collectors | scheduler leader, pending and failed jobs per kind, servers paused by the circuit breaker |
| Platform → Monitoring storage | time-series database status and ingestion |
| Platform → Database drivers | driver availability |
| Server → Overview → Collection status | per-component state (ok, insufficient privilege, not supported, error) |
| `app.cli metrics status` | time-series status from the CLI |

Logs: JSON lines on stdout of each container (`docker compose logs backend`), with `request_id` and
user. Details and common problems: [Health and logs](../operations/health-and-logs.md),
[Troubleshooting](../troubleshooting/index.md).
