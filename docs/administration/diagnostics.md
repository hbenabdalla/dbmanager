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

## Diagnostics bundle

For support cases, **Server → Settings → Operations → Download diagnostics** (permission
`collectors.view` on the server) downloads one JSON file, `dbm-diagnostics-<server>.json`
(API: `GET /api/v1/servers/{id}/diagnostics`). It contains:

* the server's settings: engine and version, environment, host, port, default database, TLS mode,
  management mode, collection interval and connection options;
* its logins, without secrets: purpose, masked username, secret provider and whether a secret is stored;
* health: status and reasons, connection status, last error, last connection and collection,
  consecutive failures and circuit-breaker state;
* collector state per component and the detected capabilities;
* the last 50 collector jobs with kind, status, duration, error and result;
* driver, DB Manager, Python and database schema versions.

Passwords, tokens, keys and `user:password@` in connection strings are replaced by `***` wherever
they appear, including error messages, options and job results. Review the file before sending it
anyway. Every download is audited (`server.diagnostics`).

The same screen offers **Test connection** (staged DNS, TCP, TLS, sign-in, version and privilege
checks with the stored settings) and **Scan security now**.

Logs: JSON lines on stdout of each container (`docker compose logs backend`), with `request_id` and
user. Details and common problems: [Health and logs](../operations/health-and-logs.md),
[Troubleshooting](../troubleshooting/index.md).
