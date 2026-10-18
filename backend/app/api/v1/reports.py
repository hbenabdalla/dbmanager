from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.deps import DB, CurrentPrincipal
from app.core.errors import NotFound, ValidationFailed
from app.services import audit
from app.services.reports import PLANNED_FORMATS, RENDERERS, REPORTS

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("")
async def list_reports(p: CurrentPrincipal) -> list[dict[str, Any]]:
    return [
        {
            "key": r.key,
            "title": r.title,
            "description": r.description,
            "formats": list(RENDERERS),
            "planned_formats": PLANNED_FORMATS,
            "available": p.authz.has_anywhere("reports.view") and p.authz.has_anywhere(r.permission),
        }
        for r in REPORTS.values()
    ]


@router.get("/{key}.{fmt}")
async def download(key: str, fmt: str, db: DB, p: CurrentPrincipal) -> Response:
    p.authz.require_anywhere("reports.view")
    r = REPORTS.get(key)
    if r is None:
        raise NotFound("Unknown report")
    if fmt not in RENDERERS:
        raise ValidationFailed(f"Format {fmt!r} is not available yet", details={"available": list(RENDERERS)})
    if r.permission in ("audit.view",):
        p.authz.require(r.permission)
    else:
        p.authz.require_anywhere(r.permission)
    cols, rows = await r.producer(db, p.authz)
    renderer = RENDERERS[fmt]
    data = renderer.render(r.title, cols, rows)
    await audit.record("report.download", actor=p, category="report", object_name=key, details={"rows": len(rows)})
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return Response(
        data,
        media_type=renderer.media_type,
        headers={"Content-Disposition": f'attachment; filename="{key}-{stamp}.{renderer.extension}"'},
    )
