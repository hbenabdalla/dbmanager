"""Performance intelligence: query regressions, period and plan comparison, capacity
forecasts (milestone 0.13). Read-only analysis of the monitoring history."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api import source as fleet_source
from app.api.deps import DB, CurrentPrincipal, load_server
from app.api.source import SourceQuery
from app.core.errors import NotFound, PermissionDenied, ValidationFailed
from app.domain.masking import mask_sql
from app.models import QueryFingerprint, QueryPlanSnapshot, Server
from app.services import advisor, audit, perf_intel
from app.services.authz import server_ref

router = APIRouter(tags=["performance"])

MAX_COMPARE_SPAN = timedelta(days=31)


async def _texts(db: DB, server_id: uuid.UUID, fps: set[str], show: bool) -> dict[tuple[str, str], str]:
    if not fps:
        return {}
    rows = (
        await db.execute(
            select(QueryFingerprint).where(
                QueryFingerprint.server_id == server_id, QueryFingerprint.fingerprint.in_(sorted(fps))
            )
        )
    ).scalars()
    return {(r.database, r.fingerprint): (r.normalized_query if show else mask_sql(r.normalized_query)) for r in rows}


# ------------------------------------------------------------------------------------------
# query regressions
# ------------------------------------------------------------------------------------------
@router.get("/servers/{server_id}/query-regressions", summary="Statements slower than their own baseline")
async def query_regressions(
    server_id: uuid.UUID,
    db: DB,
    p: CurrentPrincipal,
    recent_hours: int | None = Query(None, ge=1, le=48),
    baseline_days: int | None = Query(None, ge=1, le=90),
    include_plan_changes: bool = True,
) -> dict[str, Any]:
    s = await load_server(db, p, server_id, "query.view")
    settings = await advisor.perf_settings(db)
    if recent_hours is not None:
        settings["performance.regression_recent_hours"] = recent_hours
    if baseline_days is not None:
        settings["performance.baseline_days"] = baseline_days
    if settings["performance.regression_recent_hours"] >= settings["performance.baseline_days"] * 24:
        raise ValidationFailed("the recent window must be shorter than the baseline period")
    items = await advisor.regressions_for(s.id, settings)
    if not include_plan_changes:
        items = [i for i in items if i["regressed"]]
    show = p.authz.has("query.view_text", server_ref(s))
    texts = await _texts(db, s.id, {i["fingerprint"] for i in items}, show)
    for i in items:
        i["query_text"] = texts.get((i["database"] or "", i["fingerprint"]), "")
    plan_identity = s.engine in ("oracle", "mssql")
    return {
        "server_id": str(s.id),
        "engine": s.engine,
        "settings": {k.removeprefix("performance."): v for k, v in settings.items()},
        "plan_identity": plan_identity,
        "notes": []
        if plan_identity
        else ["plan changes are not detectable on this engine: its statement statistics carry no plan identity"],
        "text_masked": not show,
        "items": items,
    }


# ------------------------------------------------------------------------------------------
# period-vs-period comparison
# ------------------------------------------------------------------------------------------
def _period(start: datetime | None, end: datetime | None, name: str) -> tuple[datetime, datetime]:
    if start is None or end is None:
        raise ValidationFailed(f"period {name} needs a start and an end")
    start = start if start.tzinfo else start.replace(tzinfo=UTC)
    end = end if end.tzinfo else end.replace(tzinfo=UTC)
    if end <= start:
        raise ValidationFailed(f"period {name}: end must be after start")
    if end - start > MAX_COMPARE_SPAN:
        raise ValidationFailed(f"period {name} is limited to 31 days")
    if end > datetime.now(UTC) + timedelta(minutes=5):
        raise ValidationFailed(f"period {name} ends in the future")
    return start, end


PRESETS = {
    "hour_vs_previous": (timedelta(hours=1), timedelta(hours=1)),
    "day_vs_previous": (timedelta(days=1), timedelta(days=1)),
    "day_vs_week_ago": (timedelta(days=1), timedelta(days=7)),
    "week_vs_previous": (timedelta(days=7), timedelta(days=7)),
}


@router.get("/servers/{server_id}/query-compare", summary="Compare statement activity between two periods")
async def query_compare(
    server_id: uuid.UUID,
    db: DB,
    p: CurrentPrincipal,
    preset: Literal["hour_vs_previous", "day_vs_previous", "day_vs_week_ago", "week_vs_previous"] | None = None,
    a_start: datetime | None = None,
    a_end: datetime | None = None,
    b_start: datetime | None = None,
    b_end: datetime | None = None,
    database: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """Period A is the reference, period B the one under review. ``preset`` fills both:
    B = the last hour/day/week, A = the same length one period (or one week) earlier."""
    s = await load_server(db, p, server_id, "query.view")
    if preset:
        length, shift = PRESETS[preset]
        now = datetime.now(UTC)
        b = (now - length, now)
        a = (b[0] - shift, b[1] - shift)
    else:
        a, b = _period(a_start, a_end, "A"), _period(b_start, b_end, "B")
    out = await perf_intel.compare_periods(s.id, a, b, database=database, limit=limit)
    show = p.authz.has("query.view_text", server_ref(s))
    texts = await _texts(db, s.id, {i["fingerprint"] for i in out["items"]}, show)
    for i in out["items"]:
        i["query_text"] = texts.get((i["database"] or "", i["fingerprint"]), "")
    out.update({"preset": preset, "text_masked": not show, "engine": s.engine})
    return out


# ------------------------------------------------------------------------------------------
# capacity forecasts
# ------------------------------------------------------------------------------------------
@router.get("/servers/{server_id}/capacity-forecast", summary="Storage growth and days-to-full with 90% bands")
async def server_forecast(
    server_id: uuid.UUID,
    db: DB,
    p: CurrentPrincipal,
    scope: Literal["server", "database", "tablespace"] = "server",
    days: int = Query(30, ge=7, le=365),
    horizon_days: int = Query(90, ge=7, le=730),
) -> dict[str, Any]:
    s = await load_server(db, p, server_id, "performance.view")
    items = await perf_intel.capacity_forecast([s.id], scope, days=days, horizon_days=horizon_days)
    return {"server_id": str(s.id), "scope": scope, "history_days": days, "horizon_days": horizon_days, "items": items}


@router.get("/capacity/forecast", summary="Fleet storage forecasts, most urgent first")
async def fleet_forecast(
    db: DB,
    p: CurrentPrincipal,
    scope: Literal["server", "database", "tablespace"] = "server",
    days: int = Query(30, ge=7, le=365),
    horizon_days: int = Query(90, ge=7, le=730),
    limit: int = Query(100, ge=1, le=500),
    source: SourceQuery = None,
) -> dict[str, Any]:
    stmt = select(Server)
    flt = p.authz.server_filter("performance.view")
    if flt is not None:
        stmt = stmt.where(flt)
    src_cond = fleet_source.condition(await fleet_source.resolve(db, p, source))
    if src_cond is not None:
        stmt = stmt.where(src_cond)
    servers = {str(s.id): s for s in (await db.execute(stmt)).scalars()}
    items = await perf_intel.capacity_forecast(list(servers), scope, days=days, horizon_days=horizon_days)
    for i in items:
        sv = servers.get(i["server_id"])
        i["server_name"] = sv.name if sv else None
        i["engine"] = sv.engine if sv else None
        i.pop("band", None)  # per-object charts come from the server endpoint
        i.pop("history", None)
    return {"scope": scope, "horizon_days": horizon_days, "items": items[:limit], "total": len(items)}


# ------------------------------------------------------------------------------------------
# saved plans and plan-vs-plan comparison
# ------------------------------------------------------------------------------------------
def _plan_out(r: QueryPlanSnapshot, s: Server, show_text: bool, full: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": str(r.id),
        "server_id": str(s.id),
        "server_name": s.name,
        "engine": r.engine,
        "database": r.database,
        "fingerprint": r.fingerprint,
        "label": r.label,
        "statement": r.statement if show_text else mask_sql(r.statement),
        "statement_sha256": r.statement_sha256,
        "mode": r.mode,
        "total_cost": r.total_cost,
        "created_by": r.created_by,
        "created_at": r.created_at,
    }
    if full:
        d["plan"] = r.plan
    return d


async def _load_plan(db: DB, p: CurrentPrincipal, plan_id: uuid.UUID) -> tuple[QueryPlanSnapshot, Server]:
    r = await db.get(QueryPlanSnapshot, plan_id)
    if r is None:
        raise NotFound("Saved plan not found")
    s = await load_server(db, p, r.server_id)
    if not p.authz.has("query.explain", server_ref(s, r.database)):
        raise NotFound("Saved plan not found")
    return r, s


@router.get("/servers/{server_id}/plans", summary="Execution plans saved for comparison")
async def list_plans(
    server_id: uuid.UUID,
    db: DB,
    p: CurrentPrincipal,
    fingerprint: str | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id, "query.explain")
    stmt = select(QueryPlanSnapshot).where(QueryPlanSnapshot.server_id == s.id)
    if fingerprint:
        stmt = stmt.where(QueryPlanSnapshot.fingerprint == fingerprint)
    rows = (await db.execute(stmt.order_by(QueryPlanSnapshot.created_at.desc()).limit(limit))).scalars()
    show = p.authz.has("query.view_text", server_ref(s))
    return [_plan_out(r, s, show) for r in rows]


@router.get("/plans/compare", summary="Plan-vs-plan comparison of two saved plans")
async def compare_plans(a: uuid.UUID, b: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    ra, sa = await _load_plan(db, p, a)
    rb, sb = await _load_plan(db, p, b)
    diff = perf_intel.compare_plans(ra.plan, rb.plan)
    return {
        "a": _plan_out(ra, sa, p.authz.has("query.view_text", server_ref(sa)), full=True),
        "b": _plan_out(rb, sb, p.authz.has("query.view_text", server_ref(sb)), full=True),
        "same_statement": ra.statement_sha256 == rb.statement_sha256,
        "diff": diff,
    }


@router.get("/plans/{plan_id}", summary="One saved plan")
async def get_plan(plan_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    r, s = await _load_plan(db, p, plan_id)
    return _plan_out(r, s, p.authz.has("query.view_text", server_ref(s)), full=True)


@router.delete("/plans/{plan_id}", summary="Delete a saved plan")
async def delete_plan(plan_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    r, s = await _load_plan(db, p, plan_id)
    if r.created_by != p.username and not p.authz.has("performance.tune", server_ref(s)):
        raise PermissionDenied(
            "Only the author or a user with performance.tune can delete a saved plan",
            details={"permission": "performance.tune"},
        )
    await db.delete(r)
    await audit.record(
        "query.plan_deleted",
        actor=p,
        server=s,
        database=r.database,
        category="query",
        target_type="query_plan",
        target_id=str(r.id),
        object_name=r.label,
    )
    return {"deleted": str(plan_id)}
