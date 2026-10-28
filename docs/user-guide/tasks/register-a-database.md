# Register a database

**Goal:** one database server is registered, collected every minute (or the interval you choose)
and visible in the fleet.

## Before you start

* Permission `server.register` (Platform Administrator holds it; grant it narrowly, because it lets
  the backend connect to any host). Storing a password also needs `credentials.manage`.
* A least-privileged **monitoring account** on the database server, created with the grants on the
  [engine page](../../database-engines/index.md) — never `postgres`, `root`, `sa`, `SYS` or the Db2
  instance owner.
* Network access from the KBM DBManager backend to the server's host and port.
* For *Administration enabled*: a second, separate **administration account**.

## Steps

1. Open **Overview → Fleet → All servers** and select **Add server** (or **+ Add → Database server**
   in the top bar, or *Add a database server* in the command palette). The *Add a database server*
   wizard opens.
2. **Engine** — select the engine. An engine marked *driver missing* cannot be connected until the
   driver is installed ([Database drivers](../../administration/drivers.md)). **Continue**.
3. **Connection** — enter **Name** (for example `pg-orders-prod-01`), **Host**, **Port**, the default
   database and the engine's own options (Oracle service name or SID; SQL Server instance and
   encryption; Db2 schema …). **Continue**.
4. **TLS** — choose the TLS mode; `verify-full` is preselected. Paste a **CA certificate (PEM)** if
   the server certificate is signed by a private CA. **Continue**.
5. **Credentials** — enter the **Monitoring user**, choose the **Secret source** (encrypted in
   KBM DBManager, environment variable, mounted file or, when configured, HashiCorp Vault) and the
   password or reference. Leave **Collect metrics** on and choose the **Collection interval**.
   **Continue**.
6. **Management mode** — keep **Monitoring Only** unless DBAs will run guarded changes from
   KBM DBManager. **Administration Enabled** needs **Administration user** and **Administration
   password**. **Continue**.
7. **Placement** — choose **Environment**, **Site**, **Cluster**, **Role in cluster**,
   **Application**, **Owner** and **Tags** (`team=payments, pci=true`). Environment and tags decide
   which alert rules, backup policies and role assignments apply. **Continue**.
8. **Test connection** — select **Run test**. Each stage (validate, driver, DNS, TCP, TLS,
   authenticate, version, privileges, capabilities) turns ok, warn or fail. With a secret source
   other than *encrypted in KBM DBManager*, the backend resolves the reference for the test. Select
   **Continue**, or **Skip test** to register before the network is ready.
9. **Review** — check the summary and select **Register server**.
10. **Done** — select **Open server**, or **Add another**.

## What you should see

![Server page after the first collection](../../assets/screenshots/server-detail-real-postgresql.png)

* The server appears in **All servers** immediately; until the first collection it shows no
  metrics. Metrics arrive within one collection interval, query statistics after the first
  five-minute cycle, recommendations and security findings after their first runs (every
  15 minutes and every 6 hours). **Collect now** on the server page starts a collection at once.
* The header shows the badge *Monitoring only* or *Administration enabled*.
* The **Overview** tab lists the collection status per component and the capabilities found; a
  capability that needs more privileges says which grant is missing.
* The registration is in the audit log (`server.register`).

## If it does not work

* A stage of the test fails: the failing stage names the layer — see
  [Connections](../../troubleshooting/connections.md) and
  [Engine-specific problems](../../troubleshooting/engines.md).
* The server is registered but shows *Unavailable*: the server page shows the last error;
  **Settings → Operations → Test connection** repeats the staged test with the stored settings.
* Tabs are disabled with *Requires privilege*: add the grant named in the tooltip to the
  monitoring account ([Least-privilege accounts](../../database-engines/permissions.md)).
* No metrics after several minutes: [Monitoring problems](../../troubleshooting/monitoring.md).

Reference: [Database onboarding wizard](../onboarding/database-onboarding.md),
[Servers](../servers.md).
