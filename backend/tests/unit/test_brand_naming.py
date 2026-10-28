"""Brand consistency (KBM corporate rebrand, 0.16): customer-facing text says **KBM DBManager**,
the retired DBManager artwork is not referenced, and the KBM logo files are the supplied ones.

Technical identifiers keep "DBManager"/"dbmanager" on purpose (repository, image and chart
names, ``DBM_*`` variables, the ``/version`` machine identifier, history). The checks therefore
look only at prose and UI strings, not at code spans, paths or identifiers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OLD_NAME = re.compile(r"(?<!KBM )(?<![\w/.`'\"-])DBManager(?![\w-])")
#: Markdown that documents history or quotes machine values verbatim
HISTORY = {"CHANGELOG.md", "docs/product/roadmap-audit-history.md"}
#: pages that define the naming rules and therefore quote the bare word
NAMING_RULES = {
    "docs/branding/kbm-dbmanager-brand-guidelines.md",
    "docs/branding/kbm-brand-analysis.md",
    "docs/development/brand-assets.md",
}
#: prose that names the technical identifier on purpose
ALLOWED_PROSE = (
    "`DBManager`",  # inline code
    '"product": "DBManager"',
    "former DBManager",  # history of the retired identity
    "*DBManager*",  # the word of the lockup, quoted
)


def _prose_lines(text: str):  # type: ignore[no-untyped-def]
    fence = False
    for n, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            continue
        if fence:
            continue
        # drop inline code, link targets and HTML tags before looking at the words
        yield n, re.sub(r"`[^`]*`|\]\([^)]*\)|<[^>]+>|https?://\S+", " ", line)


def test_documentation_prose_uses_the_product_name() -> None:
    offenders = []
    files = [*ROOT.glob("docs/**/*.md"), ROOT / "README.md", ROOT / "ROADMAP.md"]
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if rel in HISTORY or rel in NAMING_RULES or rel.startswith("docs/release-notes/"):
            continue
        for n, line in _prose_lines(f.read_text(encoding="utf-8")):
            if OLD_NAME.search(line) and not any(a in line for a in ALLOWED_PROSE):
                offenders.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not offenders, "customer-facing text should say 'KBM DBManager':\n" + "\n".join(offenders)


def test_ui_strings_use_the_product_name() -> None:
    offenders = []
    for f in sorted((ROOT / "frontend" / "src").rglob("*.ts*")):
        rel = f.relative_to(ROOT).as_posix()
        if "/test/" in rel:
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            s = line.strip()
            if s.startswith(("//", "*", "/*")) or "PRODUCT_SHORT" in s:
                continue
            for m in re.finditer(r"\bDBManager\b", s):
                before = s[: m.start()]
                if not before.endswith("KBM ") and ">{PRODUCT_SHORT}<" not in s:
                    offenders.append(f"{rel}:{n}: {s[:110]}")
    assert not offenders, "UI text should say 'KBM DBManager':\n" + "\n".join(offenders)


def test_application_and_documentation_titles() -> None:
    html = (ROOT / "frontend" / "index.html").read_text()
    assert "<title>KBM DBManager</title>" in html
    assert 'content="#1a4796"' in html  # theme colour = KBM blue
    manifest = json.loads((ROOT / "frontend" / "public" / "site.webmanifest").read_text())
    assert manifest["name"] == "KBM DBManager"
    mkdocs = (ROOT / "mkdocs.yml").read_text()
    assert "site_name: KBM DBManager Documentation" in mkdocs
    assert "assets/brand/kbm/" in mkdocs


def test_retired_artwork_is_not_referenced() -> None:
    retired = re.compile(r"dbmanager-(logo|mark)|brand/dbmanager/|brand/favicon-32\.png")
    offenders = []
    for pattern in (
        "frontend/src/**/*.ts*",
        "frontend/index.html",
        "backend/app/**/*.py",
        "docs/**/*.md",
        "*.md",
        "mkdocs.yml",
    ):
        for f in ROOT.glob(pattern):
            rel = f.relative_to(ROOT).as_posix()
            if rel.startswith("docs/release-notes/") or rel == "CHANGELOG.md":
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
                if retired.search(line):
                    offenders.append(f"{rel}:{n}")
    assert not offenders, offenders
    assert not (ROOT / "frontend" / "src" / "assets" / "brand" / "dbmanager").exists()


def test_kbm_logo_files_are_the_supplied_artwork() -> None:
    """The four logo files are single-colour KBM artwork (blue #1A4796 or white): nobody
    recoloured or replaced them; the web app ships the same files as the documentation."""
    docs = ROOT / "docs" / "assets" / "brand" / "kbm"
    app = ROOT / "frontend" / "src" / "assets" / "brand" / "kbm"
    for name, colour in [
        ("kbm-logo.svg", "#1a4796"),
        ("kbm-mark.svg", "#1a4796"),
        ("kbm-logo-white.svg", "#fff"),
        ("kbm-mark-white.svg", "#fff"),
    ]:
        svg = (docs / name).read_text()
        fills = set(re.findall(r"fill:(#[0-9a-fA-F]{3,6})", svg))
        assert fills == {colour}, (name, fills)
        assert (app / name).read_bytes() == (docs / name).read_bytes(), name
    # the full logo carries the company name lines, the mark does not
    assert len(re.findall(r"<path", (docs / "kbm-logo.svg").read_text())) > 40
    assert len(re.findall(r"<path", (docs / "kbm-mark.svg").read_text())) == 4
