# KBM DBManager brand guidelines

The reference for everything KBM DBManager shows: the application, documentation, reports,
screenshots, videos and marketing material. The evidence behind each rule is in the
[brand analysis](kbm-brand-analysis.md); the files are described in
[brand assets](../development/brand-assets.md).

## 1. Official product name

| Use | Write |
|---|---|
| Product name, everywhere customers read it | **KBM DBManager** |
| Company | **Khorafi Business Machines (KBM)**, short **KBM** |
| Documentation title | **KBM DBManager Documentation** |
| Product next to the KBM mark in the lockup | **DBManager** (the mark supplies "KBM") |

Keep **DBManager** alone only for technical identifiers and history: the repository
`hbenabdalla/dbmanager`, image names `dbmanager-backend` / `dbmanager-frontend`, the Helm chart
`dbmanager`, `DBM_*` environment variables, database and table names, API paths and the machine
identifier `"product": "DBManager"` of `GET /api/v1/version`, and release notes of earlier versions.
Never write *DB Manager*, *KBM-DBManager*, *KBMDBManager* or *DBManager by KBM*.

## 2. Corporate logo usage

- Use only the files KBM supplied (`docs/assets/brand/kbm/kbm-logo*.svg`, `kbm-mark*.svg`).
- Blue logo (`#1A4796`) on white or KBM tints; white logo on KBM blue or navy.
- Scale with the aspect ratio kept. Do not redraw, recolour, outline, add shadows, rotate, crop
  into the letters or place on photographs.
- Clear space: at least the height of the logo's double band on every side; in the application at
  least 8 px.
- Full logo (with the company name) from 140 px wide; below that use the letters-only mark.
- One KBM logo per screen: the sidebar carries it; pages do not repeat it.

## 3. Product logo composition

```
[KBM mark]  │  DBManager
```

KBM letters, a thin divider (KBM tint `#CCD8ED`, or white at 35 % on blue), then **DBManager** in Plus
Jakarta Sans Bold, in KBM blue (white on blue). Mark width 31 % of the lockup, word size 11 % of
it, spacing 4.5 %. Optional upper-case caption below the word (for example *DATABASE OPERATIONS*).
Accessible name: *KBM DBManager*. Implemented once in `BrandLogo`; never rebuild it by hand in a
page.

## 4. Primary palette

| Token | Value | Use |
|---|---|---|
| `--brand-primary` | `#1A4796` KBM blue | actions, links, focus, selection, navigation rail, headings, covers |
| `--brand-secondary` | `#091450` KBM navy | dark-theme rail, tooltips, dialog scrim, video backgrounds |
| `--brand-deep` | `#1F3864` | table header rows in documents and PDFs |
| text | `#2D3548` | body text (12.3 : 1 on white) |

## 5. Secondary palette

| Value | Use |
|---|---|
| `#5A6478`, `#5B6B87` | secondary and muted text (5.4 : 1 on white) |
| `#CCD8ED`, `#D9E1F2` | borders, dividers |
| `#EDF3FC`, `#F7F9FC` | callout and table fills, subtle surfaces |
| `#0689D8` | `--brand-accent`: second chart series, documentation accent |
| `#A02B93`, `#75787B`, `#4EA72E` | further chart series only |

Status colours are **not** part of the brand palette and keep their meaning everywhere:
healthy `#17895C`, warning `#B77A10`, critical `#C93636`, offline/unknown `#6B7886`, information
`#0571B4`, simulated `#6A4CC4`. Never show a status in KBM blue, and never rely on colour alone:
every status also has a label or icon.

## 6. Typography

| Role | Font | Notes |
|---|---|---|
| Interface, headings, documents produced by the product | Plus Jakarta Sans 400/500/600/700 | SIL OFL, bundled (`@fontsource/plus-jakarta-sans`) |
| Code, SQL, identifiers | IBM Plex Mono 400/500 | |
| Page title | 22 px bold, KBM blue (`text-heading`) | |
| Card title | 13.5 px semibold | |
| Body | 13.5 px regular, line height 1.5 | |
| Labels | 11 px semibold, upper case, letter spacing 0.12 – 0.22 em | KBM cover labels |
| PDF reports | Helvetica (embedded standard font) in KBM colours | |

## 7. UI design tokens

All colours, radii, shadows and motion timings are CSS custom properties in
`frontend/src/index.css` and mapped to Tailwind utilities (`bg-brand`, `text-heading`,
`border-brand-line`, `bg-accent`, `text-muted`, `bg-ok-soft` …). Components never hard-code colours.
Semantic aliases: `--brand-primary`, `--brand-secondary`, `--brand-accent`, `--background`,
`--foreground`, `--card`, `--border`, `--muted`, `--destructive`, `--success`, `--warning`,
`--info`. Radius: controls 6 px, panels 10 px. Shadows: `--shadow-card` (soft navy), `--shadow-pop`.
Motion: 150 / 200 / 300 ms, disabled under `prefers-reduced-motion`.

## 8. Component styling

- **Cards**: white surface, 1 px border `#D9E1F2`, 10 px radius, soft navy shadow; bold title.
- **Buttons**: primary filled KBM blue with white text; secondary outlined; destructive red only for
  irreversible actions.
- **Navigation rail**: KBM blue (navy in dark theme), white text, active item on a lighter blue.
- **Tables**: light header in the app (dense data); deep-blue header row in PDFs and documents.
- **Callouts**: tint background with a KBM-blue rule.
- **Covers and hero areas** (login, About): KBM blue, white full logo, `KbmCoverMotif`, white title.
  Use sparingly — at most one per screen.
- **Icon badges** (marketing, video): white circle ring around a KBM-blue disc with a white icon.

## 9. Database-engine logo usage

KBM branding identifies the **platform**; engine marks identify the **technology**; functional icons
stand for **actions**. Engine marks are KBM DBManager's own designs (`DatabaseEngineIcon`), keep
their own colours on every background, are never recoloured to KBM blue and are always accompanied
by the engine name. Vendor logos are not shipped; licensed organisations can supply them with
`DBM_BRANDING_DIR` ([brand assets](../development/brand-assets.md#operator-supplied-licensed-logos)).

## 10. Light theme

Background `#F4F6FA`, surfaces white and `#F7F9FC`, borders `#D9E1F2`, text `#2D3548`, headings and
actions KBM blue, rail KBM blue.

## 11. Dark theme

Built on KBM navy, not inverted: background `#070D24`, surfaces `#0D1533` – `#17224A`, rail
`#091450`, text `#E6EBF5`, actions a lighter KBM blue `#7199EA` (6.4 : 1 on the surface), status
colours brightened for contrast. The white KBM logo replaces the blue one automatically.

## 12. Documentation theme

MkDocs Material with `docs/assets/stylesheets/kbm.css`: KBM-blue header and tabs (navy in the dark
scheme), white KBM mark as header logo, KBM favicon, navy footer, Plus Jakarta Sans headings in
KBM blue, tables with deep-blue header rows, callouts with the KBM rule. Mermaid diagrams take the
theme colours; keep them technical (boxes, arrows, labels), never decorative.

## 13. Screenshot guidelines

- Capture the running application, never mock-ups: `docshots.py`-style automation against a
  simulated fleet plus real lab servers.
- 1440 × 900, light theme, sidebar expanded, unless a page shows a specific state (dark theme,
  dialogs).
- Simulated data must be visibly labelled as simulated; no real host names, customer names,
  credentials or tokens.
- File names in `docs/assets/screenshots/` are the page names (`dashboard-overview.png`); replace
  files rather than adding versions.

## 14. Video graphics

Opening: KBM blue with motif → white full KBM logo → **KBM DBManager** → *Unified Database
Operations* → engine marks → the dashboard. Feature titles: upper-case tracked label plus a bold
white title on KBM blue; application footage framed as a card with the soft navy shadow. Lower
thirds: navy bar with white text. Closing: white KBM logo, product name, documentation URL.
Transitions: fades and short slides (≤ 400 ms), no spins or zooms. Subtitles: Plus Jakarta Sans,
white on navy at 85 % opacity. Claims must match the [capability matrix](../product/capability-matrix.md).

## 15. Marketing graphics

The cover layout (KBM blue, white logo top left, title bottom left, motif right) for banners,
thumbnails and slides; product screenshots inside white cards with the navy shadow; the lockup on
white for document headers. Messaging: *KBM DBManager — Unified Database Operations*,
*Monitor. Manage. Secure. Optimize.* (proposed lines, not KBM slogans). Never advertise roadmap
items as available.

## 16. Accessibility requirements

- Text contrast at least 4.5 : 1 (large text 3 : 1); UI components and chart series at least 3 : 1.
- Status by colour **and** text or icon; engine marks with names.
- Focus ring visible (2 px KBM blue).
- All motion honours `prefers-reduced-motion`.
- Logos have text alternatives (*KBM DBManager*, *KBM*), or are decorative when the name is printed.

## 17. Correct and incorrect usage

| Correct | Incorrect |
|---|---|
| "Sign in to **KBM DBManager**" | "Sign in to DBManager" (customer-facing text) |
| White KBM logo on KBM blue | Blue KBM logo on navy; KBM logo on a photograph |
| The lockup `KBM │ DBManager` from `BrandLogo` | A redrawn or recoloured KBM logo; "KBM" typed in a font as a logo |
| A critical alert in red with the word *Critical* | A critical alert in KBM blue, or colour without a label |
| PostgreSQL mark in its own colour with "PostgreSQL" | Engine marks recoloured KBM blue, or vendor logos |
| `DBM_MASTER_KEY`, `dbmanager-backend:0.16.0` | Renaming environment variables or images to "KBM…" |
| "Planned for 1.0" for roadmap items in videos | Presenting roadmap items as available |
