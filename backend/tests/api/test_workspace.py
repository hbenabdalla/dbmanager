import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_classify_endpoint(admin, servers) -> None:  # type: ignore[no-untyped-def]
    sid = servers["PG-PROD-01"]["id"]
    r = await admin.post("/api/v1/workspace/classify", json={"sql": "truncate orders", "server_id": sid})
    body = r.json()
    assert body["risk"] == "DESTRUCTIVE" and body["confirmation"] == "server name"


async def test_production_change_requires_server_name_and_reason(admin, servers) -> None:  # type: ignore[no-untyped-def]
    s = servers["PG-PROD-01"]
    body = {"server_id": s["id"], "sql": "delete from orders"}
    r = await admin.post("/api/v1/workspace/execute", json=body)
    assert r.status_code == 428 and r.json()["error"]["details"]["expected"] == "server name"
    r = await admin.post("/api/v1/workspace/execute", json={**body, "confirm": s["name"]})
    assert r.status_code == 422  # change reason required in production
    r = await admin.post("/api/v1/workspace/execute", json={**body, "confirm": s["name"], "reason": "CHG-1234"})
    assert r.status_code == 200 and r.json()["simulated"] is True and r.json()["committed"] is False


async def test_multiple_statements_rejected(admin, servers) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/workspace/execute", json={"server_id": servers["PG-DEV-01"]["id"], "sql": "select 1; select 2"}
    )
    assert r.status_code == 422


async def test_history_records_without_secrets(admin, servers) -> None:  # type: ignore[no-untyped-def]
    sid = servers["PG-DEV-01"]["id"]
    await admin.post(
        "/api/v1/workspace/execute",
        json={"server_id": sid, "sql": "create role x password 'TopSecret1!'", "confirm": "CONFIRM"},
    )
    hist = (await admin.get("/api/v1/workspace/history")).json()["items"]
    assert hist and all("TopSecret1!" not in h["statement"] for h in hist)


async def test_explain_analyze_needs_confirmation(admin, servers) -> None:  # type: ignore[no-untyped-def]
    s = servers["PG-PROD-01"]
    r = await admin.post(f"/api/v1/servers/{s['id']}/explain", json={"sql": "select * from orders", "analyze": True})
    assert r.status_code == 428 and r.json()["error"]["details"]["production"] is True
    r = await admin.post(
        f"/api/v1/servers/{s['id']}/explain",
        json={"sql": "select * from orders", "analyze": True, "confirm": s["name"]},
    )
    assert r.status_code == 200 and r.json()["mode"] == "analyze"
    r = await admin.post(
        f"/api/v1/servers/{s['id']}/explain", json={"sql": "select * from orders o join customers c on c.id = o.cid"}
    )
    assert r.status_code == 200 and r.json()["executed"] is False and r.json()["root"]


async def test_csv_export(admin, servers) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/workspace/export.csv", json={"server_id": servers["PG-DEV-01"]["id"], "sql": "select 1"}
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
