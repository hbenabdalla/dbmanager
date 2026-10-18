# Security

![Security posture](../assets/screenshots/security-posture.png)

* **Security posture** — a score per server (100 minus 25 per critical, 10 per high, 4 per medium
  and 1 per low open finding) and the fleet average; *Good / Fair / Poor* bands filter the server
  list; severity tiles and categories open the matching findings; averages by environment and engine.
* **Findings** — results of the security rules (accounts, privileges, authentication, encryption,
  auditing, surface, lifecycle) with evidence and remediation; acknowledge or dismiss with a reason.
* **Privileged accounts** — administrative database accounts across the fleet with the engine's own
  reason (superuser, `sysadmin`, DBA role, DBADM …), login ability and last login where known.
* **Compliance** — findings grouped by control area.

Security scans run every 6 hours and on demand through `POST /api/v1/servers/{id}/security-scan`. The rules per
engine are listed on the [engine pages](../database-engines/index.md); how the platform itself is
protected is described in [Security](../security/index.md).
