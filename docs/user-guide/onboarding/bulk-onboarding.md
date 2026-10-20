# Bulk onboarding

**Overview → Fleet → Bulk onboarding** (`server.register`) registers many servers at once.

1. **1. Provide servers** — choose **Grid** (edit rows; **Add row**), **CSV file** (drop a file or
   **Choose file**) or **JSON** (paste, then **Load into grid**). Download the template with
   **CSV template** (`GET /api/v1/servers/bulk/template.csv`).
2. **2. Validate and register** — **Validate rows** checks every row on the server and highlights
   errors and warnings in the cells; fix them in place and validate again. Invalid rows are skipped.
3. **Register N server(s)** registers the valid rows with a progress bar and a result per row
   (**Open** links to each new server). **Start monitoring** (on by default) schedules collection
   immediately.

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
| `vault:<[mount:]path#key>@<username>` | the password is read from HashiCorp Vault KV v2 at connection time (needs `DBM_VAULT_ADDR`) |

Leave the column empty to register without a credential (and without monitoring) and add one later
on the server's Settings tab. Using a credential reference needs `credentials.manage`.

Validation rules: a duplicate name or `engine + host + port` (in the file or already registered) is
an error; a missing or unknown environment is an error; a row without a credential is registered
without monitoring (warning); unknown site, cluster or application is a warning and is
created; a missing port defaults to the engine's port; `management_mode` is `monitoring` (default)
or `administration` — `administration` is accepted with a warning that change actions will be offered.
Every created server is audited, plus one `server.bulk_register` event.
