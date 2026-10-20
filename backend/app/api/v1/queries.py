"""Query performance analysis and execution plans."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.adapters.base import Capability
from app.api import source as fleet_source
from app.api.deps import DB, CurrentPrincipal, load_server, rate_limit
from app.api.source import SourceQuery
from app.core.errors import ConfirmationRequired, PermissionDenied, ValidationFailed
from app.domain.masking import mask_sql
from app.domain.sql_classifier import classify_sql
from app.models import QueryFingerprint, QueryPlanSnapshot, Server
from app.services import app_settings, audit
from app.services import metrics as metrics_svc
from app.services.authz import server_ref
from app.services.connections import adapter_for, adapter_preferring, require_administration, require_capability, target

router = APIRouter(tags=["queries"])


SORTS = set(metrics_svc.QUERY_SORTS)


def _window(range_: str) -> int:
    minutes = metrics_svc.RANGES.get(range_)
    if minutes is None:
        raise ValidationFailed("invalid range", details={"allowed": list(metrics_svc.RANGES)})
    return minutes * 60


async def _fingerprint_meta(db: DB, keys: set[tuple[str, str, str]]) -> dict[tuple[str, str, str], QueryFingerprint]:
    if not keys:
        return {}
    sids = {uuid.UUID(k[0]) for k in keys}
    fps = {k[2] for k in keys}
    rows = (
        await db.execute(
            select(QueryFingerprint).where(QueryFingerprint.server_id.in_(sids), QueryFingerprint.fingerprint.in_(fps))
        )
    ).scalars()
    return {(str(r.server_id), r.database, r.fingerprint): r for r in rows}


async def _text_filter(db: DB, server_ids: list[uuid.UUID], q: str | None) -> list[str] | None:
    """Fingerprints whose normalized text matches the search (searched in PostgreSQL, so the
    time-series query is restricted by fingerprint instead of by text)."""
    if not q:
        return None
    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    return list(
        (
            await db.execute(
                select(QueryFingerprint.fingerprint)
                .where(QueryFingerprint.server_id.in_(server_ids), QueryFingerprint.normalized_query.ilike(pattern))
                .distinct()
                .limit(500)
            )
        ).scalars()
    )


def _shape(item: dict[str, Any], meta: QueryFingerprint | None, show_text: bool) -> dict[str, Any]:
    text = meta.normalized_query if meta else ""
    return {
        **item,
        "query_id": item["fingerprint"],
        "engine_query_id": meta.engine_query_id if meta else None,
        "username": meta.username if meta else None,
        "query_text": text if show_text else mask_sql(text),
        "first_seen": meta.first_seen.isoformat() if meta else None,
        "snapshot_at": item.get("last_seen"),
        "interval": {
            "seconds": item["window_seconds"],
            "calls": item["calls"],
            "calls_per_sec": item["calls_per_sec"],
            "mean_ms": item["mean_ms"],
        },
    }


@router.get("/servers/{server_id}/query-stats")
async def query_stats(
    server_id: uuid.UUID,
    db: DB,
    p: CurrentPrincipal,
    sort: str = "total_ms",
    min_mean_ms: float | None = None,
    q: str | None = None,
    range: str = Query("1h", description="activity window: 15m|30m|1h|6h|12h|24h|7d|30d|90d"),
    limit: int = Query(100, le=500),
) -> dict[str, Any]:
    """Statements ranked by their activity in the window (from the time-series database)."""
    s = await load_server(db, p, server_id, "query.view")
    key = sort.lstrip("-")
    if key not in SORTS:
        raise ValidationFailed("invalid sort", details={"allowed": sorted(SORTS)})
    window = _window(range)
    fps = await _text_filter(db, [s.id], q)
    items = await metrics_svc.top_queries([s.id], window_s=window, sort=key, limit=limit, fingerprints=fps)
    meta = await _fingerprint_meta(db, {(i["server_id"], i["database"] or "", i["fingerprint"]) for i in items})
    show = p.authz.has("query.view_text", server_ref(s))
    rows = [_shape(i, meta.get((i["server_id"], i["database"] or "", i["fingerprint"])), show) for i in items]
    if min_mean_ms is not None:
        rows = [r for r in rows if (r["mean_ms"] or 0) >= min_mean_ms]
    cap = (s.capabilities or {}).get("query_statistics")
    return {
        "server_id": str(s.id),
        "snapshot_at": max((r["snapshot_at"] for r in rows if r["snapshot_at"]), default=None),
        "window_seconds": window,
        "capability": cap,
        "text_masked": not show,
        "items": rows,
    }


@router.get("/servers/{server_id}/query-stats/{fingerprint}/history")
async def query_history(
    server_id: uuid.UUID, fingerprint: str, db: DB, p: CurrentPrincipal, hours: int = Query(24, ge=1, le=24 * 90)
) -> dict[str, Any]:
    s = await load_server(db, p, server_id, "query.view")
    if not fingerprint.isalnum() or len(fingerprint) > 32:
        raise ValidationFailed("invalid query fingerprint")
    end = datetime.now(UTC)
    points = await metrics_svc.query_history(s.id, fingerprint, end - timedelta(hours=hours), end)
    meta = (
        await db.execute(
            select(QueryFingerprint)
            .where(QueryFingerprint.server_id == s.id, QueryFingerprint.fingerprint == fingerprint)
            .order_by(QueryFingerprint.last_seen.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    show = p.authz.has("query.view_text", server_ref(s))
    return {
        "query_id": fingerprint,
        "fingerprint": fingerprint,
        "query_text": (meta.normalized_query if show else mask_sql(meta.normalized_query)) if meta else None,
        "database": meta.database or None if meta else None,
        "first_seen": meta.first_seen.isoformat() if meta else None,
        "last_seen": meta.last_seen.isoformat() if meta else None,
        "points": points,
    }


@router.get("/servers/{server_id}/query-fingerprints", summary="Known normalized statements of a server")
async def query_fingerprints(
    server_id: uuid.UUID, db: DB, p: CurrentPrincipal, limit: int = Query(200, le=2000)
) -> list[dict[str, Any]]:
    s = await load_server(db, p, server_id, "query.view")
    show = p.authz.has("query.view_text", server_ref(s))
    rows = (
        await db.execute(
            select(QueryFingerprint)
            .where(QueryFingerprint.server_id == s.id)
            .order_by(QueryFingerprint.last_seen.desc())
            .limit(limit)
        )
    ).scalars()
    return [
        {
            "fingerprint": r.fingerprint,
            "normalized_query": r.normalized_query if show else mask_sql(r.normalized_query),
            "engine": r.engine,
            "database": r.database or None,
            "engine_query_id": r.engine_query_id,
            "first_seen": r.first_seen.isoformat(),
            "last_seen": r.last_seen.isoformat(),
        }
        for r in rows
    ]


@router.get("/queries/top", summary="Top queries across all visible servers")
async def top_queries(
    db: DB,
    p: CurrentPrincipal,
    sort: str = "total_ms",
    limit: int = Query(50, le=200),
    min_mean_ms: float | None = None,
    range: str = Query("1h", description="activity window"),
    source: SourceQuery = None,
) -> list[dict[str, Any]]:
    if sort not in SORTS:
        raise ValidationFailed("invalid sort", details={"allowed": sorted(SORTS)})
    # RBAC first: only servers the caller may analyse reach the time-series query (by id).
    flt = p.authz.server_filter("query.view")
    servers_q = select(Server)
    if flt is not None:
        servers_q = servers_q.where(flt)
    src_cond = fleet_source.condition(await fleet_source.resolve(db, p, source))
    if src_cond is not None:
        servers_q = servers_q.where(src_cond)
    servers = {str(s.id): s for s in (await db.execute(servers_q)).scalars()}
    if not servers:
        return []
    items = await metrics_svc.top_queries(list(servers), window_s=_window(range), sort=sort, limit=limit)
    meta = await _fingerprint_meta(db, {(i["server_id"], i["database"] or "", i["fingerprint"]) for i in items})
    out = []
    for i in items:
        s = servers.get(i["server_id"])
        if s is None:  # defensive: never return telemetry of a server outside the RBAC set
            continue
        d = _shape(
            i,
            meta.get((i["server_id"], i["database"] or "", i["fingerprint"])),
            p.authz.has("query.view_text", server_ref(s)),
        )
        if min_mean_ms is not None and (d["mean_ms"] or 0) < min_mean_ms:
            continue
        d.update(
            {
                "server_name": s.name,
                "engine": s.engine,
                "is_demo": s.is_demo,
                "data_source": s.data_source,
                "environment": s.environment.name if s.environment else None,
            }
        )
        out.append(d)
    return out


class ExplainRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)
    database: str | None = None
    analyze: bool = False
    confirm: str | None = None
    reason: str | None = Field(None, max_length=1000)
    #: save the resulting plan for later comparison (plan-vs-plan) under this label
    save_label: str | None = Field(None, min_length=1, max_length=128)
    #: the statement's fingerprint when explained from the query list (links the saved plan)
    fingerprint: str | None = Field(None, max_length=32, pattern=r"^[0-9a-zA-Z]+$")


#: a saved plan keeps the engine's raw plan document only up to this size
MAX_RAW_PLAN_CHARS = 512_000


@router.post("/servers/{server_id}/explain", dependencies=[rate_limit("expensive")])
async def explain(server_id: uuid.UUID, body: ExplainRequest, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    s = await load_server(db, p, server_id)
    ref = server_ref(s, body.database)
    cls = classify_sql(body.sql, s.engine)
    if cls.statement_count > 1:
        raise ValidationFailed("Explain one statement at a time")
    if cls.statement_type in ("EXPLAIN", "EXPLAIN_ANALYZE"):
        raise ValidationFailed("Submit the statement without the EXPLAIN keyword; use the Analyze option instead")
    p.authz.require("query.explain", ref)
    is_prod = bool(s.environment and s.environment.tier == "production")
    risk = "READ"
    if body.analyze:
        p.authz.require("query.explain_analyze", ref)
        require_capability(s, Capability.EXPLAIN_ANALYZE)
        risk = "LOW_RISK_CHANGE" if cls.read_only else cls.risk.value
        if not cls.read_only and not p.authz.has("query.modify", ref):
            raise PermissionDenied(
                "EXPLAIN ANALYZE of a data-modifying statement requires query.modify",
                details={"permission": "query.modify"},
            )
        if not cls.read_only:
            # executing a change (even rolled back) is a change action: never on Monitoring-only servers
            require_administration(s)
        if is_prod and not await app_settings.get(db, "workspace.allow_explain_analyze_in_production"):
            raise PermissionDenied("EXPLAIN ANALYZE on production servers is disabled by platform settings")
        expected = s.name if (is_prod or not cls.read_only) else "ANALYZE"
        if body.confirm != expected:
            raise ConfirmationRequired(
                "EXPLAIN ANALYZE executes the statement (changes are rolled back, but locks, load and side effects "
                "such as sequence increments still happen).",
                details={
                    "risk": risk,
                    "expected": "server name" if expected == s.name else "ANALYZE",
                    "production": is_prod,
                    "statement_type": cls.statement_type,
                    "reasons": cls.reasons,
                },
            )
    else:
        require_capability(s, Capability.EXPLAIN)
    digest = hashlib.sha256(body.sql.encode()).hexdigest()[:16]
    action = "query.explain_analyze" if body.analyze else "query.explain"
    try:
        changes = body.analyze and not cls.read_only
        adapter = (
            (await adapter_for(db, s, "admin")) if changes else (await adapter_preferring(db, s, "admin", "monitoring"))
        )
        async with target(adapter) as a:
            result = await a.explain_query(body.sql, body.database, analyze=body.analyze)
    except Exception as exc:
        await audit.record(
            action,
            actor=p,
            server=s,
            database=body.database,
            result="failure",
            category="query",
            risk_class=risk,
            failure_reason=str(exc)[:500],
            change_reason=body.reason,
            details={"sha256": digest, "statement_type": cls.statement_type},
        )
        raise
    await audit.record(
        action,
        actor=p,
        server=s,
        database=body.database,
        category="query",
        risk_class=risk,
        change_reason=body.reason,
        details={"sha256": digest, "statement_type": cls.statement_type, "objects": cls.objects},
    )
    out = result.model_dump(mode="json")
    if body.save_label:
        plan = dict(out)
        if len(json.dumps(plan.get("raw"), default=str)) > MAX_RAW_PLAN_CHARS:
            plan["raw"] = None
            plan["notes"] = [*plan.get("notes", []), "raw plan document omitted (too large to store)"]
        snap = QueryPlanSnapshot(
            id=uuid.uuid4(),
            server_id=s.id,
            database=body.database,
            fingerprint=body.fingerprint,
            label=body.save_label,
            statement=body.sql,
            statement_sha256=hashlib.sha256(body.sql.encode()).hexdigest(),
            engine=s.engine,
            mode=result.mode,
            total_cost=result.total_cost,
            plan=plan,
            created_by=p.username,
        )
        db.add(snap)
        await audit.record(
            "query.plan_saved",
            actor=p,
            server=s,
            database=body.database,
            category="query",
            target_type="query_plan",
            target_id=str(snap.id),
            object_name=body.save_label,
            details={"sha256": digest, "mode": result.mode},
        )
        out["snapshot_id"] = str(snap.id)
    out["classification"] = cls.to_dict()
    out["simulated"] = s.is_demo
    return out
