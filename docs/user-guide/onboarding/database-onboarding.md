# Database onboarding wizard

![Add database wizard](../../assets/screenshots/database-onboarding-engine-selection.png)

**Overview → Fleet → All servers → Add server** (or **+ Add → Database server**) opens *Add a
database server*, which registers one server in nine steps. **Continue** moves to the next step
after it is validated; **Back** returns.

1. **Engine** — PostgreSQL, MySQL, MariaDB, SQL Server, Oracle, Db2 LUW, Db2 for z/OS (partial).
   If the backend lacks the engine's driver the engine shows *driver missing* and the remediation
   (for SQL Server: *Microsoft ODBC Driver 18 is required*).
2. **Connection** — host, port, default database and the engine's own options (Oracle service name
   or SID and protocol; SQL Server instance, encryption, application intent; Db2 schema …).
3. **TLS** — mode (`disable`, `prefer`, `require`, `verify-ca`, `verify-full`) and an optional CA
   certificate (PEM).
4. **Credentials** — the monitoring account (see the grants on each [engine page](../../database-engines/index.md)).
   Passwords are encrypted at rest and never shown again.
5. **Management mode** — **Monitoring Only** (default) or **Administration Enabled**. The second
   needs an **Administration user** and password and allows guarded changes; it is never enabled
   automatically. Servers registered through the API or a bulk file without a mode are also
   monitoring-only.
6. **Placement** — environment, site, cluster and role, application, tags; licensable features
   (Oracle Diagnostics / Tuning Pack flags stay off unless you record a licence).
7. **Test connection** — **Run test** starts a live, streamed test: validate → driver → DNS → TCP → TLS →
   authenticate → version → privileges → capabilities. Each stage turns ok, warn or fail; the first
   failure stops the test and names the layer. Capability results are grouped by level
   (FULL, PARTIAL, requires privilege/configuration/licence …). **Skip test** continues without it.
8. **Review** — everything that will be stored; **Register server** saves it.
9. **Done** — **Open server** or **Add another**.

A server can be registered even when the test fails (for example before firewall rules are in
place); it then shows *Unavailable* until it can be reached.
