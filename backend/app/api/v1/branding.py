"""Operator-supplied database engine logos.

DBManager ships original engine identity marks only: vendor logos are trademarks whose
redistribution needs the owner's permission (see docs/development/brand-assets.md). An
organisation that holds such a licence can place the files in ``DBM_BRANDING_DIR``; the web
UI then shows them next to the engine name. Only fixed file names are served
(``<engine>.svg`` or ``<engine>.png`` for the known engine keys); nothing else in the directory
is reachable, and SVG files are sent with a restrictive Content-Security-Policy so that a
script inside an SVG never runs.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Response

from app.api.deps import CurrentPrincipal
from app.core.config import get_settings
from app.core.errors import NotFound

router = APIRouter(tags=["branding"])

ENGINE_KEYS = ("postgresql", "mysql", "mariadb", "mssql", "oracle", "db2", "db2zos")
MEDIA = {"svg": "image/svg+xml", "png": "image/png"}
MAX_BYTES = 512 * 1024


def _dir() -> Path | None:
    d = get_settings().branding_dir.strip()
    if not d:
        return None
    p = Path(d)
    return p if p.is_dir() else None


def _file(engine: str) -> tuple[Path, str] | None:
    base = _dir()
    if base is None or engine not in ENGINE_KEYS:
        return None
    for ext in ("svg", "png"):
        f = base / f"{engine}.{ext}"
        if f.is_file() and not f.is_symlink() and 0 < f.stat().st_size <= MAX_BYTES:
            return f, ext
    return None


@router.get("/branding/engine-logos", summary="Engines with an operator-supplied logo")
async def engine_logos(p: CurrentPrincipal) -> dict[str, list[str]]:
    return {"engines": [e for e in ENGINE_KEYS if _file(e)]}


@router.get("/branding/engine-logos/{engine}", summary="An operator-supplied engine logo")
async def engine_logo(engine: str, p: CurrentPrincipal) -> Response:
    found = _file(engine)
    if found is None:
        raise NotFound("No logo for this engine")
    path, ext = found
    return Response(
        path.read_bytes(),
        media_type=MEDIA[ext],
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )
