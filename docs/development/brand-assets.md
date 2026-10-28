# Brand assets

This page describes the KBM logo and the KBM DBManager product lockup, the database engine marks
shown in the product, where the files live, and the rules for changing them. The brand rules
themselves are in the [KBM DBManager brand guidelines](../branding/kbm-dbmanager-brand-guidelines.md).

## The KBM logo

KBM DBManager is a product of Khorafi Business Machines (KBM). The KBM logo is used **exactly as
KBM supplied it** — vector files, KBM blue `#1A4796` and white — and is only ever scaled with its
aspect ratio kept. It is never redrawn, recoloured, stretched, outlined or given effects.

| File | Content | Used for |
|---|---|---|
| `docs/assets/brand/kbm/kbm-logo.svg` | full logo, KBM blue (letters, *Khorafi Business Machines (W.L.L.)*, Arabic name) | light backgrounds: login form side, documents |
| `docs/assets/brand/kbm/kbm-logo-white.svg` | full logo, white | KBM-blue / navy backgrounds: login cover, About banner, video, banners |
| `docs/assets/brand/kbm/kbm-mark.svg` | the KBM letters only, blue | product lockup on light surfaces, PDF footer |
| `docs/assets/brand/kbm/kbm-mark-white.svg` | the KBM letters only, white | sidebar, documentation header, favicon |
| `frontend/src/assets/brand/kbm/*.svg` | the same four files | the web application |
| `backend/app/data/brand/kbm-logo.png`, `kbm-mark.png` | rendered from the SVGs (1200 / 600 px wide) | PDF report header and footer |
| `frontend/public/favicon.svg`, `favicon.ico`, `favicon-16/32.png`, `icon-192/512.png`, `apple-touch-icon.png` | the white KBM mark centred on a KBM-blue tile | browser tab, app icons |
| `docs/assets/brand/kbm/kbm-dbmanager-lockup.png`, `-white.png` | the product lockup (below) | README, slides, video |
| `docs/assets/brand/kbm/kbm-dbmanager-banner.png` | KBM-blue cover banner | README, documentation home |

The *mark* files are the supplied artwork with the two company-name lines left out (the same
variant KBM uses in its document footers); the remaining paths and their coordinates are unchanged,
only the view box is tightened to them.

### Product lockup

`[KBM mark] | DBManager` — the KBM letters, a thin vertical divider and the word **DBManager** in
Plus Jakarta Sans Bold, reading as the official product name **KBM DBManager**. It is implemented
once, as `BrandLogo` in `frontend/src/components/brand.tsx` (and rendered for documents by the same
proportions): mark width 31 % of the lockup, word size 11 % of it. `KbmLogo` renders the KBM logo
alone (`full` for the version with the company name), `BrandMark` the mark alone, `KbmCoverMotif`
the decorative cover pattern. The text alternative is **KBM DBManager**, or the image is decorative
when the name is printed next to it.

Motion is limited to a short fade on the loading screen and a hover opacity change on the sidebar
lockup; both are disabled under `prefers-reduced-motion`. The logo is never animated continuously.

### Retired artwork

The DBManager logo of 0.14 – 0.15 (database cylinder, ring and cloud) is retired: it is no longer
shipped in the application, the documentation, PDF reports or icons. Release notes of those
versions still mention it.

## Database engine marks

The product shows which engine a server runs everywhere it lists servers: registration and
onboarding, the fleet table and its engine filter, the server header, the dashboard's engine
distribution (click to filter), topology nodes, global search, alerts, findings and
recommendations, backup and restore, Engine support, Database drivers, notification-channel routing,
reports. One component does this: `DatabaseEngineIcon` (`frontend/src/components/engine-icon.tsx`).

**KBM DBManager does not ship vendor logos.** The marks are KBM DBManager's own design: a rounded tile in a
per-engine colour with a two-letter monogram (`Pg`, `My`, `Ma`, `SS`, `Or`, `D2`, `Dz`). Db2 for z/OS
has a band at the bottom of its tile so it is never confused with Db2 LUW, and unknown engines get a
neutral database glyph. The marks are not modelled on, and must not imitate, any vendor logo
(no elephant, dolphin, sea lion, flag, globe or other vendor artwork).

| Key | Label | Monogram | Tile colour |
|---|---|---|---|
| `postgresql` | PostgreSQL | Pg | `#35649a` |
| `mysql` | MySQL | My | `#9a5a10` |
| `mariadb` | MariaDB | Ma | `#7a5230` |
| `mssql` | SQL Server | SS | `#a4373a` |
| `oracle` | Oracle | Or | `#b23a20` |
| `db2` | Db2 LUW | D2 | `#1f7a72` |
| `db2zos` | Db2 for z/OS | Dz | `#185a54` |
| anything else | the engine key | glyph | `#5b6678` |

Rules the component enforces:

- **Text is always available.** The engine name is either printed next to the mark (`withLabel`)
  or is the mark's accessible name (`role="img"`, `aria-label`, tooltip). Use `decorative` only when
  the name is already printed next to it. Colour never carries the meaning alone.
- **Contrast.** White monograms are at least 4.5:1 on every tile colour; the tile has a thin outline
  so it stays distinct on dark surfaces.
- **Versions come from discovery.** `version` is the version the adapter detected on that server;
  marks never print a hard-coded version.
- **Spellings.** `sqlserver`, `db2-zos`, `postgres` and similar spellings map to the canonical keys
  (`normalizeEngine` in `frontend/src/lib/engines.ts`).
- **No external resources.** Marks are inline SVG; no logo CDN or remote image is used.
- **Not action icons.** Engine marks identify engines; they are never used for buttons or actions.
- **No licensing implication.** Showing the Oracle mark says nothing about Oracle Diagnostics or
  Tuning Packs; KBM DBManager keeps those features off unless licensed and configured.

### Operator-supplied licensed logos

An organisation that is licensed to use a vendor's logo (for example under a partner agreement) can
show it instead of the KBM DBManager mark:

1. Place the files in a directory readable by the backend, named exactly `<engine>.svg` or
   `<engine>.png` for the keys above (for example `oracle.svg`, `db2zos.png`). Files larger than
   512 KiB, symbolic links and any other file names are ignored.
2. Set `DBM_BRANDING_DIR` to that directory and restart the backend.

The UI asks `GET /api/v1/branding/engine-logos` which engines have a logo and loads each from
`GET /api/v1/branding/engine-logos/{engine}` (signed-in users only). The images are shown on a
neutral plate, scaled to fit without distortion, and never recoloured; the engine name stays the
accessible name. SVG files are served with `Content-Security-Policy: default-src 'none'; sandbox`
and `X-Content-Type-Options: nosniff`, so scripts in an SVG never run. If an image fails to load, the
KBM DBManager mark is shown.

Do not commit vendor logos to this repository, and do not add a licensed logo to the image you
distribute unless your licence allows redistribution.

## Trademark notice

PostgreSQL is a registered trademark of the PostgreSQL Community Association of Canada. MySQL and
Oracle are registered trademarks of Oracle and/or its affiliates. MariaDB is a registered trademark
of MariaDB plc and/or its affiliates. Microsoft and SQL Server are trademarks of the Microsoft group of companies. IBM and
Db2 are trademarks of International Business Machines Corporation. Other names may be trademarks of
their respective owners.

KBM DBManager is not affiliated with, sponsored by or endorsed by any of these companies, and the engine marks used in KBM DBManager are its own designs, not the vendors' logos.
The product names are used only to say which database engines KBM DBManager works with.

Why KBM DBManager does not bundle the vendors' logos: their trademark policies require permission for
using the logo artwork in a product (PostgreSQL's policy reserves the logo for approved uses;
Microsoft, IBM and Oracle/MySQL do not license their logos for third-party products without an
agreement; the MariaDB sea lion logo belongs to the MariaDB company). The policies are published at
[postgresql.org](https://www.postgresql.org/about/policies/trademarks/),
[oracle.com](https://www.oracle.com/legal/trademarks.html),
[mysql.com](https://www.mysql.com/about/legal/trademark.html),
[microsoft.com](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general),
[mariadb.org](https://mariadb.org/en/trademark/) and
[ibm.com](https://www.ibm.com/legal/copyright-trademark). This is not legal advice; check the current
policy and your agreements before using a vendor logo.
