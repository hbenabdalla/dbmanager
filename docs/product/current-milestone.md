# Current milestone

| | |
|---|---|
| **Milestone** | 0.14 — Reporting (with the DBManager branding and engine-identity workstream) |
| **Status** | DONE. Release **0.14.0** also contains the roadmap reconciliation (remediation sprints R1 – R7); only publishing remains |
| **Release target** | 0.14.0 (this release). Next: milestone 0.15 — Cloud-Native Deployment (**NEXT, not started**) |

## Objective

Give people the reports they pass on — security findings for the auditor, the heaviest statements
for the application team, regressions and storage forecasts for capacity reviews — as CSV or PDF,
on demand or e-mailed on a schedule, without ever sending more than the person the report runs for
may see. In the same release, give the product a consistent visual identity: the DBManager logo
everywhere the product introduces itself, and one engine mark per database engine everywhere a
server is listed — DBManager's own marks, because vendor logos need the vendors' permission.

## Roadmap reconciliation in this release

Before 0.14.0 was published, every earlier milestone was audited again from the code. The 61
claims that were not true were fixed in remediation sprints R1 – R7, and the
[roadmap audit](roadmap-audit.md) holds the evidence. The fixes change some behaviour:

- Servers registered without a management mode are monitoring-only. Migration `0013` keeps
  existing servers in administration mode.
- Webhook URLs and headers of notification channels are write-only.
- Behind a proxy, set `DBM_TRUSTED_PROXIES` (backend) and `DBM_OUTER_PROXY` (frontend nginx).
- A platform restore without a safety backup needs
  `backup.allow_restore_without_safety_backup`.
- Report schedules can be changed only by their owner, their creator or a user manager.

## Next milestone (not started)

**0.15 — Cloud-Native Deployment** is NEXT: published, signed images with an SBOM, a Helm chart,
manifest validation, an HA and TLS reference, and the dependency major upgrades. No work on it has
started. [Scope](roadmap.md#milestone-015-cloud-native-deployment-next).

## Sprints

| Sprint | Scope | Status |
|---|---|---|
| 0.14.1 Report coverage | New definitions *Security findings* (statuses, minimum severity, lifecycle dates, remediation), *Query statistics* (window 1–744 h, share of the server's time, masked text), *Query regressions*, *Capacity forecast* (per server / database / tablespace); environment, server and engine parameters for every report; 50-row preview; Reports page grouped by area with a generate dialog | DONE |
| 0.14.2 Scheduled reports | Schedules (daily / weekly / monthly at a time in a time zone) e-mailed as attachments through e-mail channels; each run re-loads the owner's roles — a disabled or deleted owner, or one who lost the report's permission, fails the run and nothing is sent; attachment size limit; status per run and per channel; *Send now*; history; audit `report.delivered`, `report_schedule.*`; permission `reports.schedule`; wizard; migration `0012` | DONE |
| 0.14.3 PDF export | Server-side PDF (ReportLab, BSD licence): logo, title, generated-for, time, scope and row count, header repeated on every page, landscape for wide tables, 5,000-row cap with the CSV as the complete export, data-classification footer; CSV values that a spreadsheet would execute are neutralised | DONE |
| 0.14.4 Branding & engine identity | DBManager logo (as provided, only scaled) on sign-in, setup wizard, sidebar (expanded and collapsed), About, loading and first-run screens, README and documentation; favicon, app icons and web manifest; `DatabaseEngineIcon` with DBManager's own engine marks (Db2 LUW and Db2 for z/OS distinct, unknown fallback, accessible names) replacing the letter badges across the product; optional operator-supplied licensed logos (`DBM_BRANDING_DIR`); [brand assets](../development/brand-assets.md) with trademark notice; product name *DBManager* in all current text | DONE |

## Decisions

* **No vendor logos in the product.** The PostgreSQL, Microsoft, IBM, Oracle/MySQL and MariaDB
  trademark policies do not allow their logos in a third-party product without permission.
  DBManager ships its own marks and lets a licensed organisation supply logos locally; see
  [brand assets](../development/brand-assets.md).
* **E-mail only for schedules.** Webhook channels (Slack, Teams, generic) cannot carry files; a
  schedule accepts e-mail channels only.
* **Owner's access, every run.** A schedule stores who it runs as, not a snapshot of their rights.
* **Version 0.14.0** (semantic versioning, minor): new features, an additive migration, no removed
  endpoints. `GET /reports` no longer has `planned_formats`; `fleet-health.pdf` and every other
  report now answer with a PDF instead of `422`.
* **MFA issuer.** The default `DBM_MFA_ISSUER` is now `DBManager`; existing authenticator entries keep
  their label and keep working.

## Dependencies

* E-mail notification channels and SMTP (milestone 0.10).
* Statement statistics, regression detection and capacity forecasts (milestone 0.13) for the new
  query and forecast reports.
* ReportLab 4.2+ in the backend image (pure Python; installed from `pyproject.toml`).
* Migration `0012` (applied automatically on upgrade).

## Acceptance criteria

| Criterion | Evidence |
|---|---|
| Every report downloads as CSV and as a valid PDF; unknown reports answer 404 and unknown formats 422 | `tests/api/test_reports.py::test_every_report_downloads_as_csv_and_pdf` |
| Parameters filter the rows (server, engine, minimum severity, statuses, window) and invalid values answer 422; scope is printed with names | `test_parameters_filter_and_validate` |
| Reports contain only servers the caller may see; the audit report needs platform `audit.view`; downloads are audited | `test_reports_respect_scope_and_permissions` |
| Query text is masked | `test_query_text_is_masked_in_query_statistics` |
| A schedule e-mails the PDF (and, after an edit, the CSV) to every recipient of its channels through a real SMTP server; runs, last status and audit are recorded | `test_schedule_lifecycle_delivers_pdf_by_email` (aiosmtpd) |
| A disabled owner fails the run with the reason and nothing is sent | `test_disabled_owner_fails_the_run_without_sending` |
| The scheduler runs a due schedule once and advances it | `test_scheduler_job_runs_due_schedules` |
| Invalid schedules (frequency, time, time zone, day of month, empty channels, webhook channel, unknown report or format) answer 422 | `test_schedule_validation` |
| The PDF renderer handles empty, wide, long and hostile content; CSV neutralises formulas but not numbers | `tests/unit/test_report_pdf.py` |
| The UI previews with parameters, downloads with the same scope, creates a schedule through the wizard, sends now and shows history | `frontend/src/test/reports.test.tsx` |
| Engine marks have accessible names, keep Db2 LUW and z/OS apart, fall back for unknown engines, use no external resources; licensed logos are shown unaltered and fall back on error | `frontend/src/test/engine-icon.test.tsx` |
| Licensed logos are served only for whitelisted names, with nosniff and a sandboxing CSP | `tests/api/test_branding.py` |
| Upgrade from 0.13.0 keeps all data | `tests/migrations/test_migrations.py::test_existing_installation_upgrades_to_head_with_its_data[previous]` |

## Next

Milestone **0.15 — Cloud-Native Deployment** (NEXT): published and signed images with SBOM, Helm
chart, manifest validation in CI, HA and TLS reference. See the [roadmap](roadmap.md).
