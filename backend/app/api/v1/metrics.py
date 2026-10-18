"""Metrics API: fleet-wide and multi-server telemetry reads, plus monitoring-storage admin.

Every endpoint resolves the caller's visible servers in PostgreSQL (RBAC scope + real /
simulated filter) **before** querying the time-series database, and the query is restricted
to those servers by their stable ids. The browser never talks to the time-series database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api import source as fleet_source
from app.api.deps import DB, CurrentPrincipal, load_server
from app.api.source import SourceQuery
from app.core.errors import ValidationFailed
from app.models import Server
from app.services import audit, metric_catalog, monitoring_storage
from app.services import metrics as metrics_svc
from app.timeseries.catalog import all_definitions
from app.timeseries.repository import TimeSeriesUnavailable

router = APIRouter(tags=["metrics"])
MAX_UNGROUPED_SERVERS = 50


async def visible_servers(
    db: DB, p: CurrentPrincipal, perm: str, source: str | None, ids: list[uuid.UUID] | None = None
) -> dict[str, Server]:
    stmt = select(Server)
    flt = p.authz.server_filter(perm)
    if flt is not None:
        stmt = stmt.where(flt)
    cond = fleet_source.condition(await fleet_source.resolve(db, p, source))
    if cond is not None:
        stmt = stmt.where(cond)
    if ids:
        stmt = stmt.where(Server.id.in_(ids))
    return {str(s.id): s for s in (await db.execute(stmt)).scalars()}


@router.get("/metrics/catalog", summary="Normalized metric catalog (names, types, units, intervals)")
async def catalog(p: CurrentPrincipal) -> list[dict[str, Any]]:
    out = []
    for d in all_definitions():
        desc = metric_catalog.describe(d.key)
        out.append(
            {
                "key": d.key,
                "name": d.name,
                "type": d.type.value,
                "unit": d.unit,
                "interval": d.interval.value,
                "labels": ["server_id", "engine", "data_source", *d.labels],
                "engines": d.engines,
                "label": desc["label"],
                "description": desc["description"],
            }
        )
    return out


@router.get("/metrics/range", summary="Range query over one or many servers (optionally aggregated)")
async def range_query(
    db: DB,
    p: CurrentPrincipal,
    metric: list[str] = Query(..., description="metric keys, e.g. connections.total"),
    server_id: list[uuid.UUID] | None = Query(None, description="restrict to these servers (default: all visible)"),
    range: str | None = Query("1h", description="15m|30m|1h|6h|12h|24h|7d|30d|90d"),
    start: datetime | None = None,
    end: datetime | None = None,
    agg: str | None = Query(None, description="per-step aggregation: avg|min|max|sum|count|last|p50|p95|p99"),
    group: Literal["none", "sum", "avg", "max", "min"] = Query("none", description="combine servers"),
    labeled: bool = False,
    max_points: int = Query(240, ge=10, le=1000),
    source: SourceQuery = None,
) -> dict[str, Any]:
    servers = await visible_servers(db, p, "performance.view", source, server_id)
    if group == "none" and len(servers) > MAX_UNGROUPED_SERVERS:
        raise ValidationFailed(
            f"at most {MAX_UNGROUPED_SERVERS} servers per ungrouped query; pass server_id or group=sum|avg|max|min",
            details={"servers": len(servers)},
        )
    a, b = metrics_svc.resolve_range(range, start, end)
    data = await metrics_svc.series(
        list(servers),
        metric[:30],
        a,
        b,
        aggregate_servers=None if group == "none" else group,
        labeled=labeled,
        agg=agg,
        step=metrics_svc.step_for(a, b, max_points),
    )
    data["servers"] = {
        sid: {"name": s.name, "engine": s.engine, "data_source": s.data_source} for sid, s in servers.items()
    }
    data["catalog"] = {m: metric_catalog.describe(m) for m in metric}
    return data


@router.get("/metrics/current", summary="Latest value of metrics for many servers in one call")
async def current(
    db: DB,
    p: CurrentPrincipal,
    metric: list[str] | None = Query(None),
    server_id: list[uuid.UUID] | None = Query(None),
    source: SourceQuery = None,
) -> dict[str, Any]:
    servers = await visible_servers(db, p, "performance.view", source, server_id)
    keys = metric or list(metric_catalog.DASHBOARD_METRICS)
    try:
        values = await metrics_svc.latest(list(servers), keys)
        src, ts_status = "timeseries", "available"
    except TimeSeriesUnavailable:
        # fall back to the collector's operational cache in PostgreSQL
        values = {
            sid: {
                **{k: v for k, v in (s.latest_metrics or {}).items() if k in keys},
                "_collected_at": (s.latest_metrics or {}).get("_collected_at"),
            }
            for sid, s in servers.items()
        }
        src, ts_status = "collector_cache", "unavailable"
    return {"source": src, "timeseries": ts_status, "metrics": keys, "values": values}


@router.get("/metrics/capacity", summary="Storage growth and days-until-full for visible servers")
async def fleet_capacity(
    db: DB, p: CurrentPrincipal, days: int = Query(30, ge=7, le=400), source: SourceQuery = None
) -> list[dict[str, Any]]:
    servers = await visible_servers(db, p, "performance.view", source)
    rows = await metrics_svc.capacity(list(servers), days)
    for r in rows:
        s = servers.get(r["server_id"])
        if s:
            r.update({"server_name": s.name, "engine": s.engine, "data_source": s.data_source})
    rows.sort(key=lambda r: (r["days_until_full"] is None, r["days_until_full"] or 0))
    return rows


@router.get("/servers/{server_id}/databases/{database}/metrics", summary="Query telemetry of one database")
async def database_metrics(
    server_id: uuid.UUID,
    database: str,
    db: DB,
    p: CurrentPrincipal,
    range: str | None = Query("1h"),
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    """Executions/s and execution time/s of the statements in one database (sum over
    fingerprints), from the query telemetry series."""
    from app.services.authz import server_ref
    from app.timeseries.catalog import QUERY_BY_FIELD
    from app.timeseries.model import Selector
    from app.timeseries.repository import get_repository

    s = await load_server(db, p, server_id, "query.view")
    if not p.authz.has("query.view", server_ref(s, database)):
        from app.core.errors import PermissionDenied

        raise PermissionDenied("not allowed for this database")
    a, b = metrics_svc.resolve_range(range, start, end)
    step = max(metrics_svc.step_for(a, b), 300)
    repo = get_repository()
    out: dict[str, Any] = {"server_id": str(s.id), "database": database, "step_seconds": step, "series": []}
    for field, label in (("calls", "executions_per_sec"), ("total_ms", "exec_ms_per_sec")):
        res = await repo.query_range(
            Selector.of(QUERY_BY_FIELD[field].name, [str(s.id)], database=database),
            a,
            b,
            step,
            rollup="rate",
            group_by=("database",),
            group_agg="sum",
        )
        for ser in res:
            out["series"].append(
                {"metric": label, "points": [{"t": metrics_svc._iso(t), "v": round(v, 4)} for t, v in ser.points]}
            )
    return out


# ---- platform administration: monitoring storage -----------------------------------------
@router.get("/platform/monitoring-storage", summary="Time-series database status, retention and ingestion")
async def storage_status(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require_anywhere("collectors.view")
    return await monitoring_storage.status(db)


class RetentionIn(BaseModel):
    days: int = Field(ge=1, le=3650)


@router.put("/platform/monitoring-storage/retention", summary="Set the monitoring retention policy")
async def set_retention(body: RetentionIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("settings.manage")
    before = await monitoring_storage.retention_days(db)
    await monitoring_storage.set_retention_days(db, body.days, p.username)
    await audit.record(
        "settings.update",
        actor=p,
        category="platform",
        details={"monitoring.retention_days": {"from": before, "to": body.days}},
    )
    return await monitoring_storage.status(db)
