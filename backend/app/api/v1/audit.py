"""Audit log browsing and export."""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, func, or_, select

from app.api.deps import DB, CurrentPrincipal, rate_limit
from app.core.csvsafe import csv_safe
from app.models import AuditEvent
from app.schemas.common import Page, PageParams, paginate
from app.services import audit as audit_svc

router = APIRouter(prefix="/audit", tags=["audit"])

EXPORT_COLUMNS = [
    "ts",
    "username",
    "auth_source",
    "source_ip",
    "action",
    "category",
    "risk_class",
    "result",
    "server_name",
    "database",
    "object_name",
    "target_type",
    "target_id",
    "failure_reason",
    "change_reason",
    "request_id",
]


def event_out(e: AuditEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "ts": e.ts,
        "user_id": str(e.user_id) if e.user_id else None,
        "username": e.username,
        "auth_source": e.auth_source,
        "source_ip": e.source_ip,
        "action": e.action,
        "category": e.category,
        "risk_class": e.risk_class,
        "target_type": e.target_type,
        "target_id": e.target_id,
        "server_id": str(e.server_id) if e.server_id else None,
        "server_name": e.server_name,
        "database": e.database,
        "object_name": e.object_name,
        "result": e.result,
        "failure_reason": e.failure_reason,
        "change_reason": e.change_reason,
        "request_id": e.request_id,
        "details": e.details,
    }


def _filtered(
    username: str | None,
    action: str | None,
    result: list[str] | None,
    category: list[str] | None,
    server: str | None,
    start: datetime | None,
    end: datetime | None,
    q: str | None,
) -> Select[Any]:
    stmt = select(AuditEvent)
    if username:
        stmt = stmt.where(func.lower(AuditEvent.username) == username.lower())
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if result:
        stmt = stmt.where(AuditEvent.result.in_(result))
    if category:
        stmt = stmt.where(AuditEvent.category.in_(category))
    if server:
        stmt = stmt.where(AuditEvent.server_name == server)
    if start:
        stmt = stmt.where(AuditEvent.ts >= start)
    if end:
        stmt = stmt.where(AuditEvent.ts <= end)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(AuditEvent.action).like(like),
                func.lower(AuditEvent.object_name).like(like),
                func.lower(AuditEvent.server_name).like(like),
                func.lower(AuditEvent.username).like(like),
                AuditEvent.request_id == q,
            )
        )
    return stmt.order_by(AuditEvent.ts.desc(), AuditEvent.id.desc())


@router.get("")
async def list_events(
    db: DB,
    p: CurrentPrincipal,
    params: PageParams = Depends(),
    username: str | None = None,
    action: str | None = None,
    result: list[str] | None = Query(None),
    category: list[str] | None = Query(None),
    server: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Page[dict[str, Any]]:
    p.authz.require("audit.view")
    stmt = _filtered(username, action, result, category, server, start, end, params.q)
    items, total = await paginate(db, stmt, params.page, params.page_size)
    return Page(items=[event_out(e) for e in items], total=total, page=params.page, page_size=params.page_size)


#: rows in one CSV export; ``X-DBM-Export-Truncated: true`` says more events matched
EXPORT_MAX_ROWS = 200_000


@router.get("/export.csv", dependencies=[rate_limit("expensive")])
async def export(
    db: DB,
    p: CurrentPrincipal,
    username: str | None = None,
    action: str | None = None,
    result: list[str] | None = Query(None),
    category: list[str] | None = Query(None),
    server: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    q: str | None = None,
) -> StreamingResponse:
    p.authz.require("audit.export")
    stmt = (
        _filtered(username, action, result, category, server, start, end, q)
        .with_only_columns(*(getattr(AuditEvent, c) for c in EXPORT_COLUMNS))
        .limit(EXPORT_MAX_ROWS + 1)
    )
    rows = (await db.execute(stmt)).all()  # plain tuples: far lighter than ORM objects
    truncated = len(rows) > EXPORT_MAX_ROWS
    rows = rows[:EXPORT_MAX_ROWS]
    await audit_svc.record(
        "audit.export",
        actor=p,
        category="audit",
        details={
            "rows": len(rows),
            "truncated": truncated,
            "filters": {"username": username, "action": action, "server": server, "q": q},
        },
    )

    async def gen() -> AsyncIterator[str]:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(EXPORT_COLUMNS)
        for i, e in enumerate(rows):
            # usernames of failed sign-ins and other values are user input: neutralise formulas
            w.writerow([csv_safe(v) for v in e])
            if i % 1000 == 0:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    return StreamingResponse(
        gen(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="audit-log.csv"',
            # more events match than one file holds: narrow the filters (e.g. by date) and export again
            "X-DBM-Export-Truncated": "true" if truncated else "false",
        },
    )
