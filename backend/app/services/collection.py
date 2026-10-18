"""Collection pipeline: adapter → collector → normalizer → batch writer → time-series DB.

Called by background jobs and by "collect now" / "test connection" actions. PostgreSQL only
receives the *current* operational state of a server (``latest_metrics`` cache, health,
collector state, query fingerprint metadata); history goes to the time-series database
through :mod:`app.timeseries.writer`, which never blocks or fails the collection when the
time-series database is down.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterError, AdapterNotSupported, MetricSnapshot, QueryStat
from app.core.config import get_settings
from app.models import CollectorJob, DatabaseEntry, DbAccount, MetricSample, QueryFingerprint, QueryStatSnapshot, Server
from app.services import metric_catalog
from app.services.connections import adapter_for
from app.timeseries.catalog import name_for
from app.timeseries.model import Sample
from app.timeseries.normalizer import base_labels, normalize_query_stats, normalize_snapshot
from app.timeseries.writer import get_writer

log = logging.getLogger(__name__)


# -------------------------------------------------------------------------------------
# circuit breaker bookkeeping (stored on the server row => shared across workers)
# -------------------------------------------------------------------------------------
def mark_success(server: Server) -> None:
    server.consecutive_failures = 0
    server.circuit_open_until = None
    server.connection_status = "ok"
    server.last_error = None
    server.last_connected_at = datetime.now(UTC)


def mark_failure(server: Server, error: str) -> None:
    s = get_settings()
    server.consecutive_failures += 1
    server.connection_status = "failed"
    server.last_error = error[:2000]
    if server.consecutive_failures >= s.circuit_failure_threshold:
        backoff = min(60 * 2 ** (server.consecutive_failures - s.circuit_failure_threshold), 3600)
        server.circuit_open_until = datetime.now(UTC) + timedelta(seconds=backoff)


# -------------------------------------------------------------------------------------
def snapshot_to_rows(server: Server, snap: MetricSnapshot, derived: dict[str, float]) -> list[dict[str, Any]]:
    """Legacy PostgreSQL rows (only with ``DBM_METRICS_PG_DUAL_WRITE=true``)."""
    ts = snap.collected_at
    values = {**snap.gauges, **derived}
    rows = [
        {"server_id": server.id, "metric": k, "ts": ts, "value": float(v), "labels": None}
        for k, v in values.items()
        if v is not None
    ]
    rows += [
        {"server_id": server.id, "metric": m, "ts": ts, "value": float(v), "labels": lbl} for m, lbl, v in snap.labeled
    ]
    return rows


async def persist_snapshot(db: AsyncSession, server: Server, snap: MetricSnapshot) -> dict[str, float]:
    derived, state = metric_catalog.derive(
        snap.counters, server.collector_state.get("metrics"), snap.collected_at.timestamp()
    )
    ts_state = dict(server.collector_state.get("timeseries") or {})
    get_writer().submit(normalize_snapshot(server, snap, derived, ts_state))
    if get_settings().metrics_pg_dual_write:
        rows = snapshot_to_rows(server, snap, derived)
        if rows:
            await db.execute(insert(MetricSample), rows)
    latest = {**snap.gauges, **derived}
    # keep previous derived values when this is the first sample after a restart
    merged = {**{k: v for k, v in (server.latest_metrics or {}).items() if not k.startswith("_")}, **latest}
    merged["_collected_at"] = snap.collected_at.isoformat()
    merged["_unavailable"] = snap.unavailable  # type: ignore[assignment]
    server.latest_metrics = merged
    server.collector_state = {**server.collector_state, "metrics": state, "timeseries": ts_state}
    server.last_collected_at = snap.collected_at
    status, reasons = metric_catalog.compute_health(latest)
    server.health_status, server.health_reasons = status, reasons
    return latest


async def record_unavailable(db: AsyncSession, server: Server, error: str) -> None:
    """Persist an availability failure (used after the job's savepoint was rolled back)."""
    mark_failure(server, error)
    now = datetime.now(UTC)
    server.health_status, server.health_reasons = "unavailable", [error[:200]]
    get_writer().submit([Sample.make(name_for("availability.up"), base_labels(server), 0.0, now)])
    if get_settings().metrics_pg_dual_write:
        await db.execute(
            insert(MetricSample),
            [{"server_id": server.id, "metric": "availability.up", "ts": now, "value": 0.0, "labels": None}],
        )
    server.latest_metrics = {**(server.latest_metrics or {}), "availability.up": 0.0, "_collected_at": now.isoformat()}


#: job kind → collection component shown on the server page
COMPONENTS = {
    "collect_metrics": "performance",
    "collect_query_stats": "query_statistics",
    "discover_inventory": "inventory",
    "generate_recommendations": "recommendations",
    "security_scan": "security",
}


def classify_reason(reason: str) -> str:
    """Map an adapter's "why not" text to a component status the UI can explain."""
    r = reason.lower()
    if any(w in r for w in ("grant ", "privilege", "permission", "not allowed", "access denied")):
        return "insufficient_privilege"
    if "license" in r or "licensed" in r:
        return "license_required"
    if any(w in r for w in ("enable", "install", " is off", "set ", "must be loaded", "create the", "explain tables")):
        return "configuration_required"
    return "not_supported"


def set_component(server: Server, kind: str, status: str, message: str | None = None) -> None:
    name = COMPONENTS.get(kind, kind)
    comps = dict((server.collector_state or {}).get("components") or {})
    comps[name] = {
        "status": status,
        "message": (message or None) and message[:500],
        "at": datetime.now(UTC).isoformat(),
    }
    server.collector_state = {**(server.collector_state or {}), "components": comps}


async def collect_metrics(db: AsyncSession, server: Server) -> dict[str, Any]:
    s = get_settings()
    state = server.collector_state or {}
    last_slow = state.get("slow_at")
    now = datetime.now(UTC)
    slow = not last_slow or (now - datetime.fromisoformat(last_slow)).total_seconds() >= s.interval_slow_seconds - 5
    adapter = await adapter_for(db, server)
    try:
        snap = await adapter.get_performance_metrics(slow=slow)
    finally:
        await adapter.close()
    mark_success(server)
    latest = await persist_snapshot(db, server, snap)
    if slow:
        server.collector_state = {**server.collector_state, "slow_at": now.isoformat()}
    limited = {k: v for k, v in snap.unavailable.items() if classify_reason(v) == "insufficient_privilege"}
    if limited:
        set_component(
            server, "collect_metrics", "partial", "; ".join(f"{k}: {v}" for k, v in list(limited.items())[:3])
        )
    else:
        set_component(server, "collect_metrics", "ok")
    return {"metrics": len(latest), "health": server.health_status, "unavailable": len(snap.unavailable)}


async def collect_query_stats(db: AsyncSession, server: Server) -> dict[str, Any]:
    adapter = await adapter_for(db, server)
    try:
        stats = await adapter.get_query_statistics(limit=100)
    except AdapterNotSupported as exc:
        set_component(server, "collect_query_stats", classify_reason(exc.reason), exc.reason)
        return {"skipped": exc.reason}
    finally:
        await adapter.close()
    now = datetime.now(UTC)
    written = await store_query_stats(db, server, stats, now)
    server.collector_state = {**server.collector_state, "query_stats_at": now.isoformat()}
    set_component(server, "collect_query_stats", "ok")
    return {"queries": len(stats), "fingerprints": written}


async def store_query_stats(db: AsyncSession, server: Server, stats: list[QueryStat], now: datetime) -> int:
    """Fingerprint metadata → PostgreSQL, fingerprint-keyed counters → time-series DB."""
    s = get_settings()
    known = set(
        (await db.execute(select(QueryFingerprint.fingerprint).where(QueryFingerprint.server_id == server.id)))
        .scalars()
        .all()
    )
    samples, rows = normalize_query_stats(
        server, stats, now, top_n=s.query_stats_top_n, known=known, max_fingerprints=s.query_fingerprints_max_per_server
    )
    await upsert_fingerprints(db, server, rows, now)
    get_writer().submit(samples)
    if s.metrics_pg_dual_write and stats:
        await db.execute(insert(QueryStatSnapshot), [_legacy_query_row(server, q, now) for q in stats])
    return len(rows)


async def upsert_fingerprints(db: AsyncSession, server: Server, rows: list[Any], now: datetime) -> None:
    if not rows:
        return
    values = {}
    for r in rows:  # one row per (fingerprint, database) — the statement may be listed per user
        values[(r.fingerprint, (r.database or "")[:128])] = {
            "server_id": server.id,
            "fingerprint": r.fingerprint,
            "database": (r.database or "")[:128],
            "engine": server.engine,
            "username": r.username,
            "normalized_query": r.normalized_text,
            "engine_query_id": r.engine_query_id,
            "data_source": server.data_source,
            "first_seen": now,
            "last_seen": now,
        }
    stmt = pg_insert(QueryFingerprint).values(list(values.values()))
    stmt = stmt.on_conflict_do_update(
        constraint="uq_query_fingerprints_server_fp_db",
        set_={
            "last_seen": stmt.excluded.last_seen,
            "username": stmt.excluded.username,
            "engine_query_id": stmt.excluded.engine_query_id,
        },
    )
    await db.execute(stmt)


def _legacy_query_row(server: Server, q: QueryStat, now: datetime) -> dict[str, Any]:
    return {
        "server_id": server.id,
        "ts": now,
        "query_id": q.query_id,
        "database": q.database,
        "username": q.username,
        "query_text": q.query_text[:20000],
        "calls": q.calls,
        "total_ms": q.total_ms,
        "mean_ms": q.mean_ms,
        "max_ms": q.max_ms,
        "rows": q.rows,
        "cpu_ms": q.cpu_ms,
        "logical_reads": q.logical_reads,
        "physical_reads": q.physical_reads,
        "temp_blocks": q.temp_blocks,
        "lock_wait_ms": q.lock_wait_ms,
        "extra": q.extra or None,
    }


async def discover(db: AsyncSession, server: Server) -> dict[str, Any]:
    """Refresh version, capabilities and database inventory."""
    adapter = await adapter_for(db, server)
    try:
        info = await adapter.get_server_info()
        caps = await adapter.discover_capabilities()
        try:
            dbs = await adapter.list_databases()
        except AdapterError:
            dbs = []
    finally:
        await adapter.close()
    mark_success(server)
    set_component(server, "discover_inventory", "ok")
    server.version = info.version
    if info.cloud_provider and not server.cloud_provider:
        server.cloud_provider = info.cloud_provider
    server.capabilities = {k.value: v.model_dump(mode="json") for k, v in caps.items()}
    server.collector_state = {**server.collector_state, "server_info": info.model_dump(mode="json")}
    now = datetime.now(UTC)
    existing = {
        e.name: e
        for e in (await db.execute(select(DatabaseEntry).where(DatabaseEntry.server_id == server.id))).scalars()
    }
    seen = set()
    for d in dbs:
        seen.add(d.name)
        e = existing.get(d.name) or DatabaseEntry(server_id=server.id, name=d.name)
        e.size_bytes, e.owner, e.discovered_at = d.size_bytes, d.owner, now
        e.properties = {"is_system": d.is_system, "encoding": d.encoding, "accessible": d.accessible}
        db.add(e)
    gone = [n for n in existing if n not in seen]
    if gone and dbs:
        await db.execute(
            delete(DatabaseEntry).where(DatabaseEntry.server_id == server.id, DatabaseEntry.name.in_(gone))
        )
    return {"version": info.version, "databases": len(dbs)}


async def refresh_accounts(
    db: AsyncSession, server: Server, roles: list[dict[str, Any]], direct_grants: list[dict[str, Any]] | None = None
) -> None:
    now = datetime.now(UTC)
    grants = {g["grantee"]: g["grants"] for g in direct_grants or []}
    await db.execute(delete(DbAccount).where(DbAccount.server_id == server.id))
    rows = []
    for r in roles:
        rows.append(
            {
                "server_id": server.id,
                "name": r["name"][:256],
                "kind": "user" if r.get("can_login") else "role",
                "can_login": bool(r.get("can_login")),
                "is_admin": bool(r.get("is_superuser") or r.get("admin_reasons")),
                "admin_reasons": r.get("admin_reasons") or [],
                "member_of": r.get("member_of") or [],
                "direct_grant_count": int(grants.get(r["name"], 0)),
                "last_login_at": datetime.fromisoformat(r["last_login_at"]) if r.get("last_login_at") else None,
                "password_expires_at": datetime.fromisoformat(r["valid_until"]) if r.get("valid_until") else None,
                "attributes": r.get("attributes") or {},
                "seen_at": now,
            }
        )
    if rows:
        await db.execute(insert(DbAccount), rows)


async def retention(db: AsyncSession) -> dict[str, Any]:
    """PostgreSQL housekeeping. Time-series retention is enforced natively by the time-series
    database (``-retentionPeriod``); here only the legacy metric tables (transition period),
    stale query fingerprints, job history and sessions are pruned, and pending time-series
    deletions (requested while the TSDB was down) are retried."""
    s = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=s.metrics_retention_days)
    m = await db.execute(delete(MetricSample).where(MetricSample.ts < cutoff))
    q = await db.execute(delete(QueryStatSnapshot).where(QueryStatSnapshot.ts < cutoff))
    j = await db.execute(
        delete(CollectorJob).where(
            CollectorJob.created_at < datetime.now(UTC) - timedelta(days=s.job_history_retention_days),
            CollectorJob.status.in_(("done", "failed", "skipped")),
        )
    )
    from app.services.auth import purge_expired_sessions

    sessions = await purge_expired_sessions(db)
    from app.services import monitoring_storage

    fp_cutoff = datetime.now(UTC) - timedelta(days=await monitoring_storage.retention_days(db))
    f = await db.execute(delete(QueryFingerprint).where(QueryFingerprint.last_seen < fp_cutoff))
    pending = await monitoring_storage.retry_pending_deletes(db)
    return {
        "legacy_metric_rows": m.rowcount,
        "legacy_query_rows": q.rowcount,
        "query_fingerprints": f.rowcount,
        "jobs": j.rowcount,
        "sessions": sessions,
        "timeseries_pending_deletes": pending,
    }


async def timed(coro: Any) -> tuple[Any, float]:
    start = time.perf_counter()
    res = await coro
    return res, (time.perf_counter() - start) * 1000
