"""Monitoring only is the default (roadmap baseline: guarded administration). A server
registered without a management mode never gets change actions, and EXPLAIN ANALYZE of a
data-changing statement — which executes it, even if rolled back — counts as a change."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _register(admin, **extra) -> dict:  # type: ignore[no-untyped-def, type-arg]
    u = make_url(os.environ["DBM_TEST_DATABASE_URL"])
    r = await admin.post(
        "/api/v1/servers",
        json={
            "name": f"mm-{uuid.uuid4().hex[:6]}",
            "engine": "postgresql",
            "host": u.host or "localhost",
            "port": u.port or 5432,
            "default_database": u.database,
            "tls_mode": "disable",
            "monitoring_enabled": False,
            "monitoring_credential": {"username": u.username, "password": u.password},
            "admin_credential": {"username": u.username, "password": u.password},
            **extra,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_monitoring_only_is_the_default(admin) -> None:  # type: ignore[no-untyped-def]
    s = await _register(admin)
    try:
        assert s["management_mode"] == "monitoring"
        actions = (await admin.get(f"/api/v1/servers/{s['id']}/admin/actions")).json()
        assert actions["management_mode"] == "monitoring"
        # a change through the workspace is refused, a read is not
        body = {"server_id": s["id"], "sql": "create table mm_probe (id int)", "confirm": "CONFIRM"}
        r = await admin.post("/api/v1/workspace/execute", json=body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "monitoring_only"
        ok = await admin.post("/api/v1/workspace/execute", json={"server_id": s["id"], "sql": "select 1"})
        assert ok.status_code == 200, ok.text
        # EXPLAIN ANALYZE of a DML statement executes it: refused on a Monitoring-only server ...
        dml = {"sql": "delete from pg_catalog.pg_class where false", "analyze": True, "confirm": s["name"]}
        r = await admin.post(f"/api/v1/servers/{s['id']}/explain", json=dml)
        assert r.status_code == 409 and r.json()["error"]["code"] == "monitoring_only"
        # ... while a read-only EXPLAIN ANALYZE is allowed
        read = {"sql": "select 1", "analyze": True, "confirm": "ANALYZE"}
        r = await admin.post(f"/api/v1/servers/{s['id']}/explain", json=read)
        assert r.status_code == 200, r.text
    finally:
        await admin.delete(f"/api/v1/servers/{s['id']}", params={"confirm": s["name"]})


async def test_administration_must_be_chosen_explicitly(admin) -> None:  # type: ignore[no-untyped-def]
    s = await _register(admin, management_mode="administration")
    try:
        dml = {"sql": "delete from pg_catalog.pg_class where false", "analyze": True, "confirm": s["name"]}
        r = await admin.post(f"/api/v1/servers/{s['id']}/explain", json=dml)
        assert r.status_code == 200, r.text
    finally:
        await admin.delete(f"/api/v1/servers/{s['id']}", params={"confirm": s["name"]})
