"""Runtime guards that need the database: scheduler leader election (one leader at a time,
leadership moves when the leader's session is gone), the idle session timeout, and servers
with an open circuit are not scheduled."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_only_one_scheduler_is_leader_and_leadership_moves(app) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import get_engine
    from app.jobs.runner import BackgroundRunner

    engine = get_engine()
    a, b = BackgroundRunner(), BackgroundRunner()
    ca, cb = await engine.connect(), await engine.connect()
    try:
        assert await a.leader_tick(ca) is True
        assert await b.leader_tick(cb) is False
        # the leader confirms its leadership every tick without stacking lock counts
        for _ in range(3):
            assert await a.leader_tick(ca) is True
        assert await b.leader_tick(cb) is False
        # the leader's session ends: the other instance takes over on its next tick
        await ca.invalidate()
        assert await b.leader_tick(cb) is True
        ca = await engine.connect()
        assert await a.leader_tick(ca) is False
    finally:
        await ca.close()
        await cb.close()


async def test_idle_session_expires_and_is_removed(make_client) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings
    from app.db.session import session_scope
    from app.models import UserSession
    from tests.api.conftest import ADMIN

    c = await make_client(ADMIN)
    assert (await c.get("/api/v1/auth/me")).status_code == 200
    idle = get_settings().session_idle_timeout_minutes
    async with session_scope() as db:
        sessions = list((await db.execute(select(UserSession))).scalars())
        newest = max(sessions, key=lambda x: x.created_at)
        token_hash = newest.token_hash
        await db.execute(
            update(UserSession)
            .where(UserSession.token_hash == token_hash)
            .values(last_seen_at=datetime.now(UTC) - timedelta(minutes=idle + 1))
        )
    assert (await c.get("/api/v1/auth/me")).status_code == 401
    async with session_scope() as db:
        assert await db.get(UserSession, token_hash) is None


async def test_open_circuit_servers_are_not_scheduled(admin, servers) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import session_scope
    from app.jobs.runner import schedule_due
    from app.models import CollectorJob, Server

    sid = uuid.UUID(servers["PG-DEV-01"]["id"])
    async with session_scope() as db:
        await db.execute(
            update(Server).where(Server.id == sid).values(circuit_open_until=datetime.now(UTC) + timedelta(hours=1))
        )
        await db.execute(
            update(CollectorJob)
            .where(CollectorJob.server_id == sid)
            .values(created_at=datetime(2000, 1, 1, tzinfo=UTC))
        )
    try:
        before = datetime.now(UTC)
        await schedule_due()
        async with session_scope() as db:
            new = (
                (
                    await db.execute(
                        select(CollectorJob).where(CollectorJob.server_id == sid, CollectorJob.created_at >= before)
                    )
                )
                .scalars()
                .all()
            )
        assert new == []
    finally:
        async with session_scope() as db:
            await db.execute(update(Server).where(Server.id == sid).values(circuit_open_until=None))
