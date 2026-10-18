"""Background scheduler and worker pool.

* **Scheduler** — exactly one process at a time holds a PostgreSQL advisory lock and
  enqueues due jobs into ``collector_jobs`` (deduplicated by a partial unique index).
* **Workers** — any number of processes claim jobs with ``FOR UPDATE SKIP LOCKED`` and run
  them under a global concurrency semaphore. Each job uses short-lived target connections.

Running with ``DBM_PROCESS_ROLE=worker`` on separate pods scales collection independently
of the API.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import AdapterConnectionError, AdapterError, AdapterNotSupported, AdapterPermissionError
from app.core.config import get_settings
from app.core.metrics import COLLECTOR_DURATION, COLLECTOR_INFLIGHT, COLLECTOR_JOBS, SCHEDULER_LEADER
from app.db.session import get_engine, session_scope
from app.jobs.handlers import GLOBAL_JOBS, JOB_KINDS, interval_for, jitter_for
from app.models import CollectorJob, Server
from app.services import collection

log = logging.getLogger("app.jobs")
LEADER_LOCK_ID = 0x44424D31  # "DBM1"
#: the simulation generator is the only long-running global job
LONG_JOB = "simulation"
LONG_JOB_LIMIT = timedelta(hours=3)
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


async def enqueue(
    server_id: uuid.UUID | None,
    kind: str,
    trigger: str = "manual",
    delay_s: float = 0,
    db: AsyncSession | None = None,
) -> bool:
    """Insert a pending job unless one is already pending/running for (server, kind).

    Pass the caller's session to enqueue inside its transaction (e.g. right after creating
    the server row); otherwise a separate unit of work is used.
    """
    stmt = (
        pg_insert(CollectorJob)
        .values(
            id=uuid.uuid4(),
            server_id=server_id,
            kind=kind,
            status="pending",
            trigger=trigger,
            run_after=datetime.now(UTC) + timedelta(seconds=delay_s),
            created_at=datetime.now(UTC),
            attempts=0,
        )
        .on_conflict_do_nothing(
            index_elements=["server_id", "kind"], index_where=text("status IN ('pending','running')")
        )
    )
    if db is not None:
        return bool((await db.execute(stmt)).rowcount)
    async with session_scope() as s:
        return bool((await s.execute(stmt)).rowcount)


async def schedule_due() -> int:
    """Enqueue every job whose interval has elapsed. Returns number enqueued."""
    now = datetime.now(UTC)
    n = 0
    async with session_scope() as db:
        # recover jobs stuck in 'running' after a worker crash (simulation runs may take longer)
        await db.execute(
            update(CollectorJob)
            .where(
                CollectorJob.status == "running",
                or_(
                    and_(CollectorJob.kind != LONG_JOB, CollectorJob.started_at < now - timedelta(minutes=10)),
                    and_(CollectorJob.kind == LONG_JOB, CollectorJob.started_at < now - LONG_JOB_LIMIT),
                ),
            )
            .values(status="failed", last_error="worker lost (timed out)", finished_at=now)
        )
        servers = (
            (
                await db.execute(
                    select(Server).where(
                        Server.monitoring_enabled.is_(True),
                        or_(Server.circuit_open_until.is_(None), Server.circuit_open_until < now),
                    )
                )
            )
            .scalars()
            .all()
        )
        last = {
            (sid, kind): ts
            for sid, kind, ts in (
                await db.execute(
                    select(CollectorJob.server_id, CollectorJob.kind, func.max(CollectorJob.created_at)).group_by(
                        CollectorJob.server_id, CollectorJob.kind
                    )
                )
            ).all()
        }
        rows = []
        for s in servers:
            for kind in JOB_KINDS:
                prev = last.get((s.id, kind))
                iv = interval_for(kind, s)
                if prev is None or (now - prev).total_seconds() >= iv:
                    rows.append(
                        {
                            "id": uuid.uuid4(),
                            "server_id": s.id,
                            "kind": kind,
                            "status": "pending",
                            "trigger": "schedule",
                            # stable phase offset: spreads a large fleet over the interval
                            "run_after": now + timedelta(seconds=jitter_for(kind, s.id, iv)),
                            "created_at": now,
                            "attempts": 0,
                        }
                    )
        for kind, iv in GLOBAL_JOBS.items():
            prev = last.get((None, kind))
            if prev is None or (now - prev).total_seconds() >= iv:
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "server_id": None,
                        "kind": kind,
                        "status": "pending",
                        "trigger": "schedule",
                        "run_after": now,
                        "created_at": now,
                        "attempts": 0,
                    }
                )
        if rows:
            stmt = (
                pg_insert(CollectorJob)
                .values(rows)
                .on_conflict_do_nothing(
                    index_elements=["server_id", "kind"], index_where=text("status IN ('pending','running')")
                )
            )
            n = (await db.execute(stmt)).rowcount or 0
    return n


async def claim(batch: int) -> list[uuid.UUID]:
    async with session_scope() as db:
        sub = (
            select(CollectorJob.id)
            .where(CollectorJob.status == "pending", CollectorJob.run_after <= func.now())
            .order_by(CollectorJob.run_after)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
        ids = (
            (
                await db.execute(
                    update(CollectorJob)
                    .where(CollectorJob.id.in_(sub))
                    .values(
                        status="running", locked_by=WORKER_ID, started_at=func.now(), attempts=CollectorJob.attempts + 1
                    )
                    .returning(CollectorJob.id)
                )
            )
            .scalars()
            .all()
        )
    return list(ids)


async def _job_kind(job_id: uuid.UUID) -> str | None:
    async with session_scope() as db:
        return (await db.execute(select(CollectorJob.kind).where(CollectorJob.id == job_id))).scalar_one_or_none()


async def run_simulation_job(job_id: uuid.UUID) -> None:
    """Run a simulation generate/regenerate outside a long-lived job transaction."""
    from app.simulation import service as simulation

    start = time.perf_counter()
    status, error, result = "done", None, None
    try:
        result = await simulation.execute_pending()
        if result.get("skipped"):
            status = "skipped"
    except Exception as exc:  # noqa: BLE001 - recorded on the job row and the simulation run
        status, error = "failed", f"{type(exc).__name__}: {exc}"
    elapsed = (time.perf_counter() - start) * 1000
    async with session_scope() as db:
        await db.execute(
            update(CollectorJob)
            .where(CollectorJob.id == job_id)
            .values(
                status=status,
                last_error=(error or None) and error[:4000],
                result={k: v for k, v in (result or {}).items() if k != "plan"} or None,
                finished_at=datetime.now(UTC),
                duration_ms=round(elapsed, 2),
            )
        )
    COLLECTOR_JOBS.labels(LONG_JOB, status).inc()
    COLLECTOR_DURATION.labels(LONG_JOB).observe(elapsed / 1000)


async def run_job(job_id: uuid.UUID) -> None:
    if await _job_kind(job_id) == LONG_JOB:
        await run_simulation_job(job_id)
        return
    start = time.perf_counter()
    status, error, result = "done", None, None
    kind = "?"
    async with session_scope() as db:
        job = await db.get(CollectorJob, job_id)
        if job is None:
            return
        kind = job.kind
        try:
            if job.server_id is None:
                if job.kind == "retention":
                    result = await collection.retention(db)
                elif job.kind == "timeseries_deletes":
                    from app.services import monitoring_storage

                    result = {"deleted": await monitoring_storage.retry_pending_deletes(db)}
                elif job.kind == "evaluate_alerts":
                    from app.services import alerting

                    result = await alerting.evaluate(db)
                elif job.kind == "notification_retries":
                    from app.services import alerting

                    result = await alerting.retry_due(db)
                else:
                    status, error = "skipped", f"unknown global job {job.kind}"
            else:
                server = await db.get(Server, job.server_id)
                if server is None or job.kind not in JOB_KINDS:
                    status, error = "skipped", "server removed or unknown job kind"
                else:
                    handler, _ = JOB_KINDS[job.kind]
                    try:
                        async with db.begin_nested():
                            result = await handler(db, server)
                    except AdapterConnectionError as exc:
                        await db.refresh(server)
                        await collection.record_unavailable(db, server, str(exc))
                        collection.set_component(server, job.kind, "unavailable", str(exc))
                        raise
                    except (AdapterPermissionError, AdapterNotSupported) as exc:
                        # the server is reachable: only this component is limited
                        await db.refresh(server)
                        status_ = (
                            "insufficient_privilege"
                            if isinstance(exc, AdapterPermissionError)
                            else collection.classify_reason(exc.reason)
                        )
                        collection.set_component(server, job.kind, status_, str(exc))
                        raise
                    except AdapterError as exc:
                        await db.refresh(server)
                        collection.set_component(server, job.kind, "failed", str(exc))
                        raise
                    if result and result.get("skipped"):
                        status = "skipped"
        except AdapterConnectionError as exc:
            status, error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - recorded on the job row
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            log.warning("job failed", extra={"job_id": str(job_id), "kind": kind, "error": error})
        elapsed = (time.perf_counter() - start) * 1000
        job.status, job.last_error, job.result = status, (error or None) and error[:4000], result
        job.finished_at, job.duration_ms = datetime.now(UTC), round(elapsed, 2)
    COLLECTOR_JOBS.labels(kind, status).inc()
    COLLECTOR_DURATION.labels(kind).observe(elapsed / 1000)


class BackgroundRunner:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []
        self._stop = asyncio.Event()
        s = get_settings()
        self._sem = asyncio.Semaphore(s.collector_concurrency)
        self.is_leader = False
        self.started_at: datetime | None = None
        self.last_tick: datetime | None = None

    def start(self) -> None:
        self.started_at = datetime.now(UTC)
        self._tasks = [
            asyncio.create_task(self._scheduler_loop(), name="dbm-scheduler"),
            asyncio.create_task(self._worker_loop(), name="dbm-worker"),
        ]

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _scheduler_loop(self) -> None:
        s = get_settings()
        engine = get_engine()
        lock_conn = None
        while not self._stop.is_set():
            try:
                if lock_conn is None:
                    lock_conn = await engine.connect()
                if not self.is_leader:
                    got = (
                        await lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LEADER_LOCK_ID})
                    ).scalar()
                    await lock_conn.commit()
                    self.is_leader = bool(got)
                    SCHEDULER_LEADER.set(1 if self.is_leader else 0)
                if self.is_leader:
                    await schedule_due()
                    self.last_tick = datetime.now(UTC)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
                self.is_leader = False
                if lock_conn is not None:
                    try:
                        await lock_conn.close()
                    except Exception:  # noqa: BLE001, S110 - connection already broken
                        pass
                    lock_conn = None
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=s.scheduler_tick_seconds)
            except TimeoutError:
                pass
        if lock_conn is not None:
            await lock_conn.close()

    async def _worker_loop(self) -> None:
        s = get_settings()
        inflight: set[asyncio.Task[None]] = set()
        while not self._stop.is_set():
            try:
                free = s.collector_concurrency - len(inflight)
                if free > 0:
                    for jid in await claim(min(free, s.collector_batch_size)):
                        t = asyncio.create_task(self._run(jid))
                        inflight.add(t)
                        t.add_done_callback(inflight.discard)
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                log.exception("worker poll failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=s.worker_poll_seconds)
            except TimeoutError:
                pass
        for t in inflight:
            t.cancel()

    async def _run(self, job_id: uuid.UUID) -> None:
        async with self._sem:
            COLLECTOR_INFLIGHT.inc()
            try:
                long_job = await _job_kind(job_id) == LONG_JOB
                timeout = (
                    LONG_JOB_LIMIT.total_seconds() if long_job else get_settings().target_statement_timeout_seconds * 4
                )
                await asyncio.wait_for(run_job(job_id), timeout=timeout)
            except TimeoutError:
                async with session_scope() as db:
                    await db.execute(
                        update(CollectorJob)
                        .where(and_(CollectorJob.id == job_id))
                        .values(status="failed", last_error="job timed out", finished_at=datetime.now(UTC))
                    )
            finally:
                COLLECTOR_INFLIGHT.dec()


runner: BackgroundRunner | None = None
