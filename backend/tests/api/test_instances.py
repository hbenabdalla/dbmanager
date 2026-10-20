"""Deployment registry (sprint 0.15.4): every process reports itself, any replica lists them all,
the leader is visible, a stopped process leaves, a crashed one turns stale and is pruned."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_instances_heartbeat_leader_stale_and_prune(admin, make_client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import session_scope
    from app.jobs import runner as job_runner
    from app.models import PlatformInstance
    from app.services import instances

    async with session_scope() as db:
        await db.execute(PlatformInstance.__table__.delete())
        # another replica: an API pod of the previous version, and a worker that crashed 10 min ago
        now = datetime.now(UTC)
        db.add(PlatformInstance(id="api-old:1", role="api", hostname="api-old", version="0.13.0", last_seen_at=now))
        db.add(
            PlatformInstance(
                id="worker-dead:7",
                role="worker",
                hostname="worker-dead",
                version="0.14.0",
                runs_worker=True,
                is_leader=True,
                last_seen_at=now - timedelta(minutes=10),
            )
        )
        db.add(
            PlatformInstance(
                id="ancient:1", role="api", hostname="a", version="0.12.0", last_seen_at=now - timedelta(days=2)
            )
        )

    class FakeRunner:
        is_leader = True

    monkeypatch.setattr(job_runner, "runner", FakeRunner())
    async with session_scope() as db:
        await instances.beat(db)  # this process, holding the leadership
    monkeypatch.setattr(job_runner, "runner", None)  # the row keeps what the heartbeat reported
    data = (await admin.get("/api/v1/platform/instances")).json()
    by_id = {i["id"]: i for i in data["instances"]}
    me = by_id[instances.instance_id()]
    assert me["this_instance"] and me["is_leader"] and me["status"] == "active"
    # the dead leader is stale and no longer counted as leader
    assert by_id["worker-dead:7"]["status"] == "stale" and by_id["worker-dead:7"]["is_leader"] is False
    assert "ancient:1" not in by_id  # pruned after a day
    summ = data["summary"]
    assert summ["leader"] == instances.instance_id() and summ["stale"] == 1 and summ["mixed_versions"] is True
    assert "0.13.0" in summ["versions"]
    # the collectors page sees the whole deployment
    st = (await admin.get("/api/v1/collectors/status")).json()
    assert st["deployment"]["leader"] == instances.instance_id()
    # a second beat updates instead of inserting (leadership gone now); leaving removes the row
    async with session_scope() as db:
        await instances.beat(db)
        assert (await db.get(PlatformInstance, instances.instance_id(), populate_existing=True)).is_leader is False
        await instances.leave(db)
    data = (await admin.get("/api/v1/platform/instances")).json()
    assert instances.instance_id() not in {i["id"] for i in data["instances"]}
    # needs collectors.view: the Monitoring User role does not have it, an environment DBA does
    monitor = await make_client(("monitor", DEMO_PW))
    assert (await monitor.get("/api/v1/platform/instances")).status_code == 403
    dba = await make_client(("dba.dev", DEMO_PW))
    assert (await dba.get("/api/v1/platform/instances")).status_code == 200
    # About is open to every signed-in user: counts only, never host names or process ids
    about = (await monitor.get("/api/v1/about")).json()["deployment"]
    assert set(about) == {"api", "workers", "stale", "has_leader", "versions", "mixed_versions"}
    assert "api-old" not in str(about) and "worker-dead" not in str(about)
    async with session_scope() as db:
        await db.execute(PlatformInstance.__table__.delete())


async def test_heartbeat_task_reports_and_leaves() -> None:
    import asyncio

    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import PlatformInstance
    from app.services import instances

    hb = instances.Heartbeat()
    hb.start()
    for _ in range(50):
        async with session_scope() as db:
            if await db.get(PlatformInstance, instances.instance_id()):
                break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError("no heartbeat row")
    await hb.stop()
    async with session_scope() as db:
        assert (
            await db.execute(select(PlatformInstance).where(PlatformInstance.id == instances.instance_id()))
        ).first() is None
