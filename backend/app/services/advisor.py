"""Builds evidence contexts, runs rule engines and persists findings."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterError
from app.advisors.engine import EvaluatedFinding, EvidenceContext, MetricWindow, evaluate
from app.models import Recommendation, SecurityFinding, Server
from app.services import app_settings, perf_intel, secrets
from app.services import metrics as metrics_svc
from app.services.collection import refresh_accounts
from app.services.connections import adapter_for

log = logging.getLogger(__name__)

WINDOW_HOURS = 6.0


#: sustained-condition checks evaluated on the time-series history: metric → (threshold, minutes)
SUSTAINED_CHECKS: dict[str, tuple[float, int]] = {"connections.utilization": (85.0, 15)}


async def metric_windows(
    db: AsyncSession | None, server_id: object, hours: float = WINDOW_HOURS
) -> dict[str, MetricWindow]:
    """avg / min / max / p95 / count of every metric over the analysis window, computed by the
    time-series database (one aggregate query per statistic for all metrics)."""
    wins = await metrics_svc.windows(str(server_id), hours=hours)
    return {
        k: MetricWindow(avg=w.avg, min=w.min, max=w.max, last=w.max, count=w.count, p95=w.p95) for k, w in wins.items()
    }


async def sustained_checks(server_id: object) -> dict[str, dict[str, Any]]:
    out = {}
    for metric, (threshold, minutes) in SUSTAINED_CHECKS.items():
        out[metric] = await metrics_svc.sustained(str(server_id), metric, threshold, minutes)
    return out


async def latest_query_stats(db: AsyncSession, server_id: object) -> list[dict[str, Any]]:
    """Statements ranked by execution time within the analysis window (time-series counters),
    with their normalized text from PostgreSQL."""
    from app.models import QueryFingerprint

    items = await metrics_svc.top_queries([str(server_id)], window_s=int(WINDOW_HOURS * 3600), limit=50)
    if not items:
        return []
    texts = {
        (r.fingerprint, r.database): r.normalized_query
        for r in (
            await db.execute(
                select(QueryFingerprint).where(
                    QueryFingerprint.server_id == server_id,
                    QueryFingerprint.fingerprint.in_([i["fingerprint"] for i in items]),
                )
            )
        ).scalars()
    }
    return [
        {
            "query_id": i["fingerprint"],
            "query_text": texts.get((i["fingerprint"], i["database"] or ""), ""),
            "database": i["database"],
            "calls": int(round(i["calls"])),
            "total_ms": i["total_ms"],
            "mean_ms": i["mean_ms"] or 0.0,
            "physical_reads": i["physical_reads"],
            "temp_blocks": i["temp_blocks"],
        }
        for i in items
        if i["calls"] > 0
    ]


PERF_SETTINGS = (
    "performance.regression_threshold_pct",
    "performance.regression_min_calls",
    "performance.regression_min_delta_ms",
    "performance.regression_recent_hours",
    "performance.baseline_days",
    "performance.forecast_warning_days",
    "performance.validation_window_hours",
)


async def perf_settings(db: AsyncSession) -> dict[str, Any]:
    return {k: await app_settings.get(db, k) for k in PERF_SETTINGS}


async def regressions_for(server_id: object, settings: dict[str, Any]) -> list[dict[str, Any]]:
    return await perf_intel.query_regressions(
        str(server_id),
        recent_hours=int(settings["performance.regression_recent_hours"]),
        baseline_days=int(settings["performance.baseline_days"]),
        threshold_pct=float(settings["performance.regression_threshold_pct"]),
        min_calls=int(settings["performance.regression_min_calls"]),
        min_delta_ms=float(settings["performance.regression_min_delta_ms"]),
    )


async def _perf_intel(server: Server, settings: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Regressions and storage forecasts; an unavailable history yields none (the rules then
    simply do not fire) instead of failing the whole advisor pass."""
    try:
        regressions = await regressions_for(server.id, settings)
    except Exception:  # noqa: BLE001
        log.warning("query regression analysis failed for %s", server.name, exc_info=True)
        regressions = []
    forecasts: list[dict[str, Any]] = []
    for scope in ("server", "tablespace"):
        try:
            forecasts += await perf_intel.capacity_forecast([server.id], scope)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            log.warning("capacity forecast (%s) failed for %s", scope, server.name, exc_info=True)
    return regressions, forecasts


async def build_context(db: AsyncSession, server: Server) -> EvidenceContext:
    cred = await secrets.get_credential(db, server.id, "monitoring")
    settings = await perf_settings(db)
    regressions, forecasts = await _perf_intel(server, settings)
    return EvidenceContext(
        server_id=str(server.id),
        server_name=server.name,
        engine=server.engine,
        environment_tier=server.environment.tier if server.environment else None,
        tls_mode=server.tls_mode,
        is_demo=server.is_demo,
        window_hours=WINDOW_HOURS,
        metrics=await metric_windows(db, server.id),
        sustained=await sustained_checks(server.id),
        latest={k: v for k, v in (server.latest_metrics or {}).items() if isinstance(v, (int, float))},
        query_stats=await latest_query_stats(db, server.id),
        capabilities=server.capabilities or {},
        monitoring_username=cred.username if cred else None,
        query_regressions=regressions,
        capacity_forecasts=forecasts,
        settings=settings,
    )


async def _persist(
    db: AsyncSession, server: Server, kind: Literal["performance", "security"], findings: list[EvaluatedFinding]
) -> dict[str, int]:
    model = Recommendation if kind == "performance" else SecurityFinding
    now = datetime.now(UTC)
    existing = {
        f.fingerprint: f for f in (await db.execute(select(model).where(model.server_id == server.id))).scalars()
    }
    seen: set[str] = set()
    created = updated = 0
    for ef in findings:
        fp = ef.fingerprint(str(server.id))
        if fp in seen:
            continue
        seen.add(fp)
        r = ef.result
        row = existing.get(fp)
        if row is None:
            row = model(
                fingerprint=fp, rule_id=ef.rule.rule_id, server_id=server.id, engine=server.engine, first_seen_at=now
            )
            db.add(row)
            created += 1
        else:
            updated += 1
            if row.status == "resolved":
                row.status, row.resolved_at, row.status_reason = "open", None, "reappeared"
                if kind == "performance" and row.validation:
                    row.validation = {**row.validation, "status": "reopened", "reopened_at": now.isoformat()}
        row.category = ef.rule.category
        row.severity = ef.severity
        row.title = r.title[:256]
        row.finding = r.finding
        row.evidence = r.evidence
        row.recommendation = r.recommendation
        row.risk = r.risk
        row.confidence = r.confidence
        row.guidance = r.guidance
        row.database = r.database
        row.affected_object = r.affected_object
        row.last_seen_at = now
        if kind == "performance":
            row.expected_impact = r.expected_impact or "—"
        else:
            row.remediation = r.remediation or r.guidance
    resolved = 0
    hours = int(await app_settings.get(db, "performance.validation_window_hours"))
    for fp, row in existing.items():
        if fp not in seen and row.status in ("open", "acknowledged"):
            row.status, row.resolved_at, row.status_reason = "resolved", now, "evidence no longer present"
            if kind == "performance":
                row.validation = await perf_intel.start_validation(row, now, hours)
            resolved += 1
    return {"created": created, "updated": updated, "auto_resolved": resolved}


async def run_performance(db: AsyncSession, server: Server) -> dict[str, Any]:
    ctx = await build_context(db, server)
    adapter = await adapter_for(db, server)
    try:
        ctx.tuning = await adapter.get_tuning_context()
    except AdapterError:
        ctx.tuning = {}
    finally:
        await adapter.close()
    return await _persist(db, server, "performance", evaluate(ctx, "performance"))


async def run_security(db: AsyncSession, server: Server) -> dict[str, Any]:
    ctx = await build_context(db, server)
    adapter = await adapter_for(db, server)
    try:
        ctx.security = await adapter.get_security_context()
    finally:
        await adapter.close()
    await refresh_accounts(db, server, ctx.security.get("roles") or [], ctx.security.get("direct_table_grants"))
    return await _persist(db, server, "security", evaluate(ctx, "security"))


async def run_validations(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Global job: measure the "after" window of resolved recommendations whose window has
    elapsed, and start validations that could not start (storage outage)."""
    now = now or datetime.now(UTC)
    hours = int(await app_settings.get(db, "performance.validation_window_hours"))
    rows = (
        await db.execute(
            select(Recommendation).where(
                Recommendation.status == "resolved",
                Recommendation.resolved_at.is_not(None),
                Recommendation.resolved_at >= now - timedelta(days=30),
            )
        )
    ).scalars()
    stats = {"completed": 0, "started": 0, "failed": 0}
    for r in rows:
        v = r.validation or {}
        try:
            if not v or v.get("status") == "pending":
                r.validation = await perf_intel.start_validation(r, r.resolved_at, hours)  # type: ignore[arg-type]
                stats["started"] += 1
            elif v.get("status") == "measuring":
                done = await perf_intel.complete_validation(r, now)
                if done is not None:
                    r.validation = done
                    stats["completed"] += 1
        except Exception:  # noqa: BLE001 - retried on the next run
            log.warning("validation of recommendation %s failed", r.id, exc_info=True)
            stats["failed"] += 1
    await db.flush()
    return stats
