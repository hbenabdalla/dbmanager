"""Simulation lifecycle: generate -> use -> regenerate -> remove.

* **generate / regenerate** are queued as one background job (kind ``simulation``) on the
  platform's existing job queue; the worker reports progress on the ``simulation_runs`` row.
  Large profiles therefore never run inside an HTTP request.
* **remove** runs synchronously in one transaction (set-based deletes).
* Only one simulation operation can be active at a time.
* Every operation is audited (request and outcome), without credentials or secrets.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterConnectionError
from app.adapters.demo.adapter import DemoAdapter
from app.core.errors import Conflict, ValidationFailed
from app.core.logging import request_id_ctx
from app.db.session import session_scope
from app.models import (
    Application,
    Cluster,
    CollectorJob,
    DataSource,
    Environment,
    Server,
    ServerTag,
    SimulationRun,
    Site,
)
from app.seed.bootstrap import default_org, ensure_environments
from app.services import audit, collection
from app.services.connections import adapter_for
from app.services.principal import Principal
from app.simulation import repository
from app.simulation.generators import metrics as metric_gen
from app.simulation.generators import queries as query_gen
from app.simulation.generators import recommendations as rec_gen
from app.simulation.generators import security as sec_gen
from app.simulation.generators.fleet import FleetPlan, demo_profile, plan_fleet
from app.simulation.profiles import PROFILES, SimulationProfile, get_profile
from app.timeseries.repository import get_repository
from app.timeseries.writer import get_writer

log = logging.getLogger("app.simulation")

JOB_KIND = "simulation"
ACTIVE = ("pending", "running")
SIM = DataSource.SIMULATED
#: an outage is simulated for this long before "now" on unavailable servers
OUTAGE = timedelta(hours=2)


# ---------------------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------------------
def run_dict(r: SimulationRun | None) -> dict[str, Any] | None:
    if r is None:
        return None
    return {
        "id": str(r.id),
        "operation": r.operation,
        "profile": r.profile,
        "seed": r.seed,
        "status": r.status,
        "progress": r.progress,
        "phase": r.phase,
        "requested_by": r.requested_by,
        "created_at": r.created_at,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "stats": r.stats or {},
        "error": r.error,
    }


async def _recover_stale(db: AsyncSession) -> None:
    """Fail runs whose job no longer exists (e.g. the backend restarted mid-generation)."""
    active = (await db.execute(select(SimulationRun).where(SimulationRun.status.in_(ACTIVE)))).scalars().all()
    if not active:
        return
    job_alive = (
        await db.execute(
            select(func.count())
            .select_from(CollectorJob)
            .where(CollectorJob.kind == JOB_KIND, CollectorJob.status.in_(ACTIVE))
        )
    ).scalar_one()
    if job_alive:
        return
    now = datetime.now(UTC)
    for r in active:
        if r.created_at < now - timedelta(minutes=2):
            r.status, r.error, r.finished_at = "failed", "interrupted: the background job is no longer running", now


async def status(db: AsyncSession) -> dict[str, Any]:
    await _recover_stale(db)
    c = await repository.counts(db)
    runs = (await db.execute(select(SimulationRun).order_by(SimulationRun.created_at.desc()).limit(20))).scalars().all()
    active = next((r for r in runs if r.status in ACTIVE), None)
    current = next(
        (r for r in runs if r.operation in ("generate", "regenerate", "legacy") and r.status == "completed"), None
    )
    last_remove = next((r for r in runs if r.operation == "remove" and r.status == "completed"), None)
    if current and last_remove and last_remove.created_at > current.created_at:
        current = None  # the fleet it describes has been removed since
    present = c["servers"] > 0
    return {
        "simulation_present": present,
        "profile": current.profile if (current and present) else None,
        "seed": current.seed if (current and present) else None,
        "generated_at": (current.finished_at or current.created_at) if (current and present) else None,
        "generated_by": current.requested_by if (current and present) else None,
        "legacy": bool(current and present and current.operation == "legacy"),
        "servers": c["servers"],
        "real_servers": c["real_servers"],
        "databases": c["databases"],
        "metrics": c["metrics"],  # legacy PostgreSQL rows (transition period)
        "queries": c["queries"],
        "telemetry_samples": c["telemetry_samples"],
        "query_fingerprints": c["query_fingerprints"],
        "security_findings": c["security_findings"],
        "recommendations": c["recommendations"],
        "sites": c["sites"],
        "clusters": c["clusters"],
        "applications": c["applications"],
        "active_run": run_dict(active),
        "last_run": run_dict(runs[0] if runs else None),
        "history": [run_dict(r) for r in runs[:10]],
    }


def profiles() -> list[dict[str, object]]:
    return [p.to_dict() for p in PROFILES.values()]


# ---------------------------------------------------------------------------------------
# requests (API / CLI)
# ---------------------------------------------------------------------------------------
async def _ensure_idle(db: AsyncSession) -> None:
    await _recover_stale(db)
    busy = (
        await db.execute(select(SimulationRun).where(SimulationRun.status.in_(ACTIVE)).limit(1))
    ).scalar_one_or_none()
    if busy is not None:
        raise Conflict(
            f"A simulation {busy.operation} is already {busy.status} ({busy.progress}% done). Wait for it to finish.",
            code="simulation_busy",
        )


def _resolve_profile(key: str) -> SimulationProfile:
    try:
        return get_profile(key)
    except KeyError as exc:
        raise ValidationFailed(str(exc), code="unknown_profile") from exc


async def request_generation(
    db: AsyncSession, actor: Principal | None, *, profile: str, seed: int | None, operation: str = "generate"
) -> SimulationRun:
    """Queue a generate/regenerate run. The worker picks it up within seconds."""
    from app.jobs.runner import enqueue

    prof = _resolve_profile(profile)
    await _ensure_idle(db)
    if operation == "generate" and (await repository.counts(db))["servers"]:
        raise Conflict(
            "Simulated data already exists. Use regenerate to replace it, or remove it first.",
            code="simulation_present",
        )
    run = SimulationRun(
        operation=operation,
        profile=prof.key,
        seed=seed if seed is not None else int(datetime.now(UTC).timestamp()) % 100_000,
        status="pending",
        progress=0,
        phase="Queued",
        requested_by_id=actor.user.id if actor else None,
        requested_by=actor.username if actor else "cli",
        request_id=request_id_ctx.get(),
        stats={},
    )
    db.add(run)
    await db.flush()
    await enqueue(None, JOB_KIND, trigger="manual", db=db)
    await audit.record(
        f"simulation.{operation}_requested",
        actor=actor,
        username=None if actor else "cli",
        result="accepted",
        category="admin",
        target_type="simulation",
        target_id=str(run.id),
        details={"profile": prof.key, "seed": run.seed, "servers_planned": prof.servers},
    )
    return run


async def remove(db: AsyncSession, actor: Principal | None) -> dict[str, Any]:
    """Delete all simulated data now. Real servers, users, RBAC and settings are untouched."""
    await _ensure_idle(db)
    started = datetime.now(UTC)
    try:
        result = await repository.delete_simulated(db)
    except Exception as exc:
        await audit.record(
            "simulation.removed",
            actor=actor,
            username=None if actor else "cli",
            result="failure",
            failure_reason=f"{type(exc).__name__}: {exc}",
            target_type="simulation",
        )
        raise
    db.add(
        SimulationRun(
            operation="remove",
            status="completed",
            progress=100,
            phase="Removed",
            requested_by_id=actor.user.id if actor else None,
            requested_by=actor.username if actor else "cli",
            request_id=request_id_ctx.get(),
            stats={k: v for k, v in result.items() if k != "kept_groupings"},
            created_at=started,
            started_at=started,
            finished_at=datetime.now(UTC),
        )
    )
    await db.flush()
    await audit.record(
        "simulation.removed",
        actor=actor,
        username=None if actor else "cli",
        result="success",
        risk_class="DESTRUCTIVE",
        target_type="simulation",
        details=result,
    )
    log.info("simulated data removed", extra={"result": result})
    return result


# ---------------------------------------------------------------------------------------
# worker side
# ---------------------------------------------------------------------------------------
async def _progress(run_id: uuid.UUID, progress: int, phase: str, **values: Any) -> None:
    async with session_scope() as db:
        await db.execute(
            update(SimulationRun)
            .where(SimulationRun.id == run_id)
            .values(progress=max(0, min(progress, 100)), phase=phase[:128], **values)
        )


async def execute_pending() -> dict[str, Any]:
    """Run the oldest pending generate/regenerate run (called by the job worker or the CLI)."""
    async with session_scope() as db:
        run = (
            await db.execute(
                select(SimulationRun)
                .where(SimulationRun.status == "pending")
                .order_by(SimulationRun.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if run is None:
            return {"skipped": "no pending simulation run"}
        run.status, run.started_at, run.phase = "running", datetime.now(UTC), "Starting"
        run_id, operation, profile_key, seed, actor = run.id, run.operation, run.profile, run.seed, run.requested_by
        request_id = run.request_id
    stats: dict[str, Any] = {}
    try:
        if operation == "regenerate":
            await _progress(run_id, 2, "Removing the previous simulated fleet")
            async with session_scope() as db:
                removed = await repository.delete_simulated(db)
            stats["removed"] = {k: v for k, v in removed.items() if k != "kept_groupings"}
        stats.update(await generate(run_id, get_profile(profile_key or "small"), int(seed or 0)))
        await _progress(run_id, 100, "Completed", status="completed", finished_at=datetime.now(UTC), stats=stats)
        await audit.record(
            "simulation.regenerated" if operation == "regenerate" else "simulation.generated",
            username=actor,
            result="success",
            target_type="simulation",
            target_id=str(run_id),
            details={"profile": profile_key, "seed": seed, "request_id": request_id, **stats},
        )
        return {"run_id": str(run_id), **stats}
    except Exception as exc:
        log.exception("simulation run failed", extra={"run_id": str(run_id)})
        await _progress(
            run_id,
            100,
            "Failed",
            status="failed",
            finished_at=datetime.now(UTC),
            error=f"{type(exc).__name__}: {exc}"[:2000],
            stats=stats,
        )
        await audit.record(
            "simulation.regenerated" if operation == "regenerate" else "simulation.generated",
            username=actor,
            result="failure",
            failure_reason=f"{type(exc).__name__}: {exc}",
            target_type="simulation",
            target_id=str(run_id),
            details={"profile": profile_key, "seed": seed, "request_id": request_id},
        )
        raise


async def _unique_name(db: AsyncSession, model: Any, name: str, suffix: str) -> str:
    """Never collide with (or reuse) a real object of the same name."""
    candidate = name
    n = 1
    while (await db.execute(select(model.id).where(model.name == candidate))).first() is not None:
        n += 1
        candidate = f"{name}{suffix}" if n == 2 else f"{name}{suffix}{n}"
    return candidate


async def _persist_plan(
    run_id: uuid.UUID, plan: FleetPlan, profile: SimulationProfile, now: datetime
) -> list[uuid.UUID]:
    regression_since = (now - timedelta(hours=18)).isoformat()
    async with session_scope() as db:
        org = await default_org(db)
        await ensure_environments(db, org)
        await db.flush()
        envs = {e.name: e for e in (await db.execute(select(Environment))).scalars()}
        site_ids: dict[str, uuid.UUID] = {}
        for name, provider, region in plan.sites:
            obj = Site(
                organization_id=org.id,
                name=await _unique_name(db, Site, name, " (simulated)"),
                provider=provider,
                region=region,
                data_source=SIM,
            )
            db.add(obj)
            await db.flush()
            site_ids[name] = obj.id
        cluster_ids: dict[str, uuid.UUID] = {}
        for name, kind in plan.clusters:
            obj = Cluster(
                organization_id=org.id,
                name=await _unique_name(db, Cluster, name, "-sim"),
                kind=kind,
                description="Simulated cluster",
                data_source=SIM,
            )
            db.add(obj)
            await db.flush()
            cluster_ids[name] = obj.id
        app_ids: dict[str, uuid.UUID] = {}
        for name, service, owner in plan.applications:
            obj = Application(
                organization_id=org.id,
                name=await _unique_name(db, Application, name, " (simulated)"),
                business_service=service,
                owner=owner,
                data_source=SIM,
            )
            db.add(obj)
            await db.flush()
            app_ids[name] = obj.id

        ids: list[uuid.UUID] = []
        for p in plan.servers:
            env = envs.get(p.environment)
            s = Server(
                organization_id=org.id,
                name=await _unique_name(db, Server, p.name, "-SIM"),
                engine=p.engine,
                host=p.host,
                port=p.port,
                description=f"Simulated {p.application} database. {p.story}.",
                environment_id=env.id if env else None,
                site_id=site_ids.get(p.site),
                cluster_id=cluster_ids.get(p.cluster) if p.cluster else None,
                cluster_role=p.cluster_role,
                application_id=app_ids.get(p.application),
                owner=p.owner,
                business_service=p.business_service,
                deployment=p.deployment,
                cloud_provider=p.cloud_provider,
                tls_mode=p.tls_mode,
                data_source=SIM,
                collection_interval_s=profile.collection_interval_s,
                licensed_features=list(p.licensed_features),
                options={
                    "demo_profile": demo_profile(p, plan.seed, regression_since),
                    "simulation": {
                        "run_id": str(run_id),
                        "profile": profile.key,
                        "seed": plan.seed,
                        "condition": p.condition,
                        "story": p.story,
                    },
                },
            )
            tier = env.tier if env else "unknown"
            s.tags = [
                ServerTag(key="tier", value=tier),
                ServerTag(key="team", value=p.owner),
                ServerTag(key="application", value=p.application),
            ]
            db.add(s)
            await db.flush()
            ids.append(s.id)
    return ids


async def generate(run_id: uuid.UUID, profile: SimulationProfile, seed: int) -> dict[str, Any]:
    """Create the simulated estate for ``profile``/``seed`` and its history. Returns statistics."""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    plan = plan_fleet(profile, seed)
    await _progress(run_id, 4, f"Creating {len(plan.servers)} simulated servers")
    ids = await _persist_plan(run_id, plan, profile, now)

    metric_rows = query_rows = 0
    reachable: list[uuid.UUID] = []
    for i, sid in enumerate(ids):
        async with session_scope() as db:
            server = await db.get(Server, sid)
            assert server is not None and server.data_source == SIM  # never touch a real server
            adapter = await adapter_for(db, server)
            assert isinstance(adapter, DemoAdapter)  # simulated servers never get a network adapter
            down = "down" in adapter.scenarios
            if not down:
                await collection.discover(db, server)
                metric_rows += await metric_gen.backfill_metrics(db, server, adapter, profile, now)
                query_rows += await query_gen.backfill_query_stats(
                    db, server, adapter, profile.query_history_hours, now
                )
                reachable.append(sid)
            else:
                # history up to the outage, then nothing but "unavailable"
                metric_rows += await metric_gen.backfill_metrics(db, server, adapter, profile, now - OUTAGE)
                try:
                    await collection.discover(db, server)
                except AdapterConnectionError as exc:
                    await collection.record_unavailable(db, server, str(exc))
                    metric_rows += await metric_gen.bulk_write(metric_gen.outage_samples(server, now - OUTAGE, now))
        await _progress(run_id, 5 + int(75 * (i + 1) / len(ids)), f"Generating history ({i + 1}/{len(ids)} servers)")

    # make the bulk-loaded history visible to the recommendation engine's queries
    await get_writer().flush_all()
    await get_repository().flush()
    for i, sid in enumerate(reachable):
        async with session_scope() as db:
            server = await db.get(Server, sid)
            assert server is not None and server.data_source == SIM
            await rec_gen.generate(db, server)
            await sec_gen.generate(db, server)
        await _progress(
            run_id, 80 + int(19 * (i + 1) / len(reachable)), f"Evaluating advice ({i + 1}/{len(reachable)})"
        )

    async with session_scope() as db:
        c = await repository.counts(db)
    stats = {
        "profile": profile.key,
        "seed": seed,
        "servers_created": len(ids),
        "databases_created": c["databases"],
        "metrics_created": metric_rows,
        "metric_samples_written": metric_rows,
        "queries_created": query_rows,
        "query_samples_written": query_rows,
        "findings_created": c["security_findings"],
        "recommendations_created": c["recommendations"],
        "clusters_created": c["clusters"],
        "sites_created": c["sites"],
        "applications_created": c["applications"],
        "plan": plan.summary(),
    }
    log.info("simulated fleet generated", extra={"stats": stats})
    return stats


async def cleanup_report(db: AsyncSession) -> dict[str, Any]:
    """What a removal would delete (for the CLI dry run and the UI confirmation)."""
    c = await repository.counts(db)
    groupings = await repository.removable_groupings(db)
    return {
        "counts": c,
        "groupings": groupings,
        "legacy_demo_accounts": await repository.legacy_demo_accounts(db),
    }
