# Onboard an environment in bulk

**Goal:** all servers of one environment (for example *UAT*) are registered in one pass, placed in
the right environment, site and cluster, and monitored.

## Before you start

* Permission `server.register`; credential references need `credentials.manage`; creating the
  environment needs `inventory.manage` (platform).
* The environment exists: **Overview → Fleet → Environments → New environment** (or
  **+ Add → Environment**). An unknown environment in the file is an **error**; unknown sites,
  clusters and applications are only warnings and are created at registration.
* A monitoring account on every server, and its password where the backend can read it: an
  environment variable or mounted file on the backend and worker, a HashiCorp Vault path, or an
  already registered server whose credential is copied. **Bulk files never contain passwords.**

## Steps

1. Open **Overview → Fleet → Bulk onboarding** (or **+ Add → Bulk onboarding**).
2. Select **CSV template** to download `servers-template.csv` and fill one row per server:

    ```csv
    name,engine,host,port,environment,site,cluster,application,default_database,tls_mode,management_mode,credential,tags,description
    pg-uat-01,postgresql,10.20.1.11,5432,UAT,dc-paris,pg-uat,orders,postgres,verify-full,monitoring,env:PGUAT_MONITOR@dbm_monitor,team=orders,
    pg-uat-02,postgresql,10.20.1.12,5432,UAT,dc-paris,pg-uat,orders,postgres,verify-full,monitoring,server:pg-uat-01,team=orders,
    ```

    `credential` is one of `server:<registered server>`, `env:<VARIABLE>@<user>`,
    `file:<path>@<user>` or `vault:<[mount:]path#key>@<user>`, or empty: the server is then
    registered without monitoring until you add a credential on its Settings tab.
    `management_mode` is `monitoring` (default) or `administration`. `tags` are
    `key=value;key2=value2`. `environment` is required in every row.
3. Under **1. Provide servers**, select **CSV file** and drop the file (or **Choose file**). The
   rows appear in the grid. You can also work in **Grid** (**Add row**) or paste **JSON** and select
   **Load into grid**. Columns that look like secrets are removed in the browser.
4. Under **2. Validate and register**, select **Validate rows**. The badges show how many rows are
   valid, invalid and with warnings; cells with problems are highlighted. Fix them in the grid and
   select **Validate rows** again.
5. Leave **Start monitoring** on to schedule collection immediately.
6. Select **Register N server(s)**. Invalid rows are skipped.

## What you should see

* A progress bar, then one result per row with **Open** for each registered server, or the error.
* The servers in **Overview → Fleet → All servers**, filtered by the environment, and in
  **Overview → Topology** under the environment, site and cluster.
* One audit event per server plus one `server.bulk_register` event.

## If it does not work

* *already registered*, *same endpoint as row N*, *duplicate of row N*: a name or an
  `engine + host + port` appears twice in the file or is already registered.
* *unknown environment … (create it first)* or *environment is required*: create the environment,
  or correct the name.
* A credential reference is refused: environment variable names starting with `DBM_`, `POSTGRES_`,
  `PG`, `AWS_`, `AZURE_` or `GOOGLE_` are reserved; files must be under `DBM_SECRET_FILE_DIRS`;
  Vault needs `DBM_VAULT_ADDR` ([Secret providers](../../administration/secret-providers.md)).
* Registered servers show *Unavailable*: test one from its **Settings** tab and see
  [Connections](../../troubleshooting/connections.md).

Reference: [Bulk onboarding](../onboarding/bulk-onboarding.md),
[Environments and sites](../onboarding/environments.md).
