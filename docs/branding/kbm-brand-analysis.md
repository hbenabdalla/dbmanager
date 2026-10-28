# KBM brand analysis

This analysis is the basis of the KBM DBManager rebrand (release 0.16). It records what was taken
from KBM's own material, what was inferred from it, and what is still open. The rules that follow
from it are in the [brand guidelines](kbm-dbmanager-brand-guidelines.md).

**Verified** means read directly from KBM's files: logo artwork, theme definitions, colour values
used in KBM documents. **Inferred** means a design choice derived from how those documents look,
not from a written rule. KBM supplied no written brand manual, so every layout rule here is
inferred.

## Reference files analysed

| File | Type | What it provided |
|---|---|---|
| `KBMLogo.svg` | vector logo, supplied by KBM | the official logo in KBM blue: the KBM letters, *KHORAFI BUSINESS MACHINES (W.L.L.)* and the Arabic company name |
| `KBMLogo-white.svg` | vector logo, supplied by KBM | the same logo in white, for blue backgrounds |
| A KBM technical proposal (Word, 29 pages) | customer document in KBM's template | cover page, running headers and footers, headings, tables, callouts, colours, the logo in blue and white raster versions, a company-profile graphic |
| A KBM scope-of-work deck (PowerPoint, 5 slides) | presentation in KBM's template | slide masters, theme colours and fonts, cover slide, cards with icon badges, blue panels, motifs |

Only brand elements were taken from the two documents. Their content belongs to KBM's customers
and is not reproduced anywhere in this repository.

## Company name

The logo, the document footers and the copyright lines all read **Khorafi Business Machines
(W.L.L.)**, short form **KBM**, website `www.kbm-kw.com`. The rebrand brief said *Kuwait Business
Machines*. KBM DBManager uses the name the logo carries: **Khorafi Business Machines (KBM)**.
Flagged for confirmation by KBM.

## Logo

| Property | Finding | Status |
|---|---|---|
| Artwork | wide wordmark: the letters **KBM** cut by a horizontal double band, with *KHORAFI BUSINESS MACHINES (W.L.L.)* and the Arabic name below it | verified (SVG) |
| Colours | single colour: KBM blue `#1A4796`, or white | verified (SVG fills; the same value in the raster logos of both documents) |
| Proportions | full logo 491 × 250 (about 2 : 1); letters only 491 × 163 (about 3 : 1) | verified (artwork bounds) |
| Variants in use | full logo blue on white (document headers); full logo white on KBM blue (cover pages, title slide); letters-only blue (document footers, slide corners) | verified (documents) |
| Clear space | the documents keep at least the height of the double band (about 6 % of the logo height) on every side, and much more in practice | inferred |
| Minimum size | the documents use the letters-only variant at about 9 mm wide in footers; below that the full logo is not used | inferred |
| Background | blue logo on white or very light tints; white logo on KBM blue or navy. Never on photographs or mid-tones in the samples | inferred |

## Colours

| Role | Value | Where found | Status |
|---|---|---|---|
| Primary: KBM blue | `#1A4796` | logo artwork; PowerPoint theme *Accent 1*; cover backgrounds; headings | verified |
| Navy | `#091450` | slide text, icon badges | verified |
| Deep blue | `#1F3864` | Word table headers, title text | verified |
| Body text | `#2D3548` | Word body text; PowerPoint theme *Dark 1* | verified |
| Secondary text | `#5A6478`, `#5B6B87` | PowerPoint theme *Accent 2*; Word captions | verified |
| Muted | `#8A96A8` | PowerPoint theme *Accent 4* | verified (too light for text on white: decoration only) |
| Tints | `#CCD8ED`, `#D9E1F2`, `#D0D6E6`, `#EDF3FC`, `#F7F9FC`, `#E0E3E8` | theme *Accent 5/6*, *Light 2*; table fills, callout fills, borders | verified |
| Accent blues | `#0689D8`, `#0F9ED5`, `#1428A0` | the second PowerPoint theme in the deck | verified, used only for charts |
| Further accents | `#A02B93`, `#4EA72E`, `#75787B` | the same theme | verified, used only for charts |
| Positive (green) | `#2E7D4F` | Word "compliant" marks | verified; the UI keeps its own accessible status colours |

No written status-colour rules exist. Health and severity colours (healthy, warning, critical,
offline, unknown, information) stay the product's own accessible set, kept apart from KBM blue.

## Typography

| Use | Font | Status |
|---|---|---|
| Slide headings and labels | **Plus Jakarta Sans** (435 runs in the deck) | verified |
| Slide body text | Arial | verified |
| Word documents | Calibri (headings and body) | verified |
| Headings style | bold, title case, KBM blue or deep blue; small labels in upper case with wide letter spacing (*PREPARED BY*, *PRESENTER*, *RFQ NO.*) | verified (documents) |

Plus Jakarta Sans is open source (SIL Open Font License) and can ship with the product, while
Calibri and Arial cannot. KBM DBManager therefore uses **Plus Jakarta Sans** for all interface
text. Code and SQL stay in IBM Plex Mono.

## Graphic language

| Element | Finding | Status |
|---|---|---|
| Covers | full-bleed KBM blue, white logo top left, large white title bottom left, thin white rule, upper-case label row | verified |
| Motifs on blue | faint horizontal bands, a small dot grid, ringed circles, thin vertical lines | verified (cover slide and Word cover) |
| Cards | white, small radius, soft shadow, title in bold, an icon in a circular badge (white ring around a blue disc), a diagonal arrow ↗ in the corner | verified (slides) |
| Panels | half-page KBM-blue blocks next to white content | verified (slides) |
| Numbered lists | white numerals in blue circles | verified (slides) |
| Tables | deep-blue header row with white text, light-blue first column or zebra fill, thin grey-blue borders | verified (Word) |
| Callouts | light tint background with a KBM-blue left rule | verified (Word confidentiality notice) |
| Headers and footers | logo top left, document title and customer top right; footer with the letters-only logo, classification, page number in KBM blue | verified (Word) |
| Gradients, photographs | not used in the samples | verified (absence) |

## Layout

Generous white space, left-aligned text, content in cards or tables, wide margins, one accent
colour, and clear separation of blue framing elements from white content areas. All inferred from
the samples.

## Design decisions for the product

1. **Navigation rail in KBM blue** (navy in the dark theme), white logo artwork: the blue panels of
   the slides applied to the application frame.
2. **KBM blue is the only action colour** (buttons, links, focus, selection). Status colours are
   unchanged and never use the brand blue.
3. **Login page as a KBM cover**: KBM-blue half with the white full logo, the motifs and the product
   statement; the form on a light background.
4. **Product lockup** `[KBM mark] | DBManager` instead of a new product logo, so that the KBM logo is
   never modified and the product name stays readable at small sizes.
5. **Cards** keep their thin border and gain KBM's soft navy shadow; page titles are set in KBM blue.
6. **Dark theme** built on KBM navy `#091450` rather than an inversion of the light theme.
7. **Charts** use KBM theme accents, distinct from the status colours.
8. **PDF reports** follow KBM's document style: logo and title header, deep-blue table header row,
   letters-only logo and blue page number in the footer.
9. **Documentation** (Read the Docs) uses KBM blue, navy footer, Plus Jakarta Sans headings and
   KBM-style tables and callouts.

## Missing or ambiguous information

- No written brand manual: clear space, minimum size, the use of the letters-only variant and colour
  ratios are inferred from documents, not prescribed. Recommended: ask KBM marketing to confirm.
- Company name in the brief (*Kuwait Business Machines*) differs from the logo (*Khorafi Business
  Machines*).
- Official slogans: none found. *Unified Database Operations* and *Monitor. Manage. Secure.
  Optimize.* are proposed marketing lines, not KBM slogans.
- A white letters-only logo was not supplied; it is the supplied white logo without the name lines,
  the same treatment KBM applies to the blue logo in its footers.
- The repository and its documentation are public; publishing the KBM logo there assumes KBM's
  approval.
