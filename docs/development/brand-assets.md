# Brand assets

This page describes the DBManager logo, the database engine marks shown in the product, where the
files live, and the rules for changing them.

## The DBManager logo

The logo (a database cylinder in a ring with three nodes, a cloud and a monitoring chart, above the
word **DBManager**) is the project's own artwork. The authoritative file is
`docs/assets/brand/dbmanager-logo-source.png` (1774 × 887 px, transparent background). Every other
file below is derived from it **only by cropping and scaling**: none of them is redrawn, recoloured or
reshaped.

| File | Size | Use |
|---|---|---|
| `docs/assets/brand/dbmanager-logo-source.png` | 1774 × 887 | source of truth; not shipped in the web bundle |
| `frontend/src/assets/brand/dbmanager/dbmanager-logo.png` | 960 × 596 | full logo, large (About page) |
| `frontend/src/assets/brand/dbmanager/dbmanager-logo-small.png` | 480 × 298 | full logo, small (sign-in, setup wizard, loading screen) |
| `frontend/src/assets/brand/dbmanager/dbmanager-mark.png` | 337 × 256 | symbol only, without the lettering (sidebar, first-run screen) |
| `frontend/public/favicon.ico`, `favicon-16.png`, `favicon-32.png` | 16–48 | browser tab |
| `frontend/public/apple-touch-icon.png`, `icon-192.png`, `icon-512.png`, `site.webmanifest` | 180–512 | home-screen / app icon, web app manifest |
| `backend/app/data/brand/dbmanager-logo.png` | 480 × 298 | header of server-side PDF reports |
| `docs/assets/brand/dbmanager-logo.png`, `dbmanager-mark.png`, `favicon-32.png` | — | README and documentation site |
| `docs/assets/brand/dbmanager-mark-plate.png` | 256 × 256 | documentation header logo (the mark on a white rounded plate) |

### Light and dark backgrounds

The navy lettering and ring of the logo need a light background. On dark surfaces — the navigation
sidebar (dark in both themes), the dark theme, the documentation header — the logo is shown **on a
light plate** (a white rounded rectangle behind it) instead of being recoloured. The components that
do this are `BrandLogo` and `BrandMark` in `frontend/src/components/brand.tsx`:

- `BrandLogo plate="auto"` (default) adds the plate in the dark theme only;
- `BrandMark plate` always sits on the plate (used in the sidebar, expanded and collapsed);
- both use the product name **DBManager** as the text alternative, or are decorative
  (`alt=""`) when the name is printed next to them.

Motion is limited to a short fade on the loading screen and a hover opacity change on the sidebar
logo; both are disabled under `prefers-reduced-motion`. The logo is never animated continuously.

### Regenerating the derived files

The derived files were produced from the source with Pillow: transparent pixels with alpha ≤ 12
cleared (anti-aliasing noise), cropped to the content (full logo: box 305,90 – 1484,813; symbol:
575,90 – 1238,594), scaled with Lanczos resampling and quantised to 256 colours. To change the logo,
replace the source file and regenerate every file in the table; do not edit derived files by hand.

## Database engine marks

The product shows which engine a server runs everywhere it lists servers: registration and
onboarding, the fleet table and its engine filter, the server header, the dashboard's engine
distribution (click to filter), topology nodes, global search, alerts, findings and
recommendations, backup and restore, Engine support, Database drivers, notification-channel routing,
reports. One component does this: `DatabaseEngineIcon` (`frontend/src/components/engine-icon.tsx`).

**DBManager does not ship vendor logos.** The marks are DBManager's own design: a rounded tile in a
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
  Tuning Packs; DBManager keeps those features off unless licensed and configured.

### Operator-supplied licensed logos

An organisation that is licensed to use a vendor's logo (for example under a partner agreement) can
show it instead of the DBManager mark:

1. Place the files in a directory readable by the backend, named exactly `<engine>.svg` or
   `<engine>.png` for the keys above (for example `oracle.svg`, `db2zos.png`). Files larger than
   512 KiB, symbolic links and any other file names are ignored.
2. Set `DBM_BRANDING_DIR` to that directory and restart the backend.

The UI asks `GET /api/v1/branding/engine-logos` which engines have a logo and loads each from
`GET /api/v1/branding/engine-logos/{engine}` (signed-in users only). The images are shown on a
neutral plate, scaled to fit without distortion, and never recoloured; the engine name stays the
accessible name. SVG files are served with `Content-Security-Policy: default-src 'none'; sandbox`
and `X-Content-Type-Options: nosniff`, so scripts in an SVG never run. If an image fails to load, the
DBManager mark is shown.

Do not commit vendor logos to this repository, and do not add a licensed logo to the image you
distribute unless your licence allows redistribution.

## Trademark notice

PostgreSQL is a registered trademark of the PostgreSQL Community Association of Canada. MySQL and
Oracle are registered trademarks of Oracle and/or its affiliates. MariaDB is a registered trademark
of MariaDB plc and/or its affiliates. Microsoft and SQL Server are trademarks of the Microsoft group of companies. IBM and
Db2 are trademarks of International Business Machines Corporation. Other names may be trademarks of
their respective owners.

DBManager is an independent project. It is not affiliated with, sponsored by or endorsed by any of
these companies, and the engine marks used in DBManager are its own designs, not the vendors' logos.
The product names are used only to say which database engines DBManager works with.

Why DBManager does not bundle the vendors' logos: their trademark policies require permission for
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
