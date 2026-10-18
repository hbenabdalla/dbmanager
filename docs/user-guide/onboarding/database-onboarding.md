# Database onboarding wizard

![Add database wizard](../../assets/screenshots/add-database-wizard.png)

**Fleet → All servers → Add server** registers one server in nine steps:

1. **Engine** — PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, Db2 LUW, Db2 for z/OS (partial).
   If the backend lacks the engine's driver the engine shows *driver missing* and the remediation
   (for SQL Server: *Microsoft ODBC Driver 18 is required*).
2. **Connection** — host, port, default database and the engine's own options (Oracle service name
   or SID and protocol; SQL Server instance, encryption, application intent; Db2 schema …).
3. **TLS** — mode (`disable`, `prefer`, `require`, `verify-ca`, `verify-full`) and an optional CA
   certificate (PEM).
4. **Credentials** — the monitoring account (see the grants on each [engine page](../../database-engines/index.md)).
   Passwords are encrypted at rest and never shown again.
5. **Management mode** — **Monitoring only** (default) or **Administration enabled**. The second needs
   an administration credential and allows guarded changes; it is never enabled automatically.
6. **Placement** — environment, site, cluster and role, application, tags; licensable features
   (Oracle Diagnostics / Tuning Pack flags stay off unless you record a licence).
7. **Test connection** — a live, streamed test: validate → driver → DNS → TCP → TLS →
   authenticate → version → privileges → capabilities. Each stage turns ok, warn or fail; the first
   failure stops the test and names the layer. Capability results are grouped by level
   (FULL, PARTIAL, requires privilege/configuration/licence …).
8. **Review** — everything that will be stored.
9. **Done** — open the server or add another.

A server can be registered even when the test fails (for example before firewall rules are in
place); it then shows *Unavailable* until it can be reached.
