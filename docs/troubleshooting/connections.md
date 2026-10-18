# Connections

| Symptom | Check |
|---|---|
| Server *Unavailable* | Overview shows the last error; test the connection from the server's **Settings**; verify network policy / firewall from the backend container (`docker compose exec backend …`) |
| *Collection paused after repeated failures* | the circuit breaker opened after 3 consecutive failures ([Collectors](../monitoring/collectors.md#circuit-breaker)); fix the cause, then **Collect now** |
| Authentication failed | the account, password and (Oracle) service name / (Db2) database name; errors are normalized across engines: `AUTHENTICATION_FAILED`, `DATABASE_NOT_FOUND`, `TLS_ERROR`, `CONNECTION_FAILED`, `INSUFFICIENT_PRIVILEGES`, `QUERY_TIMEOUT`, `DRIVER_NOT_INSTALLED`, with the engine's message (passwords masked) |
| Certificate error | with TLS mode `verify-full` the server certificate must chain to the uploaded CA and match the host name (or *Host name in certificate*); use `require` (encrypt without validation) only for self-signed test instances |
| A component shows *Insufficient privilege* | the server stays connected; grant what capability discovery names (see the [engine page](../database-engines/index.md) and [least-privilege accounts](../database-engines/permissions.md)), then **Collect now** |
| A tab says *Not supported* / *Requires configuration* | the capability's note says why (engine edition, missing extension, configuration); see [Engine support](../administration/engine-support.md) |
| Administration actions are disabled | the server needs an *administration* credential and management mode, and your role the matching permission |
