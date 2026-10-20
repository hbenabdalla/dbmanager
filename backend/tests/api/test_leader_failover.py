"""Scheduler leader failover between real processes (sprint 0.15.4).

Two worker processes compete for the scheduler leadership through the PostgreSQL advisory lock
and report themselves in ``platform_instances``. Exactly one leads; when the leader is killed
(SIGKILL: no clean shutdown, its database session just disappears) the other one takes over
within a few scheduler ticks. Collection itself is disabled in the processes, so the shared
test database is not touched beyond the registry."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.asyncio(loop_scope="session")

PROCESS = """
import asyncio, os
from app.jobs import runner as job_runner
from app.services import instances

async def noop(*a, **k):
    return None

job_runner.schedule_due = noop  # leadership only: no collector jobs are created

async def main():
    job_runner.runner = job_runner.BackgroundRunner()
    hb = instances.Heartbeat()
    hb.start()
    await job_runner.runner._scheduler_loop()

asyncio.run(main())
"""


def _spawn(name: str) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "DBM_PROCESS_ROLE": "worker",
        "DBM_SCHEDULER_TICK_SECONDS": "1",
        "DBM_INSTANCE_HEARTBEAT_SECONDS": "5",
        "DBM_RUN_MIGRATIONS_ON_START": "false",
        "DBM_LOG_LEVEL": "WARNING",
        "HOSTNAME": name,
    }
    return subprocess.Popen(  # noqa: S603 - our own interpreter and inline script
        [sys.executable, "-c", PROCESS], cwd=Path(__file__).resolve().parents[2], env=env
    )


async def _leaders() -> dict[str, bool]:
    from app.db.session import session_scope
    from app.models import PlatformInstance

    async with session_scope() as db:
        rows = (await db.execute(select(PlatformInstance.id, PlatformInstance.is_leader))).all()
    return {i: lead for i, lead in rows if not i.startswith(("api-", "worker-dead", "ancient"))}


async def _wait(cond, within: float = 30.0):  # type: ignore[no-untyped-def]
    end = time.monotonic() + within
    while time.monotonic() < end:
        got = await cond()
        if got:
            return got
        await asyncio.sleep(0.5)
    raise AssertionError("condition not reached in time")


async def test_leadership_moves_when_the_leader_process_dies(app) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import session_scope
    from app.models import PlatformInstance

    async with session_scope() as db:
        await db.execute(PlatformInstance.__table__.delete())
    procs = {p.pid: p for p in (_spawn("w1"), _spawn("w2"))}
    try:

        async def one_leader():  # type: ignore[no-untyped-def]
            rows = await _leaders()
            ours = {k: v for k, v in rows.items() if int(k.rsplit(":", 1)[1]) in procs}
            return ours if len(ours) == 2 and sum(ours.values()) == 1 else None

        state = await _wait(one_leader, 60)
        leader_pid = next(int(k.rsplit(":", 1)[1]) for k, v in state.items() if v)
        procs[leader_pid].send_signal(signal.SIGKILL)
        procs[leader_pid].wait(10)
        survivor = next(p for pid, p in procs.items() if pid != leader_pid)

        async def survivor_leads():  # type: ignore[no-untyped-def]
            rows = await _leaders()
            return any(v for k, v in rows.items() if k.endswith(f":{survivor.pid}"))

        await _wait(survivor_leads, 30)
        assert survivor.poll() is None
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()
        async with session_scope() as db:
            await db.execute(PlatformInstance.__table__.delete())
