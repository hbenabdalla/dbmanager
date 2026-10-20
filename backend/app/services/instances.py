"""Registry of the running DBManager processes (sprint 0.15.4: HA deployments).

Each process — the API, a worker, or both (``DBM_PROCESS_ROLE``) — upserts its row in
``platform_instances`` every ``DBM_INSTANCE_HEARTBEAT_SECONDS``. Any API replica can then answer
"what is running?": the instances, their roles and versions (a rolling upgrade shows both), and
which worker holds the scheduler leadership. A clean stop deletes the row; a crashed process
stays visible as *stale* (no heartbeat for three intervals) until it is pruned after a day."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.core.config import get_settings
from app.db.session import session_scope
from app.models import PlatformInstance

log = logging.getLogger("app.instances")
PRUNE_AFTER = timedelta(days=1)


def instance_id() -> str:
    from app.jobs.runner import WORKER_ID

    return WORKER_ID


def _is_leader() -> bool:
    from app.jobs import runner as job_runner

    return bool(job_runner.runner and job_runner.runner.is_leader)


async def beat(db: AsyncSession, *, now: datetime | None = None) -> None:
    """Upsert this process's row and prune rows of processes gone for more than a day."""
    s = get_settings()
    now = now or datetime.now(UTC)
    values: dict[str, Any] = {
        "id": instance_id(),
        "role": s.process_role,
        "hostname": socket.gethostname()[:128],
        "version": __version__,
        "git_commit": (s.git_commit or None) and s.git_commit[:40],
        "runs_worker": s.runs_worker,
        "is_leader": _is_leader(),
        "last_seen_at": now,
    }
    stmt = insert(PlatformInstance).values(started_at=now, **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[PlatformInstance.id],
        set_={k: stmt.excluded[k] for k in values if k != "id"},
    )
    await db.execute(stmt)
    await db.execute(delete(PlatformInstance).where(PlatformInstance.last_seen_at < now - PRUNE_AFTER))


async def leave(db: AsyncSession) -> None:
    await db.execute(delete(PlatformInstance).where(PlatformInstance.id == instance_id()))


def stale_after() -> timedelta:
    return timedelta(seconds=3 * get_settings().instance_heartbeat_seconds)


async def list_instances(db: AsyncSession, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    rows = list(
        (await db.execute(select(PlatformInstance).order_by(PlatformInstance.role, PlatformInstance.id))).scalars()
    )
    limit = stale_after()
    items = []
    for r in rows:
        active = r.last_seen_at >= now - limit
        items.append(
            {
                "id": r.id,
                "role": r.role,
                "hostname": r.hostname,
                "version": r.version,
                "git_commit": r.git_commit,
                "runs_worker": r.runs_worker,
                # a stale leader no longer holds the lock (its session ended with the process)
                "is_leader": r.is_leader and active,
                "started_at": r.started_at,
                "last_seen_at": r.last_seen_at,
                "status": "active" if active else "stale",
                "this_instance": r.id == instance_id(),
            }
        )
    active_items = [i for i in items if i["status"] == "active"]
    leaders = [i["id"] for i in active_items if i["is_leader"]]
    versions = sorted({str(i["version"]) for i in active_items})
    return {
        "instances": items,
        "summary": {
            "api": sum(1 for i in active_items if i["role"] in ("api", "all")),
            "workers": sum(1 for i in active_items if i["runs_worker"]),
            "stale": sum(1 for i in items if i["status"] == "stale"),
            "leader": leaders[0] if leaders else None,
            "versions": versions,
            "mixed_versions": len(versions) > 1,
        },
        "heartbeat_seconds": get_settings().instance_heartbeat_seconds,
    }


class Heartbeat:
    """Background task of one process: report now, then every interval; leave on stop."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def _once(self) -> None:
        try:
            async with session_scope() as db:
                await beat(db)
        except Exception as exc:  # noqa: BLE001 - the registry is informational; never kill the process
            log.warning("instance heartbeat failed: %s", type(exc).__name__)

    async def _loop(self) -> None:
        interval = get_settings().instance_heartbeat_seconds
        while not self._stop.is_set():
            await self._once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=interval)

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="instance-heartbeat")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            with contextlib.suppress(Exception):
                await self._task
        try:
            async with session_scope() as db:
                await leave(db)
        except Exception:  # noqa: BLE001, S110 - best effort on shutdown
            pass


heartbeat: Heartbeat | None = None
