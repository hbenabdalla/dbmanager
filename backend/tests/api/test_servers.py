import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_register_server_never_returns_secret(admin) -> None:  # type: ignore[no-untyped-def]
    envs = {e["name"]: e["id"] for e in (await admin.get("/api/v1/environments")).json()}
    r = await admin.post(
        "/api/v1/servers",
        json={
            "name": "unreachable-pg",
            "engine": "postgresql",
            "host": "10.255.255.1",
            "port": 5432,
            "environment_id": envs["Test"],
            "tls_mode": "require",
            "monitoring_enabled": False,
            "monitoring_credential": {"username": "mon", "password": "Very-Secret-Value-123"},
        },
    )
    assert r.status_code == 201, r.text
    assert "Very-Secret-Value-123" not in r.text
    sid = r.json()["id"]
    d = await admin.get(f"/api/v1/servers/{sid}")
    assert "Very-Secret-Value-123" not in d.text
    assert d.json()["credentials"]["monitoring"]["has_secret"] is True

    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import Credential

    async with session_scope() as db:
        cred = (await db.execute(select(Credential).where(Credential.server_id == sid))).scalar_one()
        assert "Very-Secret-Value-123" not in str(cred.encrypted)
        from app.services.secrets import resolve_password

        assert resolve_password(cred) == "Very-Secret-Value-123"


async def test_validation_errors(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/servers", json={"name": "bad host!", "engine": "postgresql", "host": "a b", "port": 0}
    )
    assert r.status_code == 422
    r = await admin.post("/api/v1/servers", json={"name": "bad-engine", "engine": "sybase", "host": "h", "port": 1})
    assert r.status_code == 422


async def test_new_engine_adapters_are_real_and_report_connection_errors(admin) -> None:  # type: ignore[no-untyped-def]
    """Phases 3–6 engines register as implemented adapters; an unreachable server reports a
    normalized error code instead of fake data."""
    for engine, port in (("oracle", 1521), ("mssql", 1433), ("db2", 50000), ("db2zos", 446)):
        r = await admin.post(
            "/api/v1/servers",
            json={
                "name": f"unreachable-{engine}",
                "engine": engine,
                "host": "unreachable.invalid",
                "port": port,
                "default_database": "TESTDB",
                "connection_options": {"connect_timeout": 3},
                "monitoring_enabled": False,
                "monitoring_credential": {"username": "mon", "password": "Never-Shown-Pw-9"},
            },
        )
        assert r.status_code == 201, r.text
        s = r.json()
        assert s["implementation"] == "implemented"
        t = (await admin.post(f"/api/v1/servers/{s['id']}/test-connection")).json()
        assert t["ok"] is False and t["error_code"] in ("CONNECTION_FAILED", "DRIVER_NOT_INSTALLED"), t
        assert "Never-Shown-Pw-9" not in str(t)
        caps = (await admin.get(f"/api/v1/servers/{s['id']}/capabilities")).json()["capabilities"]
        assert caps["connection"]["level"] != "PLANNED"


async def test_delete_requires_typed_confirmation(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/servers", json={"name": "to-delete", "engine": "postgresql", "host": "h.example", "port": 5432}
    )
    sid = r.json()["id"]
    assert (await admin.delete(f"/api/v1/servers/{sid}")).status_code == 428
    assert (await admin.delete(f"/api/v1/servers/{sid}", params={"confirm": "wrong"})).status_code == 428
    assert (await admin.delete(f"/api/v1/servers/{sid}", params={"confirm": "to-delete"})).status_code == 200
    events = (await admin.get("/api/v1/audit", params={"action": "server.delete"})).json()["items"]
    assert events and events[0]["risk_class"] == "DESTRUCTIVE"


async def test_list_filters_and_sorting(admin, servers) -> None:  # type: ignore[no-untyped-def]
    r = await admin.get("/api/v1/servers", params={"engine": ["oracle"], "sort": "-name", "source": "simulated"})
    names = [s["name"] for s in r.json()["items"]]
    assert names == sorted(names, reverse=True) and names and all(n.startswith("ORA") for n in names)
    assert (await admin.get("/api/v1/servers", params={"sort": "password"})).status_code == 422
    r = await admin.get("/api/v1/servers", params={"tag": "application=Order Management"})
    assert {s["name"] for s in r.json()["items"]} == {"PG-PROD-01", "PG-DR-01", "PG-DEV-01"}


async def test_dashboard_and_reports(admin, servers) -> None:  # type: ignore[no-untyped-def]
    d = (await admin.get("/api/v1/dashboard/summary", params={"source": "simulated"})).json()
    assert d["kpis"]["servers"] == 9 and d["kpis"]["unavailable"] >= 1 and d["demo"] is True
    assert d["top_recommendations"]
    r = await admin.get("/api/v1/reports/fleet-health.csv")
    assert r.status_code == 200 and r.text.startswith("server,engine")
    pdf = await admin.get("/api/v1/reports/fleet-health.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert (await admin.get("/api/v1/reports/fleet-health.xlsx")).status_code == 422


async def test_recommendation_status_is_audited_not_applied(admin, servers) -> None:  # type: ignore[no-untyped-def]
    rec = (await admin.get("/api/v1/recommendations", params={"page_size": 1})).json()["items"][0]
    r = await admin.patch(
        f"/api/v1/recommendations/{rec['id']}", json={"status": "acknowledged", "reason": "planned for Q4"}
    )
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    ev = (await admin.get("/api/v1/audit", params={"action": "recommendation.status"})).json()["items"]
    assert ev[0]["change_reason"] == "planned for Q4"


async def test_metrics_series(admin, servers) -> None:  # type: ignore[no-untyped-def]
    sid = servers["PG-PROD-01"]["id"]
    r = await admin.get(f"/api/v1/servers/{sid}/metrics", params={"metric": ["connections.total"], "range": "24h"})
    data = r.json()
    assert data["series"] and len(data["series"][0]["points"]) > 50
    assert (await admin.get(f"/api/v1/servers/{sid}/metrics", params={"range": "1y"})).status_code == 422


async def test_sql_server_test_connection_explains_missing_odbc_driver(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.adapters import drivers

    monkeypatch.setattr(drivers, "_odbc_drivers", lambda: [])
    r = await admin.post(
        "/api/v1/servers/test-connection",
        json={"engine": "mssql", "host": "10.255.255.1", "port": 1433, "username": "mon", "password": "x-Secret-1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["error_code"] == "DRIVER_NOT_INSTALLED"
    assert body["message"].startswith("Microsoft ODBC Driver 18 is required.")
    drv = {d["key"]: d for d in (await admin.get("/api/v1/platform/drivers")).json()}
    assert drv["pyodbc"]["status"] == "missing"
    support = {e["engine"]: e for e in (await admin.get("/api/v1/platform/engine-support")).json()["engines"]}
    assert support["mssql"]["driver_status"] == "missing"
    assert support["mssql"]["driver_detail"]["remediation"].startswith("Microsoft ODBC Driver 18 is required.")
    catalog = {e["engine"]: e for e in (await admin.get("/api/v1/engines")).json()}
    assert catalog["mssql"]["driver_status"] == "missing"


async def test_engine_support_carries_real_engine_validation(admin) -> None:  # type: ignore[no-untyped-def]
    support = {e["engine"]: e for e in (await admin.get("/api/v1/platform/engine-support")).json()["engines"]}
    assert support["postgresql"]["validation"]["status"] == "validated"
    assert support["postgresql"]["capabilities"]["Connection"]["validated"] == "validated"
    assert support["db2zos"]["validation"]["status"] == "external"
    for engine in ("mssql", "oracle", "db2"):  # until scripts/integration-test has run against them
        assert support[engine]["validation"]["status"] in ("pending", "validated", "failed")


async def test_licensed_features_can_be_changed_and_are_validated(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/servers",
        json={
            "name": "lic-ora-01",
            "engine": "oracle",
            "host": "ora.invalid",
            "port": 1521,
            "monitoring_enabled": False,
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    try:
        r = await admin.patch(f"/api/v1/servers/{sid}", json={"licensed_features": ["diagnostics_pack"]})
        assert r.status_code == 200, r.text
        assert (await admin.get(f"/api/v1/servers/{sid}")).json()["licensed_features"] == ["diagnostics_pack"]
        r = await admin.patch(f"/api/v1/servers/{sid}", json={"licensed_features": ["everything"]})
        assert r.status_code == 422
    finally:
        await admin.delete(f"/api/v1/servers/{sid}", params={"confirm": "lic-ora-01"})
