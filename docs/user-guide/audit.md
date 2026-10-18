# Audit

**Security → Audit logs** (`audit.view`, platform scope) records sign-ins (success and failure),
configuration and RBAC changes, server registration and credential changes, SQL workspace
executions with classification, administration actions with their statements (passwords masked),
cancel/terminate, denied mutating requests (`access.denied`), simulation runs and identity-provider
tests.

Filter by time, category, actor, server, action and result; each event has a request id that also
appears in the backend logs. **Export CSV** downloads the filtered events
(`GET /api/v1/audit/export.csv`, permission `audit.export`; the export itself is audited). A server's **Audit** tab shows its own events.

Audit details are sanitised: keys that look like secrets are removed before storage.
