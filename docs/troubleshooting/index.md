# Troubleshooting

Start with **Platform → Diagnostics** (component health, versions) and the server's
**Overview → Collection status** (last result per component). Every API error carries a
`request_id`; search the backend logs for it (`docker compose logs backend | grep <request_id>`).

| Area | Page |
|---|---|
| Server unavailable, authentication, TLS, privileges | [Connections](connections.md) |
| SQL Server ODBC driver, MySQL/MariaDB TLS not negotiated, Oracle TCPS, privileges and licensed-pack refusals, Db2 connection, one database per registration and monitoring privileges | [Engine-specific problems](engines.md) |
| What the real-engine validation found per engine (ODBC, DMV permissions, Oracle PDB, Db2 readiness and MON_GET, performance_schema, MariaDB differences, backup failures) | [Real-engine findings](real-engine-findings.md) |
| Missing metrics, paused collection, time-series database unavailable | [Monitoring](monitoring.md) |
| LDAP and OIDC sign-in | [Identity providers](identity.md) |
| Startup, `.env`, cookies, master key, lost admin password, client IP behind a proxy, `safety_backup_required`, webhook URL not shown, docs build | [Platform](platform.md) |
| Backup and restore errors | [Backup & Restore → Troubleshooting](../backup-restore/troubleshooting.md) |
