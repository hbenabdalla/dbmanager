"""Coverage the roadmap audit found missing for capabilities marked complete: guarded
administration end to end against a real PostgreSQL (preview → confirm → execute → audit), group
mapping synchronisation, security posture, privileged accounts and collector health."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy.engine import make_url

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _pg_roles(name: str) -> int:
    import asyncpg

    u = make_url(os.environ["DBM_TEST_DATABASE_URL"])
    conn = await asyncpg.connect(
        host=u.host, port=u.port or 5432, user=u.username, password=u.password, database=u.database
    )
    try:
        return int(await conn.fetchval("SELECT count(*) FROM pg_roles WHERE rolname = $1", name))
    finally:
        await conn.close()


@pytest.fixture
async def local_pg(admin):  # type: ignore[no-untyped-def]
    """The test PostgreSQL itself, registered as a managed server in administration mode."""
    u = make_url(os.environ["DBM_TEST_DATABASE_URL"])
    r = await admin.post(
        "/api/v1/servers",
        json={
            "name": f"it-local-pg-{uuid.uuid4().hex[:6]}",
            "engine": "postgresql",
            "host": u.host or "localhost",
            "port": u.port or 5432,
            "default_database": u.database,
            "tls_mode": "disable",
            "monitoring_enabled": False,
            "management_mode": "administration",
            "admin_credential": {"username": u.username, "password": u.password},
        },
    )
    assert r.status_code == 201, r.text
    s = r.json()
    yield s
    await admin.delete(f"/api/v1/servers/{s['id']}", params={"confirm": s["name"]})


async def test_guarded_administration_end_to_end(admin, local_pg) -> None:  # type: ignore[no-untyped-def]
    sid, role = local_pg["id"], f"dbm_it_{uuid.uuid4().hex[:8]}"
    actions = (await admin.get(f"/api/v1/servers/{sid}/admin/actions")).json()
    assert actions["available"] is True and actions["management_mode"] == "administration"

    body = {"action": "create_role", "params": {"name": role}}
    preview = (await admin.post(f"/api/v1/servers/{sid}/admin/preview", json=body)).json()
    assert preview["executable"] is True and preview["confirmation"] == "CONFIRM"
    assert any(role in s for s in preview["statements"])
    assert await _pg_roles(role) == 0  # preview never executes

    unconfirmed = await admin.post(f"/api/v1/servers/{sid}/admin/execute", json=body)
    assert unconfirmed.status_code == 428 and unconfirmed.json()["error"]["details"]["expected"] == "CONFIRM"
    assert await _pg_roles(role) == 0
    done = await admin.post(f"/api/v1/servers/{sid}/admin/execute", json={**body, "confirm": "CONFIRM"})
    assert done.status_code == 200, done.text
    assert await _pg_roles(role) == 1

    # destructive: the server name is the confirmation
    drop = {"action": "drop_user", "params": {"name": role}}
    p2 = (await admin.post(f"/api/v1/servers/{sid}/admin/preview", json=drop)).json()
    assert p2["confirmation"] == "server name"
    wrong = await admin.post(f"/api/v1/servers/{sid}/admin/execute", json={**drop, "confirm": "CONFIRM"})
    assert wrong.status_code == 428 and await _pg_roles(role) == 1
    ok = await admin.post(f"/api/v1/servers/{sid}/admin/execute", json={**drop, "confirm": local_pg["name"]})
    assert ok.status_code == 200, ok.text
    assert await _pg_roles(role) == 0

    events = (await admin.get("/api/v1/audit", params={"page_size": 50, "q": role})).json()["items"]
    actions_seen = {(e["action"], e["result"]) for e in events}
    assert ("admin.create_role", "success") in actions_seen and ("admin.drop_user", "success") in actions_seen


async def test_monitoring_only_server_never_executes(admin, local_pg) -> None:  # type: ignore[no-untyped-def]
    sid = local_pg["id"]
    r = await admin.patch(f"/api/v1/servers/{sid}", json={"management_mode": "monitoring"})
    assert r.status_code == 200, r.text
    body = {"action": "create_role", "params": {"name": "dbm_never_created"}, "confirm": "CONFIRM"}
    preview = (await admin.post(f"/api/v1/servers/{sid}/admin/preview", json=body)).json()
    assert preview["executable"] is False
    r = await admin.post(f"/api/v1/servers/{sid}/admin/execute", json=body)
    assert r.status_code in (403, 409, 422) and await _pg_roles("dbm_never_created") == 0


async def test_group_mapping_sync_adds_and_removes_memberships(app) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import Group, GroupMapping, GroupMember, IdentityProvider
    from app.services.identity_sync import provision_external_user

    async with session_scope() as db:
        idp = IdentityProvider(
            id=uuid.uuid4(), name=f"it-ldap-{uuid.uuid4().hex[:4]}", kind="ldap", enabled=True, config={}
        )
        dbas, devs = (
            Group(id=uuid.uuid4(), name=f"it-dbas-{uuid.uuid4().hex[:4]}"),
            Group(id=uuid.uuid4(), name=f"it-devs-{uuid.uuid4().hex[:4]}"),
        )
        db.add_all([idp, dbas, devs])
        await db.flush()
        db.add_all(
            [
                GroupMapping(
                    provider_id=idp.id, external_group="CN=DB Admins,OU=Groups,DC=example,DC=com", group_id=dbas.id
                ),
                GroupMapping(provider_id=idp.id, external_group="developers", group_id=devs.id),
            ]
        )
        await db.flush()
        user = await provision_external_user(
            db,
            idp,
            subject="uid=jdoe",
            username=f"jdoe{uuid.uuid4().hex[:4]}",
            email=None,
            display_name="J Doe",
            external_groups=["cn=db admins,ou=groups,dc=example,dc=com", "developers"],
        )
        assert user is not None
        member_of = set(
            (await db.execute(select(GroupMember.group_id).where(GroupMember.user_id == user.id))).scalars()
        )
        assert member_of == {dbas.id, devs.id}
        # next login: the directory no longer lists "developers" -> that membership is removed
        await provision_external_user(
            db,
            idp,
            subject="uid=jdoe",
            username=user.username,
            email=None,
            display_name=None,
            external_groups=["CN=DB Admins,OU=Groups,DC=example,DC=com"],
        )
        member_of = set(
            (await db.execute(select(GroupMember.group_id).where(GroupMember.user_id == user.id))).scalars()
        )
        assert member_of == {dbas.id}
        # a local account with the same name is never taken over by an external identity
        from app.models import User

        db.add(User(username="local.only", auth_source="local"))
        await db.flush()
        assert (
            await provision_external_user(
                db, idp, subject="uid=local", username="local.only", email=None, display_name=None, external_groups=[]
            )
            is None
        )
        await db.rollback()


async def test_security_posture_privileged_accounts_and_collectors(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    posture = await admin.get("/api/v1/security/posture")
    assert posture.status_code == 200, posture.text
    data = posture.json()
    assert isinstance(data, dict) and data
    accounts = await admin.get("/api/v1/access/privileged-accounts", params={"page_size": 200})
    assert accounts.status_code == 200 and "items" in accounts.json()
    perf = await make_client(("perf.oracle", DEMO_PW))
    scoped = (await perf.get("/api/v1/access/privileged-accounts", params={"page_size": 200})).json()
    assert {a.get("server_name") for a in scoped["items"]} <= {"ORA-PROD-01"}

    status = await admin.get("/api/v1/collectors/status")
    assert status.status_code == 200, status.text
    jobs = await admin.get("/api/v1/collectors/jobs", params={"page_size": 20})
    assert jobs.status_code == 200 and "items" in jobs.json()
    monitor = await make_client(("monitor", DEMO_PW))
    assert (await monitor.get("/api/v1/collectors/status")).status_code in (200, 403)
