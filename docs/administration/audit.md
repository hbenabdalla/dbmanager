# Audit

The audit log is append-only in the control plane (`audit_events`) and is not time-limited by the
retention jobs. Viewing needs `audit.view` at platform scope; exporting needs `audit.export`.

What is recorded and how to search it: [Audit (user guide)](../user-guide/audit.md). For long-term
retention or a SIEM, export CSV periodically (`GET /api/v1/audit/export.csv`) or ship the backend's
JSON logs, which carry the same `request_id`.
