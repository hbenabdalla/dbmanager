# Current milestone

| | |
|---|---|
| **Milestone** | 0.16 — KBM Corporate Rebranding & Visual Identity |
| **Status** | DONE. Release **0.16.0**; publishing (push, tag) remains |
| **Release target** | 0.16.0 (this release). Next: milestone 1.0 — Production Release (**NEXT, not started**) |
| **Previous release** | 0.15.3 (Cloud-Native Deployment; 0.15.1 was the first published 0.15 release) |

## Objective

Make the product officially **KBM DBManager**, an authentic Khorafi Business Machines (KBM)
offering, in everything customers see — application, documentation, reports, screenshots, videos
and marketing assets — using KBM's own logo and visual language, without changing any API, setting,
database object or deployment name, so every existing installation upgrades unchanged.

## Workstream: KBM Corporate Rebranding & Visual Identity

| Track | Scope | Status |
|---|---|---|
| Brand analysis | KBM logo (supplied SVGs), KBM Word and PowerPoint material: colours, fonts, graphic language, open questions ([analysis](../branding/kbm-brand-analysis.md)) | DONE |
| Brand guidelines | name, logo, lockup, palettes, typography, tokens, components, engine marks, themes, documentation, screenshots, video, marketing, accessibility, do / don't ([guidelines](../branding/kbm-dbmanager-brand-guidelines.md)) | DONE |
| Product naming | **KBM DBManager** in the UI, browser title, manifest, notifications, e-mails, PDF reports, MFA issuer default, API title, docs, README, chart README; technical identifiers unchanged | DONE |
| Logo adoption | KBM logo files as supplied (blue, white; letters-only variant); lockup `[KBM] │ DBManager`; favicon and app icons; the former DBManager artwork retired | DONE |
| Design tokens | KBM blue `#1A4796`, navy `#091450`, KBM text and tint colours, chart palette from the KBM theme, Plus Jakarta Sans; status colours unchanged; light and dark themes | DONE |
| UI restyling | navigation rail, login cover, About banner, cards, page titles, tooltips, dialogs, loading and first-run screens; every page inherits the tokens | DONE |
| PDF reports | KBM document style: logo header, deep-blue table header row, footer mark and blue page number | DONE |
| Documentation branding | *KBM DBManager Documentation*: KBM theme for MkDocs Material, logo, favicon, tables, callouts, banner; prose renamed; diagrams relabelled | DONE |
| Screenshot refresh | 43 documentation screenshots recaptured from the running application | DONE |
| Video rebranding | product overview, LinkedIn version and teaser rebuilt with KBM branding, new narration, subtitles and thumbnails from a reproducible build script ([storyboards](https://github.com/hbenabdalla/dbmanager/blob/main/marketing/video/storyboards.md)) | DONE |
| Marketing assets | README banner, video descriptions for YouTube and LinkedIn, thumbnails | DONE |
| Testing | frontend, backend, documentation, brand-consistency checks, visual review in light and dark themes ([test validation](test-validation.md#release-0160-kbm-rebrand)) | DONE |
| Release preparation | version 0.16.0, changelog, release notes with upgrade and compatibility notes | DONE |

## Decisions

* **Company name** as on the logo: *Khorafi Business Machines (W.L.L.)*, short *KBM*. The brief's
  *Kuwait Business Machines* is flagged in the brand analysis for KBM to confirm.
* **No new product logo.** The product mark is a lockup of the unmodified KBM letters and the word
  *DBManager*, so the KBM logo is never altered.
* **Technical identifiers stay**: repository, images (`dbmanager-backend`, `dbmanager-frontend`),
  Helm chart `dbmanager`, `DBM_*` variables, database, tables, API paths, Docker volumes, and the
  machine identifier `"product": "DBManager"` of `GET /api/v1/version` (load-balancer checks and
  scripts match it). No migration.
* **Visible behaviour changes** that operators may notice: notification and report e-mail subjects
  start with `[KBM DBManager]`; Slack/Teams buttons say *Open in KBM DBManager*; the default MFA
  issuer for **new** enrolments is *KBM DBManager* (existing authenticator entries keep working).
* **Plus Jakarta Sans** (open licence, used in KBM's slides) replaces IBM Plex Sans in the
  interface; Calibri and Arial from KBM's Office documents cannot be shipped.
* **Engine marks unchanged**: they identify technologies, KBM identifies the platform.
* **Version 0.16.0** (minor): a customer-facing change of identity with new visible behaviour and
  no breaking change.

## Acceptance criteria

| Criterion | Evidence |
|---|---|
| Customer-facing UI and documentation say *KBM DBManager*; titles and manifest are branded | `backend/tests/unit/test_brand_naming.py` (UI strings, documentation prose, titles) |
| The retired artwork is referenced nowhere; the KBM files are the supplied single-colour artwork, identical in app and docs | `test_brand_naming.py::test_retired_artwork_is_not_referenced`, `test_kbm_logo_files_are_the_supplied_artwork` |
| The lockup and logos render the supplied files with their aspect ratio and accessible names | `frontend/src/test/engine-icon.test.tsx` (*KBM DBManager brand*) |
| Notifications, MFA and Teams/Slack payloads use the new name | `tests/unit/test_notify_delivery.py`, `test_mfa.py`, `test_alerting_completion.py` |
| `/version` keeps its machine identifier | `tests/unit/test_about_links.py`, `tests/api/test_alerting.py` |
| Every page renders without errors in the KBM theme | screenshot run of 43 pages ([visual validation](test-validation.md#visual-validation)) |
| Documentation builds strictly with the KBM theme | `mkdocs build --strict` |
| Videos contain no unsupported claims | narration checked against the [capability matrix](capability-matrix.md) |

## Next

Milestone **1.0 — Production Release** is NEXT and has not started. See the [roadmap](roadmap.md).
