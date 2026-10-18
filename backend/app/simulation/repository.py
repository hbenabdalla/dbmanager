"""Counting and deleting simulated data.

Only rows marked ``data_source = 'simulated'`` (and rows owned by such servers) are ever
selected here, and only time-series carrying the label ``data_source="simulated"`` are
deleted from the time-series database. Nothing is identified by name.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Application,
    Cluster,
    DatabaseEntry,
    DataSource,
    MetricSample,
    QueryFingerprint,
    QueryStatSnapshot,
    Recommendation,
    RoleAssignment,
    SecurityFinding,
    Server,
    Site,
    User,
)

SIM = DataSource.SIMULATED
GROUPINGS: tuple[tuple[type[Any], Any], ...] = (
    (Site, Server.site_id),
    (Cluster, Server.cluster_id),
    (Application, Server.application_id),
)
# Accounts the pre-simulation DEMO MODE created. They carry no marker, so they are only ever
# *reported* (never deleted automatically); an administrator decides what to do with them.
LEGACY_DEMO_USERNAMES = ("monitor", "dba.dev", "perf.oracle", "db2.dba", "auditor")


async def simulated_server_ids(db: AsyncSession) -> list[uuid.UUID]:
    return list((await db.execute(select(Server.id).where(Server.data_source == SIM))).scalars())


async def _count(db: AsyncSession, stmt: Any) -> int:
    return int((await db.execute(stmt)).scalar_one() or 0)


async def counts(db: AsyncSession) -> dict[str, int]:
    sim = select(Server.id).where(Server.data_source == SIM).scalar_subquery()

    def owned(model: Any) -> Any:
        return select(func.count()).select_from(model).where(model.server_id.in_(sim))

    out = {
        "servers": await _count(db, select(func.count()).select_from(Server).where(Server.data_source == SIM)),
        "real_servers": await _count(db, select(func.count()).select_from(Server).where(Server.data_source != SIM)),
        "databases": await _count(db, owned(DatabaseEntry)),
        "metrics": await _count(db, owned(MetricSample)),
        "queries": await _count(db, owned(QueryStatSnapshot)),
        "query_fingerprints": await _count(db, owned(QueryFingerprint)),
        "telemetry_samples": await _telemetry_samples(db),
        "security_findings": await _count(db, owned(SecurityFinding)),
        "recommendations": await _count(db, owned(Recommendation)),
    }
    for model, _ in GROUPINGS:
        out[model.__tablename__] = await _count(
            db, select(func.count()).select_from(model).where(model.data_source == SIM)
        )
    return out


async def _telemetry_samples(db: AsyncSession) -> int:
    """Samples the current simulated fleet wrote to the time-series database (recorded by the
    generation run — counting series in the TSDB would be far more expensive)."""
    from app.models import SimulationRun

    if not await _count(db, select(func.count()).select_from(Server).where(Server.data_source == SIM)):
        return 0
    run = (
        await db.execute(
            select(SimulationRun)
            .where(SimulationRun.operation.in_(("generate", "regenerate")), SimulationRun.status == "completed")
            .order_by(SimulationRun.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run is None:
        return 0
    st = run.stats or {}
    return int(st.get("metric_samples_written", 0)) + int(st.get("query_samples_written", 0))


async def removable_groupings(db: AsyncSession) -> dict[str, dict[str, list[str]]]:
    """Simulated sites/clusters/applications split into removable and kept.

    A simulated grouping that a real server has been attached to is kept (and reported), so
    removing the simulation never changes a real server.
    """
    out: dict[str, dict[str, list[str]]] = {}
    for model, fk in GROUPINGS:
        rows = (await db.execute(select(model).where(model.data_source == SIM))).scalars().all()
        used_by_real = set((await db.execute(select(fk).where(Server.data_source != SIM, fk.is_not(None)))).scalars())
        out[model.__tablename__] = {
            "remove": sorted(r.name for r in rows if r.id not in used_by_real),
            "keep": sorted(r.name for r in rows if r.id in used_by_real),
        }
    return out


async def scoped_role_assignments(db: AsyncSession, server_ids: list[uuid.UUID]) -> Any:
    """Role assignments whose scope points at a simulated server, cluster or site."""
    sids = [str(i) for i in server_ids]
    sim_clusters = [str(i) for i in (await db.execute(select(Cluster.id).where(Cluster.data_source == SIM))).scalars()]
    sim_sites = [str(i) for i in (await db.execute(select(Site.id).where(Site.data_source == SIM))).scalars()]
    conds = []
    if sids:
        conds.append(and_(RoleAssignment.scope_type == "server", RoleAssignment.scope_id.in_(sids)))
        prefixes = [RoleAssignment.scope_id.startswith(f"{sid}/") for sid in sids]
        conds.append(and_(RoleAssignment.scope_type.in_(["database", "schema"]), or_(*prefixes)))
    if sim_clusters:
        conds.append(and_(RoleAssignment.scope_type == "cluster", RoleAssignment.scope_id.in_(sim_clusters)))
    if sim_sites:
        conds.append(and_(RoleAssignment.scope_type == "site", RoleAssignment.scope_id.in_(sim_sites)))
    return or_(*conds) if conds else None


async def legacy_demo_accounts(db: AsyncSession) -> list[str]:
    rows = await db.execute(select(User.username).where(User.username.in_(LEGACY_DEMO_USERNAMES)))
    return sorted(rows.scalars())


async def delete_simulated(db: AsyncSession) -> dict[str, Any]:
    """Delete every simulated server (and what it owns) and unused simulated groupings."""
    before = await counts(db)
    ids = await simulated_server_ids(db)
    groupings = await removable_groupings(db)
    ra_cond = await scoped_role_assignments(db, ids)
    removed_assignments = 0
    if ra_cond is not None:
        removed_assignments = (await db.execute(delete(RoleAssignment).where(ra_cond))).rowcount or 0
    from app.services import monitoring_storage

    # time-series telemetry: one delete by the stable classification label. When the TSDB is
    # down the request is queued and retried by the retention job (never by name).
    # follow-up by the removed servers' ids only: a new simulated fleet generated right after
    # this removal (regenerate) must not lose its telemetry
    by_id: dict[str, Any] = {"server_id": [str(i) for i in ids]} if ids else {}
    ts_deleted = await monitoring_storage.delete_series(db, {"data_source": SIM}, follow_up=by_id, deferred=by_id)
    if ids:
        # The largest tables are deleted explicitly (same effect as the FK cascade, but in
        # set-based statements); the server delete cascades to everything else it owns.
        await db.execute(delete(MetricSample).where(MetricSample.server_id.in_(ids)))
        await db.execute(delete(QueryStatSnapshot).where(QueryStatSnapshot.server_id.in_(ids)))
        await db.execute(delete(Server).where(Server.id.in_(ids), Server.data_source == SIM))
    for model, fk in GROUPINGS:
        await db.execute(
            delete(model).where(
                model.data_source == SIM,
                ~exists().where(fk == model.id),  # nothing (real) still references it
            )
        )
    await db.flush()
    return {
        "servers_deleted": before["servers"],
        "databases_deleted": before["databases"],
        "metrics_deleted": before["metrics"],
        "telemetry_samples_deleted": before["telemetry_samples"],
        "timeseries_delete": "done" if ts_deleted else "queued (time-series database unavailable)",
        "queries_deleted": before["queries"],
        "security_findings_deleted": before["security_findings"],
        "recommendations_deleted": before["recommendations"],
        "sites_deleted": len(groupings["sites"]["remove"]),
        "clusters_deleted": len(groupings["clusters"]["remove"]),
        "applications_deleted": len(groupings["applications"]["remove"]),
        "role_assignments_deleted": removed_assignments,
        "kept_groupings": {k: v["keep"] for k, v in groupings.items() if v["keep"]},
    }
