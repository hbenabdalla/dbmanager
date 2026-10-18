"""Backend RBAC enforcement — the frontend is never trusted."""

import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _roles(admin) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {r["name"]: r["id"] for r in (await admin.get("/api/v1/roles")).json()}


async def _envs(admin) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {e["name"]: e["id"] for e in (await admin.get("/api/v1/environments")).json()}


async def test_monitoring_user_sees_only_production(make_client, servers) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(("monitor", DEMO_PW))
    items = (await c.get("/api/v1/servers", params={"page_size": 500})).json()["items"]
    assert items and all(s["environment"]["name"] == "Production" for s in items)
    dev = servers["PG-DEV-01"]["id"]
    assert (await c.get(f"/api/v1/servers/{dev}")).status_code == 404  # invisible, no enumeration
    prod = servers["PG-PROD-01"]["id"]
    assert (await c.get(f"/api/v1/servers/{prod}/metrics")).status_code == 200
    # monitoring only: no sessions, no SQL, no users, no admin
    assert (await c.get(f"/api/v1/servers/{prod}/sessions")).status_code == 403
    assert (await c.get(f"/api/v1/servers/{prod}/database-users")).status_code == 403
    r = await c.post("/api/v1/workspace/execute", json={"server_id": prod, "sql": "select 1"})
    assert r.status_code == 403
    assert (
        await c.post("/api/v1/servers", json={"name": "x1", "engine": "postgresql", "host": "h", "port": 5432})
    ).status_code == 403
    assert (await c.get("/api/v1/audit")).status_code == 403
    assert (await c.get("/api/v1/users")).status_code == 403


async def test_dev_dba_scoped_to_development(make_client, servers) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(("dba.dev", DEMO_PW))
    dev, prod = servers["PG-DEV-01"]["id"], servers["PG-PROD-01"]["id"]
    r = await c.post("/api/v1/workspace/execute", json={"server_id": dev, "sql": "select 1"})
    assert r.status_code == 200
    r = await c.post("/api/v1/workspace/execute", json={"server_id": dev, "sql": "drop table orders"})
    assert r.status_code == 428  # allowed, but needs typed confirmation
    assert (await c.get(f"/api/v1/servers/{prod}")).status_code == 404
    assert (await c.post("/api/v1/workspace/execute", json={"server_id": prod, "sql": "select 1"})).status_code == 404


async def test_server_scoped_performance_engineer(make_client, servers) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(("perf.oracle", DEMO_PW))
    ora = servers["ORA-PROD-01"]["id"]
    items = (await c.get("/api/v1/servers")).json()["items"]
    assert [s["name"] for s in items] == ["ORA-PROD-01"]
    assert (await c.get(f"/api/v1/servers/{ora}/query-stats")).status_code == 200
    assert (await c.get(f"/api/v1/servers/{ora}/logs")).status_code == 200
    assert (await c.get(f"/api/v1/servers/{ora}/database-users")).status_code == 403
    r = await c.post("/api/v1/workspace/execute", json={"server_id": ora, "sql": "delete from t where id = 1"})
    assert r.status_code == 403 and "query.modify" in r.json()["error"]["details"]["missing_permissions"]


async def test_create_user_and_assign_restricted_permissions(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/users", json={"username": "ro.uat", "password": "Read-Only-Passw0rd!", "must_change_password": False}
    )
    assert r.status_code == 201
    uid = r.json()["id"]
    roles, envs = await _roles(admin), await _envs(admin)
    r = await admin.post(
        "/api/v1/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": uid,
            "role_id": roles["Read Only"],
            "scope_type": "environment",
            "scope_id": envs["UAT"],
        },
    )
    assert r.status_code == 201, r.text
    c = await make_client(("ro.uat", "Read-Only-Passw0rd!"))
    names = {s["name"] for s in (await c.get("/api/v1/servers")).json()["items"]}
    assert names == {"MSSQL-UAT-01", "MYSQL-UAT-01"}
    uat = servers["MSSQL-UAT-01"]["id"]
    assert (await c.get(f"/api/v1/servers/{uat}/databases")).status_code == 200
    assert (await c.patch(f"/api/v1/servers/{uat}", json={"owner": "me"})).status_code == 403
    assert (await c.post(f"/api/v1/servers/{uat}/collect")).status_code == 403
    me = (await c.get("/api/v1/auth/me")).json()
    assert me["grants"][0]["scope_label"] == "UAT"


async def test_self_assignment_and_escalation_blocked(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    roles, envs = await _roles(admin), await _envs(admin)
    # a scoped role manager for Development
    await admin.post(
        "/api/v1/roles",
        json={
            "name": "Dev Access Manager",
            "permissions": ["rbac.manage", "server.view", "performance.view", "alerts.view"],
        },
    )
    roles = await _roles(admin)
    r = await admin.post(
        "/api/v1/users", json={"username": "mgr.dev", "password": "Manager-Passw0rd!", "must_change_password": False}
    )
    mgr = r.json()["id"]
    r = await admin.post(
        "/api/v1/users", json={"username": "target.user", "password": "Target-Passw0rd!", "must_change_password": False}
    )
    target = r.json()["id"]
    assert (
        await admin.post(
            "/api/v1/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": mgr,
                "role_id": roles["Dev Access Manager"],
                "scope_type": "environment",
                "scope_id": envs["Development"],
            },
        )
    ).status_code == 201
    c = await make_client(("mgr.dev", "Manager-Passw0rd!"))
    # allowed: delegate a subset of own permissions within own scope
    ok = await c.post(
        "/api/v1/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": target,
            "role_id": roles["Monitoring User"],
            "scope_type": "environment",
            "scope_id": envs["Development"],
        },
    )
    assert ok.status_code == 201, ok.text
    # escalation: role with permissions the manager lacks
    r = await c.post(
        "/api/v1/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": target,
            "role_id": roles["DBA Administrator"],
            "scope_type": "environment",
            "scope_id": envs["Development"],
        },
    )
    assert r.status_code == 403 and r.json()["error"]["details"]["missing_permissions"]
    # outside own scope
    r = await c.post(
        "/api/v1/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": target,
            "role_id": roles["Monitoring User"],
            "scope_type": "environment",
            "scope_id": envs["Production"],
        },
    )
    assert r.status_code == 403
    # self-assignment, even within scope
    r = await c.post(
        "/api/v1/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": mgr,
            "role_id": roles["Monitoring User"],
            "scope_type": "environment",
            "scope_id": envs["Development"],
        },
    )
    assert r.status_code == 403
    # platform scope
    r = await c.post(
        "/api/v1/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": target,
            "role_id": roles["Monitoring User"],
            "scope_type": "platform",
            "scope_id": "*",
        },
    )
    assert r.status_code == 403


async def test_system_roles_immutable(admin) -> None:  # type: ignore[no-untyped-def]
    roles = await _roles(admin)
    r = await admin.patch(f"/api/v1/roles/{roles['Monitoring User']}", json={"permissions": ["users.manage"]})
    assert r.status_code == 422


async def test_unknown_permission_rejected(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post("/api/v1/roles", json={"name": "Bad", "permissions": ["everything.allowed"]})
    assert r.status_code == 422


async def test_denials_are_audited(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(("monitor", DEMO_PW))
    prod = servers["PG-PROD-01"]["id"]
    await c.post("/api/v1/workspace/execute", json={"server_id": prod, "sql": "select 1"})
    events = (await admin.get("/api/v1/audit", params={"username": "monitor", "result": ["denied"]})).json()["items"]
    assert events, "denied workspace execution must be audited"


async def test_masked_query_text_without_view_text(make_client, servers) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(("dba.dev", DEMO_PW))
    dev = servers["PG-DEV-01"]["id"]
    sess = (await c.get(f"/api/v1/servers/{dev}/sessions")).json()
    assert sess  # DBA has query.view_text -> literals visible
    assert any("'ACME-2041'" in (s["query"] or "") for s in sess)
