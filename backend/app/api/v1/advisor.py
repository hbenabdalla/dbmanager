"""Performance recommendations, security findings and posture."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from app.advisors.engine import catalog
from app.api import source as fleet_source
from app.api.deps import DB, CurrentPrincipal, load_server
from app.api.source import SourceQuery
from app.core.errors import NotFound
from app.jobs.runner import enqueue
from app.models import DbAccount, Environment, Recommendation, SecurityFinding, Server
from app.schemas.common import Message, Page, PageParams, apply_sort
from app.services import app_settings, audit, perf_intel

router = APIRouter(tags=["advisor"])

SEVERITY_RANK = case(
    {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}, value=Recommendation.severity, else_=5
)
CATEGORY_GROUPS = {
    "query": ["queries", "indexes", "statistics", "schema"],
    "configuration": ["configuration", "memory", "tempdb", "observability"],
    "performance": [
        "connections",
        "locking",
        "replication",
        "transactions",
        "capacity",
        "waits",
        "sessions",
        "maintenance",
        "storage",
    ],
}


def _finding_out(r: Any, s: Server, kind: str) -> dict[str, Any]:
    d = {
        "id": str(r.id),
        "rule_id": r.rule_id,
        "server_id": str(s.id),
        "server_name": s.name,
        "engine": r.engine,
        "environment": s.environment.name if s.environment else None,
        "is_demo": s.is_demo,
        "database": r.database,
        "affected_object": r.affected_object,
        "category": r.category,
        "severity": r.severity,
        "title": r.title,
        "finding": r.finding,
        "evidence": r.evidence,
        "recommendation": r.recommendation,
        "risk": r.risk,
        "confidence": r.confidence,
        "guidance": r.guidance,
        "status": r.status,
        "status_reason": r.status_reason,
        "status_changed_by": r.status_changed_by,
        "first_seen_at": r.first_seen_at,
        "last_seen_at": r.last_seen_at,
        "resolved_at": r.resolved_at,
    }
    if kind == "performance":
        d["expected_impact"] = r.expected_impact
        d["validation"] = r.validation
    else:
        d["remediation"] = r.remediation
    return d


async def _list(
    db: DB,
    p: CurrentPrincipal,
    model: Any,
    perm: str,
    kind: str,
    params: PageParams,
    server_id: uuid.UUID | None,
    severity: list[str] | None,
    status: list[str] | None,
    category: list[str] | None,
    group: str | None,
    engine: list[str] | None,
    environment_id: uuid.UUID | None,
    source: str | None = None,
) -> Page[dict[str, Any]]:
    stmt = select(model, Server).join(Server, Server.id == model.server_id)
    flt = p.authz.server_filter(perm)
    if flt is not None:
        stmt = stmt.where(flt)
    if server_id is None:
        src_cond = fleet_source.condition(await fleet_source.resolve(db, p, source))
        if src_cond is not None:
            stmt = stmt.where(src_cond)
    if server_id:
        stmt = stmt.where(model.server_id == server_id)
    stmt = stmt.where(model.status.in_(status or ["open", "acknowledged"]))
    if severity:
        stmt = stmt.where(model.severity.in_(severity))
    if category:
        stmt = stmt.where(model.category.in_(category))
    if group and group in CATEGORY_GROUPS:
        stmt = stmt.where(model.category.in_(CATEGORY_GROUPS[group]))
    if engine:
        stmt = stmt.where(model.engine.in_(engine))
    if environment_id:
        stmt = stmt.where(Server.environment_id == environment_id)
    if params.q:
        like = f"%{params.q.lower()}%"
        stmt = stmt.where(
            func.lower(model.title).like(like)
            | func.lower(Server.name).like(like)
            | func.lower(model.rule_id).like(like)
        )
    rank = case({"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}, value=model.severity, else_=5)
    stmt = apply_sort(
        stmt,
        params.sort,
        {
            "severity": rank,
            "last_seen_at": model.last_seen_at,
            "first_seen_at": model.first_seen_at,
            "server": Server.name,
            "confidence": model.confidence,
        },
        rank,
    )
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await db.execute(stmt.limit(params.page_size).offset((params.page - 1) * params.page_size))).all()
    return Page(
        items=[_finding_out(r, s, kind) for r, s in rows], total=total, page=params.page, page_size=params.page_size
    )


@router.get("/recommendations")
async def recommendations(
    db: DB,
    p: CurrentPrincipal,
    params: PageParams = Depends(),
    server_id: uuid.UUID | None = None,
    severity: list[str] | None = Query(None),
    status: list[str] | None = Query(None),
    category: list[str] | None = Query(None),
    group: Literal["performance", "query", "configuration"] | None = None,
    engine: list[str] | None = Query(None),
    environment_id: uuid.UUID | None = None,
    source: SourceQuery = None,
) -> Page[dict[str, Any]]:
    return await _list(
        db,
        p,
        Recommendation,
        "performance.view",
        "performance",
        params,
        server_id,
        severity,
        status,
        category,
        group,
        engine,
        environment_id,
        source,
    )


@router.get("/security/findings")
async def security_findings(
    db: DB,
    p: CurrentPrincipal,
    params: PageParams = Depends(),
    server_id: uuid.UUID | None = None,
    severity: list[str] | None = Query(None),
    status: list[str] | None = Query(None),
    category: list[str] | None = Query(None),
    engine: list[str] | None = Query(None),
    environment_id: uuid.UUID | None = None,
    source: SourceQuery = None,
) -> Page[dict[str, Any]]:
    return await _list(
        db,
        p,
        SecurityFinding,
        "security.view",
        "security",
        params,
        server_id,
        severity,
        status,
        category,
        None,
        engine,
        environment_id,
        source,
    )


class StatusChange(BaseModel):
    status: Literal["open", "acknowledged", "dismissed", "resolved"]
    reason: str | None = Field(None, max_length=2000)


async def _change(
    db: DB, p: CurrentPrincipal, model: Any, item_id: uuid.UUID, body: StatusChange, perm: str, kind: str
) -> dict[str, Any]:
    r = await db.get(model, item_id)
    if r is None:
        raise NotFound("Not found")
    s = await load_server(db, p, r.server_id, perm)
    old = r.status
    r.status, r.status_reason, r.status_changed_by = body.status, body.reason, p.username
    r.resolved_at = datetime.now(UTC) if body.status == "resolved" else None
    if model is Recommendation:
        if body.status == "resolved" and old != "resolved":
            hours = int(await app_settings.get(db, "performance.validation_window_hours"))
            r.validation = await perf_intel.start_validation(r, r.resolved_at, hours)  # type: ignore[arg-type]
        elif body.status != "resolved" and old == "resolved" and r.validation:
            r.validation = {**r.validation, "status": "reopened", "reopened_at": datetime.now(UTC).isoformat()}
    await audit.record(
        f"{kind}.status",
        actor=p,
        server=s,
        target_type=kind,
        target_id=str(r.id),
        object_name=r.rule_id,
        change_reason=body.reason,
        details={"from": old, "to": body.status},
    )
    return _finding_out(r, s, "performance" if model is Recommendation else "security")


@router.patch("/recommendations/{item_id}")
async def update_recommendation(item_id: uuid.UUID, body: StatusChange, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    """Change review status. Recommendations are never applied automatically."""
    return await _change(db, p, Recommendation, item_id, body, "performance.tune", "recommendation")


@router.patch("/security/findings/{item_id}")
async def update_finding(item_id: uuid.UUID, body: StatusChange, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    return await _change(db, p, SecurityFinding, item_id, body, "security.manage", "security_finding")


@router.get("/recommendations/validation-summary", summary="Outcome of resolved recommendations (before/after)")
async def validation_summary(db: DB, p: CurrentPrincipal, source: SourceQuery = None) -> dict[str, Any]:
    """How many resolved recommendations improved, did not change or regressed the measured
    metric, over the visible servers."""
    stmt = (
        select(Recommendation, Server)
        .join(Server, Server.id == Recommendation.server_id)
        .where(Recommendation.validation.is_not(None))
    )
    flt = p.authz.server_filter("performance.view")
    if flt is not None:
        stmt = stmt.where(flt)
    src_cond = fleet_source.condition(await fleet_source.resolve(db, p, source))
    if src_cond is not None:
        stmt = stmt.where(src_cond)
    counts: dict[str, int] = {}
    recent: list[dict[str, Any]] = []
    for r, s in (await db.execute(stmt.order_by(Recommendation.resolved_at.desc()))).all():
        st = (r.validation or {}).get("status", "unknown")
        counts[st] = counts.get(st, 0) + 1
        if st in ("improved", "unchanged", "regressed") and len(recent) < 20:
            recent.append(_finding_out(r, s, "performance"))
    return {"counts": counts, "recent": recent}


@router.get("/advisor/rules", summary="Rule catalog of the deterministic advisors")
async def rules(p: CurrentPrincipal) -> list[dict[str, Any]]:
    return catalog()


@router.post("/servers/{server_id}/security-scan", response_model=Message)
async def run_scan(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    s = await load_server(db, p, server_id, "security.manage")
    queued = await enqueue(s.id, "security_scan", "manual", db=db)
    await audit.record("security.scan", actor=p, server=s, category="collector")
    return Message(message="Security scan queued" if queued else "A scan is already queued")


@router.get("/security/posture")
async def posture(db: DB, p: CurrentPrincipal, source: SourceQuery = None) -> dict[str, Any]:
    flt = p.authz.server_filter("security.view")
    sq = select(Server)
    if flt is not None:
        sq = sq.where(flt)
    src_cond = fleet_source.condition(await fleet_source.resolve(db, p, source))
    if src_cond is not None:
        sq = sq.where(src_cond)
    servers = (await db.execute(sq)).scalars().all()
    ids = [s.id for s in servers]
    if not ids:
        return {"score": None, "by_severity": {}, "servers": [], "by_category": {}}
    rows = (
        await db.execute(
            select(SecurityFinding.server_id, SecurityFinding.severity, func.count())
            .where(SecurityFinding.server_id.in_(ids), SecurityFinding.status.in_(["open", "acknowledged"]))
            .group_by(SecurityFinding.server_id, SecurityFinding.severity)
        )
    ).all()
    cats = dict(
        (
            await db.execute(
                select(SecurityFinding.category, func.count())
                .where(SecurityFinding.server_id.in_(ids), SecurityFinding.status.in_(["open", "acknowledged"]))
                .group_by(SecurityFinding.category)
            )
        ).all()
    )
    weights = {"critical": 25, "high": 10, "medium": 4, "low": 1, "info": 0}
    per: dict[uuid.UUID, dict[str, int]] = {}
    totals: dict[str, int] = {}
    for sid, sev, n in rows:
        per.setdefault(sid, {})[sev] = n
        totals[sev] = totals.get(sev, 0) + n
    out = []
    for s in servers:
        sev = per.get(s.id, {})
        penalty = sum(weights.get(k, 0) * v for k, v in sev.items())
        out.append(
            {
                "server_id": str(s.id),
                "server_name": s.name,
                "engine": s.engine,
                "is_demo": s.is_demo,
                "environment": s.environment.name if s.environment else None,
                "score": max(0, 100 - penalty),
                "by_severity": sev,
            }
        )
    out.sort(key=lambda x: x["score"])
    scored = [x["score"] for x in out]
    return {
        "score": round(sum(scored) / len(scored)) if scored else None,
        "by_severity": totals,
        "servers": out,
        "by_category": cats,
    }


@router.get("/access/privileged-accounts", summary="Administrative accounts across the visible fleet")
async def privileged_accounts(
    db: DB,
    p: CurrentPrincipal,
    params: PageParams = Depends(),
    only_admin: bool = True,
    dormant_days: int | None = None,
    source: SourceQuery = None,
) -> Page[dict[str, Any]]:
    stmt = (
        select(DbAccount, Server)
        .join(Server, Server.id == DbAccount.server_id)
        .outerjoin(Environment, Environment.id == Server.environment_id)
    )
    flt = p.authz.server_filter("user.view")
    if flt is not None:
        stmt = stmt.where(flt)
    src_cond = fleet_source.condition(await fleet_source.resolve(db, p, source))
    if src_cond is not None:
        stmt = stmt.where(src_cond)
    if only_admin:
        stmt = stmt.where(DbAccount.is_admin.is_(True))
    if dormant_days:
        from datetime import timedelta

        stmt = stmt.where(DbAccount.last_login_at < datetime.now(UTC) - timedelta(days=dormant_days))
    if params.q:
        stmt = stmt.where(func.lower(DbAccount.name).like(f"%{params.q.lower()}%"))
    stmt = apply_sort(
        stmt,
        params.sort,
        {"name": DbAccount.name, "server": Server.name, "last_login_at": DbAccount.last_login_at},
        Server.name,
    )
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (await db.execute(stmt.limit(params.page_size).offset((params.page - 1) * params.page_size))).all()
    return Page(
        items=[
            {
                "server_id": str(s.id),
                "server_name": s.name,
                "engine": s.engine,
                "is_demo": s.is_demo,
                "environment": s.environment.name if s.environment else None,
                "name": a.name,
                "kind": a.kind,
                "can_login": a.can_login,
                "is_admin": a.is_admin,
                "admin_reasons": a.admin_reasons,
                "member_of": a.member_of,
                "direct_grant_count": a.direct_grant_count,
                "last_login_at": a.last_login_at,
                "seen_at": a.seen_at,
            }
            for a, s in rows
        ],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/servers/{server_id}/accounts")
async def server_accounts(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id, "user.view")
    rows = (await db.execute(select(DbAccount).where(DbAccount.server_id == s.id).order_by(DbAccount.name))).scalars()
    return [
        {
            "name": a.name,
            "kind": a.kind,
            "can_login": a.can_login,
            "is_admin": a.is_admin,
            "admin_reasons": a.admin_reasons,
            "member_of": a.member_of,
            "direct_grant_count": a.direct_grant_count,
            "last_login_at": a.last_login_at,
            "seen_at": a.seen_at,
        }
        for a in rows
    ]
