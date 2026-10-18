"""Unversioned operational endpoints: liveness, readiness, Prometheus metrics."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.core.metrics import APP_DB_POOL
from app.db.session import get_engine

router = APIRouter(tags=["ops"])


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", summary="Readiness probe with per-component status")
async def readyz(response: Response) -> dict[str, object]:
    """Ready = the application database is reachable and migrated.

    The time-series database is reported separately and does **not** make the backend
    unready: without it inventory, administration and security features keep working and
    monitoring degrades to "Monitoring Storage Unavailable"."""
    components: dict[str, dict[str, object]] = {"application": {"status": "ok"}}
    ready = True
    try:
        async with get_engine().connect() as conn:
            version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        components["postgresql"] = {"status": "ok", "schema_version": version}
    except Exception as exc:  # noqa: BLE001
        ready = False
        components["postgresql"] = {"status": "unavailable", "error": type(exc).__name__}
    ts = await timeseries_component()
    components["timeseries"] = ts
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not_ready",
        "degraded": ts["status"] != "ok",
        "schema_version": components["postgresql"].get("schema_version"),
        "components": components,
    }


async def timeseries_component() -> dict[str, object]:
    from app.core.config import get_settings
    from app.timeseries.repository import get_repository
    from app.timeseries.writer import get_writer

    s = get_settings()
    if not s.timeseries_enabled:
        return {"status": "disabled"}
    repo = get_repository()
    try:
        ok = await asyncio.wait_for(repo.health_check(), timeout=3)
    except Exception:  # noqa: BLE001
        ok = False
    w = get_writer().status()
    return {
        "status": "ok" if ok else "unavailable",
        "engine": repo.engine,
        "write_buffer_pending": w["pending"],
        "last_write_success": w["last_success"].isoformat() if w["last_success"] else None,
        "last_write_failure": w["last_failure"].isoformat() if w["last_failure"] else None,
    }


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
async def metrics() -> Response:
    try:
        pool = get_engine().pool
        APP_DB_POOL.set(pool.checkedout())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001, S110 - metrics are best effort
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
