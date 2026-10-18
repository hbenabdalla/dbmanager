# Add your first database

## 1. Create a monitoring account on the database

Use a dedicated, least-privileged account — never `postgres`, `root`, `sa`, `SYS` or the Db2
instance owner. Example for PostgreSQL:

```sql
CREATE ROLE dbm_monitor LOGIN PASSWORD '…';
GRANT pg_monitor TO dbm_monitor;
-- optional, for query statistics (needs shared_preload_libraries = 'pg_stat_statements'):
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

The grants for every engine are on the [engine pages](../database-engines/index.md).

## 2. Run the onboarding wizard

**Fleet → All servers → Add server** (or **+ Add → Database server**) opens a nine-step wizard:
engine, connection, TLS, credentials, management mode, placement, live test, review, done.

![Add database wizard](../assets/screenshots/add-database-wizard.png)

* The **live test** streams each stage — validation, driver, DNS, TCP, TLS, authentication,
  version, privileges, capabilities — so a failure points at the exact layer.
* **Monitoring only** is the default management mode. Choose *Administration enabled* only when DBAs
  will run guarded changes from DB Manager; it needs a separate administration credential.

Details: [Database onboarding](../user-guide/onboarding/database-onboarding.md).

## 3. Watch the first collection

The server appears in the fleet immediately. Metrics arrive within a minute; query statistics after
the first five-minute cycle; recommendations and security findings after their first runs
(every 15 minutes and every 6 hours; **Collect now** on the server page triggers a collection).

Many servers at once: [Bulk onboarding](../user-guide/onboarding/bulk-onboarding.md).

!!! tip "Try it on the bundled PostgreSQL"
    With `DBM_SAMPLE_MONITOR_PASSWORD` set before the first start, the control-plane database gets a
    `dbm_monitor` role. Register host `postgres`, port `5432`, database `dbmanager`, TLS `prefer`,
    user `dbm_monitor`.
