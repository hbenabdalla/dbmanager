# Secret providers

Every server credential has a **secret source**. Choose it when registering a server (or in the
onboarding wizard) and on the server's **Settings** tab. **Platform → Configuration → Credentials**
shows which sources this installation offers and the state of Vault.

| Source | `provider` | What KBM DBManager stores | Where the password comes from |
|---|---|---|---|
| Encrypted in KBM DBManager | `local` | the password, AES-256-GCM envelope-encrypted with `DBM_MASTER_KEY` | the control-plane database |
| Environment variable | `env` | the variable name | the backend/worker environment (e.g. a Kubernetes Secret) |
| Mounted secret file | `file` | the file path | a file under `DBM_SECRET_FILE_DIRS` (default `/run/secrets`, `/var/run/secrets`, `/etc/dbmanager/secrets`) |
| HashiCorp Vault (KV v2) | `vault` | the reference `[mount:]path#key` | Vault, read when a connection is opened |

Passwords are never returned by the API, written to logs or put into audit events. Environment
variables whose names start with `DBM_`, `POSTGRES_`, `PG`, `AWS_`, `AZURE_` or `GOOGLE_` cannot be
used as database secrets: they belong to the platform itself.

The wizard's connection test **before** registration resolves an `env`, `file` or `vault`
reference on the backend, so the test shows whether the reference works. After registration,
**Test connection** on the server's Settings tab does the same with the stored reference.

## HashiCorp Vault

The Vault provider reads secrets from a **KV version 2** secrets engine. It is available once
`DBM_VAULT_ADDR` is set; otherwise the option is not offered and the API refuses `provider: vault`.

### References

```text
path/to/secret#key           # mount DBM_VAULT_KV_MOUNT (default "secret")
mount:path/to/secret#key     # an explicit KV v2 mount
```

Examples: `databases/pg-prod#password` reads key `password` of `secret/data/databases/pg-prod`;
`kv-dba:oracle/erp#password` reads it from the mount `kv-dba`. Mount and path use letters, digits,
`_`, `-`, `.` and `/`; `..` and leading `/` are refused. The latest version of the secret is read
(no version pinning). In bulk onboarding files the credential column is
`vault:<[mount:]path#key>@<username>`.

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `DBM_VAULT_ADDR` | empty (provider off) | e.g. `https://vault.example.com:8200` |
| `DBM_VAULT_AUTH` | `token` | `token`, `approle` or `kubernetes` |
| `DBM_VAULT_TOKEN` / `DBM_VAULT_TOKEN_FILE` | — | `token` auth: the token, or a file holding it (e.g. written by Vault Agent) |
| `DBM_VAULT_ROLE_ID`, `DBM_VAULT_SECRET_ID` / `DBM_VAULT_SECRET_ID_FILE` | — | `approle` auth |
| `DBM_VAULT_KUBERNETES_ROLE`, `DBM_VAULT_KUBERNETES_JWT_FILE` | JWT: `/var/run/secrets/kubernetes.io/serviceaccount/token` | `kubernetes` auth with the pod's service account |
| `DBM_VAULT_AUTH_MOUNT` | `approle` / `kubernetes` | path of the auth method if it is mounted elsewhere |
| `DBM_VAULT_NAMESPACE` | empty | Vault Enterprise namespace (sent as `X-Vault-Namespace`) |
| `DBM_VAULT_KV_MOUNT` | `secret` | KV v2 mount for references without one |
| `DBM_VAULT_CA_FILE` | system trust store | CA bundle for a Vault server certificate signed by a private CA |
| `DBM_VAULT_TIMEOUT_SECONDS` | `10` | per request |
| `DBM_VAULT_CACHE_SECONDS` | `60` (0–3600) | how long a resolved password is kept in memory; `0` reads Vault for every connection |

With AppRole and Kubernetes auth KBM DBManager logs in, keeps the issued token in memory and logs in
again shortly before its lease ends (or once when Vault answers 403). Give the KBM DBManager role a
policy that allows only `read` on the paths it needs, for example:

```hcl
path "secret/data/databases/*" {
  capabilities = ["read"]
}
```

Set the variables on the backend **and** the worker: both open database connections.

### Status and errors

`GET /api/v1/platform/secret-providers` (needs `credentials.manage`) reports each source; for
Vault it calls `sys/health` and returns whether it is configured, reachable, initialized and
sealed, its version, the auth method and the default KV mount. The Credentials page shows the
same information.

A failed read makes the connection fail with a message such as *secret secret/databases/pg not
found in Vault*, *Vault denied access to …*, *… has no key 'password'* or *Vault is not reachable*;
the server's collection and Test connection report it like any other connection error.

### Not supported

* KV version 1, dynamic database secrets engines and Vault leases for database credentials.
* Cloud secret managers (AWS Secrets Manager, Azure Key Vault, Google Secret Manager) are
  **planned, not implemented**. Until then, project their secrets into environment variables or
  mounted files (for example with the External Secrets Operator or the Secrets Store CSI driver)
  and use the `env` or `file` source.
