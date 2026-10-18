# Bulk onboarding

**Fleet → Bulk onboarding** registers many servers at once.

1. **Provide servers** — edit rows in the grid, upload a CSV (or JSON) file, or paste JSON.
   Download the template with **CSV template** (`GET /api/v1/servers/bulk/template.csv`).
2. **Validate rows** — the server checks every row and highlights errors and warnings in the
   cells; fix them in place and validate again.
3. **Register** — valid rows are registered with a progress bar and a result per row. *Start
   monitoring* (on by default) schedules collection immediately.

Columns: `name, engine, host, port, environment, site, cluster, application, default_database,
tls_mode, management_mode, credential, tags, description` (`tags` as `key=value;key2=value2`).

!!! danger "Never put passwords in bulk files"
    Any column that looks like a secret (`password`, `pwd`, `secret`, `token`, `key` …) is removed in
    the browser and rejected by the server. Credentials are referenced instead.

The `credential` column accepts:

| Form | Meaning |
|---|---|
| `server:<registered server>` | copy that server's monitoring credential |
| `env:<VARIABLE>@<username>` | the password is read by the backend from an environment variable (names starting with `DBM_`, `POSTGRES_`, `PG`, `AWS_`, `AZURE_`, `GOOGLE_` are refused) |
| `file:<path>@<username>` | the password is read from a file under `DBM_SECRET_FILE_DIRS` |

Validation rules: duplicate `engine + host + port` (in the file or already registered) is an error;
an unknown environment is an error; unknown site, cluster or application is a warning and is
created; a missing port defaults to the engine's port; `management_mode` defaults to monitoring only.
Every created server is audited, plus one `server.bulk_register` event.
