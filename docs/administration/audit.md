# Audit

The audit log is append-only in the control plane (`audit_events`) and is not time-limited by the
retention jobs. Viewing needs `audit.view` at platform scope; exporting needs `audit.export`.

What is recorded and how to search it: [Audit (user guide)](../user-guide/audit.md). To send audit
events to a SIEM continuously (syslog RFC 5424 or a signed webhook), configure
[Audit forwarding](siem.md). CSV export (`GET /api/v1/audit/export.csv`) and the backend's JSON logs,
which carry the same `request_id`, remain available.
