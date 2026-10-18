"""Live and collected data for a single server: objects, access, activity, metrics."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.adapters.base import Capability
from app.api.deps import DB, CurrentPrincipal, load_server
from app.core.errors import ConfirmationRequired
from app.domain.masking import mask_sql
from app.models import AuditEvent, CollectorJob, Server
from app.schemas.common import Page, PageParams, apply_sort, paginate
from app.services import audit, metric_catalog
from app.services import metrics as metrics_svc
from app.services.authz import server_ref
from app.services.connections import (
    adapter_for,
    adapter_preferring,
    require_administration,
    require_capability,
    target,
)

router = APIRouter(prefix="/servers/{server_id}", tags=["server data"])


def _mask(p: CurrentPrincipal, s: Server, sql: str | None, database: str | None = None) -> str | None:
    if sql is None:
        return None
    if p.authz.has("query.view_text", server_ref(s, database)):
        return sql
    return mask_sql(sql)


# ---- databases / schemas / objects --------------------------------------------------------
@router.get("/databases")
async def databases(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id)
    require_capability(s, Capability.DATABASES)
    async with target(await adapter_for(db, s)) as a:
        items = await a.list_databases()
    ref = server_ref(s)
    if p.authz.has("database.view", ref):
        visible = items
    else:  # database-scoped grants: show only granted databases
        visible = [d for d in items if p.authz.has("database.view", server_ref(s, d.name))]
        if not visible:
            p.authz.require("database.view", ref)
    return [d.model_dump(mode="json") for d in visible]


@router.get("/databases/{database}/schemas")
async def schemas(server_id: uuid.UUID, database: str, db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id)
    if not p.authz.can_see("schema.view", server_ref(s, database)):
        p.authz.require("schema.view", server_ref(s, database))
    require_capability(s, Capability.SCHEMAS)
    async with target(await adapter_for(db, s)) as a:
        items = await a.list_schemas(database)
    if not p.authz.has("schema.view", server_ref(s, database)):
        items = [x for x in items if p.authz.has("schema.view", server_ref(s, database, x.name))]
    return [x.model_dump(mode="json") for x in items]


@router.get("/databases/{database}/schemas/{schema}/objects")
async def objects(
    server_id: uuid.UUID,
    database: str,
    schema: str,
    db: DB,
    p: CurrentPrincipal,
    type: str | None = Query(None, alias="type"),
) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id)
    p.authz.require("schema.view", server_ref(s, database, schema))
    require_capability(s, Capability.OBJECTS)
    async with target(await adapter_for(db, s)) as a:
        items = await a.list_objects(database, schema, type)
    return [x.model_dump(mode="json") for x in items]


@router.get("/databases/{database}/schemas/{schema}/objects/{name}")
async def object_detail(
    server_id: uuid.UUID, database: str, schema: str, name: str, db: DB, p: CurrentPrincipal, type: str = Query("table")
) -> dict[str, Any]:
    s = await load_server(db, p, server_id)
    p.authz.require("schema.view", server_ref(s, database, schema))
    async with target(await adapter_for(db, s)) as a:
        d = await a.get_object_detail(database, schema, name, type)
    return d.model_dump(mode="json")


# ---- access -----------------------------------------------------------------------------
@router.get("/database-users")
async def database_users(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id, "user.view")
    require_capability(s, Capability.USERS)
    async with target(await adapter_for(db, s)) as a:
        return [u.model_dump(mode="json") for u in await a.list_users()]


@router.get("/roles")
async def db_roles(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id, "user.view")
    require_capability(s, Capability.ROLES)
    async with target(await adapter_for(db, s)) as a:
        return [u.model_dump(mode="json") for u in await a.list_roles()]


@router.get("/privileges")
async def privileges(
    server_id: uuid.UUID,
    db: DB,
    p: CurrentPrincipal,
    database: str | None = None,
    grantee: str | None = None,
    limit: int = Query(2000, le=10000),
) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id)
    p.authz.require("privilege.view", server_ref(s, database))
    require_capability(s, Capability.PRIVILEGES)
    async with target(await adapter_for(db, s)) as a:
        return [x.model_dump(mode="json") for x in await a.get_privileges(database, grantee, limit)]


# ---- activity -----------------------------------------------------------------------------
@router.get("/sessions")
async def sessions(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id, "session.view")
    require_capability(s, Capability.SESSIONS)
    async with target(await adapter_for(db, s)) as a:
        rows = await a.get_sessions()
    out = []
    for r in rows:
        d = r.model_dump(mode="json")
        d["query"] = _mask(p, s, r.query, r.database)
        out.append(d)
    return out


@router.get("/locks")
async def locks(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id, "session.view")
    require_capability(s, Capability.LOCKS)
    async with target(await adapter_for(db, s)) as a:
        rows = await a.get_locks()
    out = []
    for r in rows:
        d = r.model_dump(mode="json")
        d["query"] = _mask(p, s, r.query, r.database)
        out.append(d)
    return out


class CancelRequest(BaseModel):
    terminate: bool = False
    reason: str | None = Field(None, max_length=1000)
    confirm: str | None = None


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(
    server_id: uuid.UUID, session_id: str, body: CancelRequest, db: DB, p: CurrentPrincipal
) -> dict[str, Any]:
    s = await load_server(db, p, server_id, "session.manage")
    risk = "DESTRUCTIVE" if body.terminate else "PRIVILEGED_CHANGE"
    if body.terminate and body.confirm != session_id:
        raise ConfirmationRequired(
            "Terminating a session rolls back its open transaction. Type the session id to confirm.",
            details={"risk": risk, "expected": "session id"},
        )
    action = "session.terminate" if body.terminate else "session.cancel"
    require_administration(s)
    require_capability(s, Capability.SESSION_TERMINATE if body.terminate else Capability.SESSION_CANCEL)
    try:
        async with target(await adapter_preferring(db, s, "admin", "monitoring")) as a:
            ok = await a.cancel_session(session_id, terminate=body.terminate)
    except Exception as exc:
        await audit.record(
            action,
            actor=p,
            server=s,
            result="failure",
            object_name=f"session:{session_id}",
            risk_class=risk,
            failure_reason=str(exc),
            change_reason=body.reason,
        )
        raise
    await audit.record(
        action,
        actor=p,
        server=s,
        result="success" if ok else "failure",
        object_name=f"session:{session_id}",
        risk_class=risk,
        change_reason=body.reason,
        failure_reason=None if ok else "session not found or already finished",
    )
    return {"ok": ok}


@router.get("/replication")
async def replication(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    s = await load_server(db, p, server_id, "performance.view")
    require_capability(s, Capability.REPLICATION)
    async with target(await adapter_for(db, s)) as a:
        return (await a.get_replication_status()).model_dump(mode="json")


@router.get("/configuration")
async def configuration(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id, "performance.view")
    require_capability(s, Capability.CONFIGURATION)
    async with target(await adapter_for(db, s)) as a:
        return [c.model_dump(mode="json") for c in await a.get_configuration()]


@router.get("/storage")
async def storage(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    s = await load_server(db, p, server_id, "performance.view")
    require_capability(s, Capability.STORAGE)
    async with target(await adapter_for(db, s)) as a:
        return (await a.get_storage_metrics()).model_dump(mode="json")


# ---- metrics ------------------------------------------------------------------------------
@router.get("/metrics")
async def metrics(
    server_id: uuid.UUID,
    db: DB,
    p: CurrentPrincipal,
    metric: list[str] = Query(default_factory=lambda: list(metric_catalog.DASHBOARD_METRICS)),
    range: str | None = Query("1h", description="15m|30m|1h|6h|12h|24h|7d|30d|90d"),
    start: datetime | None = None,
    end: datetime | None = None,
    labeled: bool = False,
    agg: str | None = Query(None, description="avg|min|max|sum|count|last|p50|p95|p99 (counters: rate|increase)"),
    max_points: int = Query(240, ge=10, le=1000),
) -> dict[str, Any]:
    s = await load_server(db, p, server_id, "performance.view")
    a, b = metrics_svc.resolve_range(range, start, end)
    data = await metrics_svc.series(
        [s.id], metric[:30], a, b, labeled=labeled, agg=agg, step=metrics_svc.step_for(a, b, max_points)
    )
    data["catalog"] = {m: metric_catalog.describe(m) for m in metric}
    return data


@router.get("/metrics/baseline", summary="Statistical baseline of one metric and how the current value compares")
async def metric_baseline(
    server_id: uuid.UUID, db: DB, p: CurrentPrincipal, metric: str, days: int = Query(7, ge=1, le=90)
) -> dict[str, Any]:
    s = await load_server(db, p, server_id, "performance.view")
    return await metrics_svc.baseline(s.id, metric, days)


@router.get("/metrics/capacity", summary="Storage growth trend and (when the history allows) days until full")
async def metric_capacity(
    server_id: uuid.UUID, db: DB, p: CurrentPrincipal, days: int = Query(30, ge=7, le=400)
) -> dict[str, Any]:
    s = await load_server(db, p, server_id, "performance.view")
    rows = await metrics_svc.capacity([s.id], days)
    return rows[0] if rows else {"server_id": str(s.id), "points": 0, "reason": "no storage history yet"}


@router.get("/metrics/latest")
async def latest_metrics(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    s = await load_server(db, p, server_id, "performance.view")
    lm = dict(s.latest_metrics or {})
    unavailable = lm.pop("_unavailable", {})
    collected = lm.pop("_collected_at", None)
    # The current operational state is cached on the server row in PostgreSQL by the collector
    # (cheap, available even while the time-series database is down); history lives in the TSDB.
    return {
        "collected_at": collected,
        "values": lm,
        "unavailable": unavailable,
        "catalog": {m: metric_catalog.describe(m) for m in lm},
        "source": "collector_cache",
    }


# ---- logs & audit -----------------------------------------------------------------------------
@router.get("/logs")
async def collection_log(
    server_id: uuid.UUID, db: DB, p: CurrentPrincipal, params: PageParams = Depends()
) -> Page[dict[str, Any]]:
    s = await load_server(db, p, server_id, "logs.view")
    stmt = select(CollectorJob).where(CollectorJob.server_id == s.id)
    stmt = apply_sort(
        stmt, params.sort or "-created_at", {"created_at": CollectorJob.created_at}, CollectorJob.created_at
    )
    items, total = await paginate(db, stmt, params.page, params.page_size)
    return Page(
        items=[
            {
                "id": str(j.id),
                "kind": j.kind,
                "status": j.status,
                "trigger": j.trigger,
                "created_at": j.created_at,
                "started_at": j.started_at,
                "finished_at": j.finished_at,
                "duration_ms": j.duration_ms,
                "error": j.last_error,
                "result": j.result,
                "attempts": j.attempts,
                "worker": j.locked_by,
            }
            for j in items
        ],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get("/audit")
async def server_audit(
    server_id: uuid.UUID, db: DB, p: CurrentPrincipal, params: PageParams = Depends()
) -> Page[dict[str, Any]]:
    s = await load_server(db, p, server_id)
    p.authz.require("audit.view")
    stmt = select(AuditEvent).where(AuditEvent.server_id == s.id).order_by(AuditEvent.ts.desc())
    items, total = await paginate(db, stmt, params.page, params.page_size)
    from app.api.v1.audit import event_out

    return Page(items=[event_out(e) for e in items], total=total, page=params.page, page_size=params.page_size)
