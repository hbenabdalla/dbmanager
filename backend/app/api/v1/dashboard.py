"""Fleet dashboard aggregates (computed only over servers the caller may see)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import case, func, select

from app.api import source as fleet_source
from app.api.deps import DB, CurrentPrincipal
from app.api.source import SourceQuery
from app.models import DatabaseEntry, DataSource, Recommendation, SecurityFinding, Server
from app.services import metrics as metrics_svc
from app.services.inventory import engine_label, implementation
from app.timeseries.repository import TimeSeriesError

router = APIRouter(tags=["dashboard"])
SEV = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


async def _source_counts(db: DB, p: CurrentPrincipal) -> dict[str, int]:
    stmt = select(Server.data_source, func.count()).group_by(Server.data_source)
    flt = p.authz.server_filter("server.view")
    if flt is not None:
        stmt = stmt.where(flt)
    rows = dict((await db.execute(stmt)).all())
    sim = int(rows.get(DataSource.SIMULATED, 0))
    return {"real": sum(int(v) for k, v in rows.items() if k != DataSource.SIMULATED), "simulated": sim}


@router.get("/fleet/sources", summary="Real vs simulated servers visible to the caller")
async def fleet_sources(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    c = await _source_counts(db, p)
    return {**c, "total": c["real"] + c["simulated"], "simulation_active": c["simulated"] > 0}


@router.get("/dashboard/summary")
async def summary(db: DB, p: CurrentPrincipal, source: SourceQuery = None) -> dict[str, Any]:
    src = await fleet_source.resolve(db, p, source, default="auto")
    src_cond = fleet_source.condition(src)
    stmt = select(Server)
    flt = p.authz.server_filter("server.view")
    if flt is not None:
        stmt = stmt.where(flt)
    if src_cond is not None:
        stmt = stmt.where(src_cond)
    servers = list((await db.execute(stmt)).scalars().all())
    ids = [s.id for s in servers]
    perf_flt = p.authz.server_filter("performance.view")
    sec_flt = p.authz.server_filter("security.view")

    health: dict[str, int] = {}
    engines: dict[str, int] = {}
    envs: dict[str, dict[str, Any]] = {}
    active_conns = 0.0
    total_bytes = 0.0
    for s in servers:
        health[s.health_status] = health.get(s.health_status, 0) + 1
        engines[s.engine] = engines.get(s.engine, 0) + 1
        en = s.environment.name if s.environment else "Unassigned"
        e = envs.setdefault(
            en,
            {
                "name": en,
                "tier": s.environment.tier if s.environment else None,
                "order": s.environment.sort_order if s.environment else 999,
                "servers": 0,
                "health": {},
            },
        )
        e["servers"] += 1
        e["health"][s.health_status] = e["health"].get(s.health_status, 0) + 1
        lm = s.latest_metrics or {}
        if s.health_status != "unavailable":
            active_conns += float(lm.get("connections.active") or 0)
            total_bytes += float(lm.get("storage.database_bytes") or 0)

    db_count = (
        (
            await db.execute(
                select(func.count())
                .select_from(DatabaseEntry)
                .where(DatabaseEntry.server_id.in_(ids), DatabaseEntry.properties["is_system"].astext != "true")
            )
        ).scalar_one()
        if ids
        else 0
    )

    async def _count(model: Any, extra_flt: Any, *conds: Any) -> dict[str, int]:
        if not ids:
            return {}
        q = (
            select(model.severity, func.count())
            .join(Server, Server.id == model.server_id)
            .where(model.status.in_(["open", "acknowledged"]), *conds)
        )
        if extra_flt is not None:
            q = q.where(extra_flt)
        if src_cond is not None:
            q = q.where(src_cond)
        return dict((await db.execute(q.group_by(model.severity))).all())

    rec_sev = await _count(Recommendation, perf_flt) if p.authz.has_anywhere("performance.view") else {}
    sec_sev = await _count(SecurityFinding, sec_flt) if p.authz.has_anywhere("security.view") else {}

    rank = {"unavailable": 0, "critical": 1, "warning": 2, "unknown": 3, "healthy": 4}
    attention = sorted(
        [s for s in servers if s.health_status in ("unavailable", "critical", "warning")],
        key=lambda s: (rank.get(s.health_status, 9), s.name),
    )[:12]

    top: list[dict[str, Any]] = []
    if p.authz.has_anywhere("performance.view") and ids:
        sev_rank = case(SEV, value=Recommendation.severity, else_=5)
        q = (
            select(Recommendation, Server)
            .join(Server, Server.id == Recommendation.server_id)
            .where(Recommendation.status == "open")
        )
        if perf_flt is not None:
            q = q.where(perf_flt)
        if src_cond is not None:
            q = q.where(src_cond)
        for r, s in (await db.execute(q.order_by(sev_rank, Recommendation.confidence.desc()).limit(8))).all():
            top.append(
                {
                    "id": str(r.id),
                    "rule_id": r.rule_id,
                    "title": r.title,
                    "severity": r.severity,
                    "category": r.category,
                    "server_id": str(s.id),
                    "server_name": s.name,
                    "engine": s.engine,
                    "confidence": r.confidence,
                }
            )

    trends: dict[str, Any] = {}
    perf_ids = [s.id for s in servers if perf_flt is None or p.authz.has("performance.view", _ref(s))]
    if perf_ids:
        end = datetime.now(UTC)
        start = end - timedelta(hours=24)
        try:
            # three fleet-wide queries, run concurrently, each one request for any fleet size
            conns, lat, tps = await asyncio.gather(
                metrics_svc.series(perf_ids, ["connections.total"], start, end, aggregate_servers="sum"),
                metrics_svc.series(perf_ids, ["query.mean_latency_ms"], start, end, aggregate_servers="avg"),
                metrics_svc.series(perf_ids, ["transactions.per_sec"], start, end, aggregate_servers="sum"),
            )
            trends = {
                "connections": conns["series"][0]["points"] if conns["series"] else [],
                "latency_ms": lat["series"][0]["points"] if lat["series"] else [],
                "tps": tps["series"][0]["points"] if tps["series"] else [],
                "step_seconds": conns["step_seconds"],
            }
        except TimeSeriesError:
            # Monitoring storage down: the rest of the dashboard (from PostgreSQL) still renders.
            trends = {"connections": [], "latency_ms": [], "tps": [], "step_seconds": None, "unavailable": True}

    capacity = sorted(
        [
            {
                "server_id": str(s.id),
                "server_name": s.name,
                "engine": s.engine,
                "bytes": (s.latest_metrics or {}).get("storage.database_bytes"),
            }
            for s in servers
            if (s.latest_metrics or {}).get("storage.database_bytes")
        ],
        key=lambda x: -(x["bytes"] or 0),
    )[:10]

    return {
        "kpis": {
            "servers": len(servers),
            "healthy": health.get("healthy", 0),
            "warning": health.get("warning", 0),
            "critical": health.get("critical", 0),
            "unavailable": health.get("unavailable", 0),
            "unknown": health.get("unknown", 0),
            "databases": db_count,
            "active_connections": round(active_conns),
            "critical_findings": sec_sev.get("critical", 0) + sec_sev.get("high", 0),
            "open_recommendations": sum(rec_sev.values()),
            "total_storage_bytes": total_bytes,
        },
        "health": health,
        "engines": [
            {"engine": k, "label": engine_label(k), "count": v} for k, v in sorted(engines.items(), key=lambda x: -x[1])
        ],
        "environments": sorted(envs.values(), key=lambda e: e["order"]),
        "fleet": [
            {
                "id": str(s.id),
                "name": s.name,
                "engine": s.engine,
                "health": s.health_status,
                "environment": s.environment.name if s.environment else "Unassigned",
                "reasons": s.health_reasons[:3],
                "is_demo": s.is_demo,
                "data_source": s.data_source,
                "implementation": implementation(s),
            }
            for s in sorted(servers, key=lambda s: ((s.environment.sort_order if s.environment else 999), s.name))
        ],
        "attention": [
            {
                "id": str(s.id),
                "name": s.name,
                "engine": s.engine,
                "health": s.health_status,
                "environment": s.environment.name if s.environment else None,
                "reasons": s.health_reasons,
                "last_collected_at": s.last_collected_at,
                "is_demo": s.is_demo,
            }
            for s in attention
        ],
        "recommendations_by_severity": rec_sev,
        "findings_by_severity": sec_sev,
        "top_recommendations": top,
        "trends": trends,
        "capacity": capacity,
        "demo": any(s.is_demo for s in servers),
        "source": src,
        "sources": await _source_counts(db, p),
    }


def _ref(s: Server):  # type: ignore[no-untyped-def]
    from app.services.authz import server_ref

    return server_ref(s)
