"""Simulation lifecycle: generate, regenerate, remove — permissions, safety and auditing."""

from __future__ import annotations

import asyncio
import socket

import pytest

from tests.api.conftest import DEMO_PW, ensure_ci_fleet, ensure_test_users

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _run_worker() -> dict:
    """What the background worker does for a queued simulation job."""
    from app.simulation import service

    return await service.execute_pending()


async def _real_server(admin, name: str) -> dict:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/servers",
        json={
            "name": name,
            "engine": "postgresql",
            "host": "db.real.example",
            "port": 5432,
            "monitoring_enabled": False,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_only_super_admin_can_manage_simulation(make_client, servers) -> None:  # type: ignore[no-untyped-def]
    for user in ("monitor", "dba.dev", "auditor"):
        c = await make_client((user, DEMO_PW))
        assert (await c.get("/api/v1/simulation/status")).status_code == 403
        assert (await c.get("/api/v1/simulation/profiles")).status_code == 403
        assert (await c.post("/api/v1/simulation/generate", json={"profile": "small"})).status_code == 403
        r = await c.post("/api/v1/simulation/regenerate", json={"profile": "small", "confirm": "REGENERATE"})
        assert r.status_code == 403
        assert (await c.delete("/api/v1/simulation", params={"confirm": "REMOVE"})).status_code == 403
    # nothing changed
    st = (await (await make_client(("admin", "Admin-Passw0rd!2026"))).get("/api/v1/simulation/status")).json()
    assert st["servers"] == 9


async def test_denied_simulation_attempts_are_audited(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    c = await make_client(("monitor", DEMO_PW))
    await c.delete("/api/v1/simulation", params={"confirm": "REMOVE"})
    ev = (await admin.get("/api/v1/audit", params={"username": "monitor", "result": ["denied"]})).json()["items"]
    assert any("simulation" in (e.get("details") or {}).get("path", "") or e["action"] == "access.denied" for e in ev)


async def test_permissions_are_platform_only_and_on_super_admin_role(admin) -> None:  # type: ignore[no-untyped-def]
    perms = {p["key"]: p for p in (await admin.get("/api/v1/permissions")).json()}
    for key in ("simulation.view", "simulation.generate", "simulation.delete"):
        assert perms[key]["platform_only"] is True
    roles = {r["name"]: r for r in (await admin.get("/api/v1/roles")).json()}
    holders = [n for n, r in roles.items() if "simulation.generate" in r["permissions"]]
    assert holders == ["Platform Administrator"]


async def test_status_and_profiles(admin, servers) -> None:  # type: ignore[no-untyped-def]
    st = (await admin.get("/api/v1/simulation/status")).json()
    assert st["simulation_present"] is True and st["profile"] == "ci" and st["servers"] == 9
    assert st["telemetry_samples"] > 0 and st["query_fingerprints"] > 0
    assert st["security_findings"] > 0 and st["recommendations"] > 0
    profiles = {p["key"]: p for p in (await admin.get("/api/v1/simulation/profiles")).json()}
    assert profiles["small"]["servers"] == 5
    assert 15 <= profiles["standard"]["servers"] <= 20
    assert 50 <= profiles["large"]["servers"] <= 100


async def test_simulated_servers_are_marked(admin, servers) -> None:  # type: ignore[no-untyped-def]
    sim = [s for s in servers.values() if s["is_demo"]]
    assert len(sim) == 9 and all(s["data_source"] == "simulated" and s["host"].endswith(".sim.invalid") for s in sim)
    d = (await admin.get("/api/v1/dashboard/summary", params={"source": "simulated"})).json()
    assert all(t["is_demo"] for t in d["fleet"]) and d["demo"] is True


async def test_generate_refused_while_present_and_unknown_profile(admin, servers) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post("/api/v1/simulation/generate", json={"profile": "small"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "simulation_present"
    r = await admin.post("/api/v1/simulation/regenerate", json={"profile": "nope", "confirm": "REGENERATE"})
    assert r.status_code == 422


async def test_remove_requires_confirmation(admin, servers) -> None:  # type: ignore[no-untyped-def]
    assert (await admin.delete("/api/v1/simulation")).status_code == 428
    assert (await admin.delete("/api/v1/simulation", params={"confirm": "remove"})).status_code == 428
    r = await admin.post("/api/v1/simulation/regenerate", json={"profile": "small"})
    assert r.status_code == 428
    assert (await admin.get("/api/v1/simulation/status")).json()["servers"] == 9


async def test_simulated_servers_take_no_credentials(admin, servers) -> None:  # type: ignore[no-untyped-def]
    sid = servers["PG-PROD-01"]["id"]
    r = await admin.put(
        f"/api/v1/servers/{sid}/credentials/monitoring", json={"username": "x", "password": "Pw-123456789"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "simulated_server"


async def test_remove_keeps_real_resources_users_rbac_and_settings(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    real = await _real_server(admin, "real-pg-keep-01")
    site = (await admin.post("/api/v1/sites", json={"name": "Real HQ", "provider": "on-prem"})).json()
    await admin.patch(f"/api/v1/servers/{real['id']}", json={"site_id": site["id"]})
    r = await admin.post(
        "/api/v1/users", json={"username": "keep.me", "password": "Keep-Me-Passw0rd!", "must_change_password": False}
    )
    uid = r.json()["id"]
    roles = {x["name"]: x["id"] for x in (await admin.get("/api/v1/roles")).json()}
    ra = await admin.post(
        "/api/v1/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": uid,
            "role_id": roles["DBA Administrator"],
            "scope_type": "server",
            "scope_id": real["id"],
        },
    )
    assert ra.status_code == 201
    await admin.put("/api/v1/settings", json={"ui.banner_text": "Keep this banner"})
    envs_before = [(e["id"], e["name"], e["data_source"]) for e in (await admin.get("/api/v1/environments")).json()]
    roles_before = (await admin.get("/api/v1/roles")).json()

    preview = (await admin.get("/api/v1/simulation/cleanup-preview")).json()
    assert preview["counts"]["servers"] == 9 and preview["counts"]["real_servers"] >= 1

    # real telemetry in the time-series database must survive the removal
    from datetime import UTC, datetime

    from app.timeseries.model import Sample, Selector
    from app.timeseries.repository import get_repository

    repo = get_repository()
    now = datetime.now(UTC)
    real_labels = {"server_id": real["id"], "engine": "postgresql", "data_source": "real"}
    await repo.write_metrics_batch([Sample.make("db_connections", real_labels, 7.0, now)])
    sim_ids = [s["id"] for s in servers.values() if s["data_source"] == "simulated"]
    assert await repo.get_latest(Selector.of("db_up", sim_ids), lookback_s=86400 * 2)

    r = await admin.delete("/api/v1/simulation", params={"confirm": "REMOVE"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["servers_deleted"] == 9 and body["telemetry_samples_deleted"] > 0
    assert body["recommendations_deleted"] > 0 and body["timeseries_delete"] == "done"
    # simulated series are gone (deleted by the data_source label), the real one is intact
    assert not await repo.get_latest(Selector.of(["db_up", "db_connections"], sim_ids), lookback_s=86400 * 3)
    assert not await repo.get_latest(Selector.of("db_up", None, data_source="simulated"), lookback_s=86400 * 3)
    kept = await repo.get_latest(Selector.of("db_connections", [real["id"]]))
    assert [v.value for v in kept] == [7.0]

    all_servers = (await admin.get("/api/v1/servers", params={"page_size": 500, "source": "all"})).json()["items"]
    assert all(not s["is_demo"] for s in all_servers)
    assert any(s["id"] == real["id"] for s in all_servers)
    assert (await admin.get(f"/api/v1/servers/{real['id']}")).json()["site"]["name"] == "Real HQ"
    envs_after = [(e["id"], e["name"], e["data_source"]) for e in (await admin.get("/api/v1/environments")).json()]
    assert envs_after == envs_before and {e[2] for e in envs_after} == {"system"}
    assert [x["name"] for x in (await admin.get("/api/v1/roles")).json()] == [x["name"] for x in roles_before]
    st = (await admin.get("/api/v1/simulation/status")).json()
    assert st["simulation_present"] is False and st["servers"] == 0 and st["telemetry_samples"] == 0
    # users and their grants survive; the bootstrap admin still works
    c = await make_client(("keep.me", "Keep-Me-Passw0rd!"))
    assert [s["id"] for s in (await c.get("/api/v1/servers")).json()["items"]] == [real["id"]]
    assert (await make_client(("admin", "Admin-Passw0rd!2026"))).headers.get("X-CSRF-Token")
    settings = (await admin.get("/api/v1/settings")).json()
    assert settings["editable"]["ui.banner_text"] == "Keep this banner"
    await admin.put("/api/v1/settings", json={"ui.banner_text": ""})
    # removal is audited
    ev = (await admin.get("/api/v1/audit", params={"action": "simulation.removed"})).json()["items"]
    assert ev and ev[0]["result"] == "success" and ev[0]["details"]["servers_deleted"] == 9
    # empty / real-only dashboard
    d = (await admin.get("/api/v1/dashboard/summary")).json()
    assert d["source"] == "real" and d["demo"] is False and d["sources"]["simulated"] == 0


async def test_generate_small_profile(admin) -> None:  # type: ignore[no-untyped-def]
    st = (await admin.get("/api/v1/simulation/status")).json()
    if st["servers"]:
        await admin.delete("/api/v1/simulation", params={"confirm": "REMOVE"})
    r = await admin.post("/api/v1/simulation/generate", json={"profile": "small", "seed": 42})
    assert r.status_code == 202 and r.json()["run"]["status"] == "pending"
    # a second request while one is queued is refused
    r2 = await admin.post("/api/v1/simulation/regenerate", json={"profile": "small", "confirm": "REGENERATE"})
    assert r2.status_code == 409 and r2.json()["error"]["code"] == "simulation_busy"
    assert (await admin.delete("/api/v1/simulation", params={"confirm": "REMOVE"})).status_code == 409

    result = await _run_worker()
    assert result["servers_created"] == 5 and result["metrics_created"] > 1000 and result["queries_created"] > 0
    assert result["findings_created"] > 0 and result["recommendations_created"] > 0
    st = (await admin.get("/api/v1/simulation/status")).json()
    assert st["profile"] == "small" and st["seed"] == 42 and st["generated_by"] == "admin"
    assert st["active_run"] is None and st["last_run"]["status"] == "completed"

    items = (await admin.get("/api/v1/servers", params={"source": "simulated", "page_size": 50})).json()["items"]
    assert {s["engine"] for s in items} == {"postgresql", "oracle", "db2", "mssql", "mysql"}
    health = {s["name"]: s["health_status"] for s in items}
    assert "unavailable" in health.values() and {"critical", "warning"} & set(health.values())
    # every non-healthy server carries the evidence that explains it
    for s in items:
        if s["health_status"] in ("warning", "critical", "unavailable"):
            assert s["health_reasons"], s["name"]

    sources = (await admin.get("/api/v1/fleet/sources")).json()
    assert sources["simulated"] == 5 and sources["simulation_active"] is True
    ev = (await admin.get("/api/v1/audit", params={"action": "simulation.generated"})).json()["items"]
    assert ev and ev[0]["result"] == "success" and ev[0]["details"]["profile"] == "small"
    assert ev[0]["details"]["servers_created"] == 5 and ev[0]["username"] == "admin"
    req = (await admin.get("/api/v1/audit", params={"action": "simulation.generate_requested"})).json()["items"]
    assert req and req[0]["result"] == "accepted"


async def test_same_seed_same_estate(admin) -> None:  # type: ignore[no-untyped-def]
    before = sorted(
        s["name"] for s in (await admin.get("/api/v1/servers", params={"source": "simulated"})).json()["items"]
    )
    r = await admin.post(
        "/api/v1/simulation/regenerate", json={"profile": "small", "seed": 42, "confirm": "REGENERATE"}
    )
    assert r.status_code == 202
    result = await _run_worker()
    assert result["removed"]["servers_deleted"] == 5 and result["servers_created"] == 5
    after = sorted(
        s["name"] for s in (await admin.get("/api/v1/servers", params={"source": "simulated"})).json()["items"]
    )
    assert before == after
    ev = (await admin.get("/api/v1/audit", params={"action": "simulation.regenerated"})).json()["items"]
    assert ev and ev[0]["result"] == "success"


async def test_regenerate_standard_profile(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/simulation/regenerate", json={"profile": "standard", "seed": 3, "confirm": "REGENERATE"}
    )
    assert r.status_code == 202
    result = await _run_worker()
    assert 15 <= result["servers_created"] <= 20
    items = (await admin.get("/api/v1/servers", params={"source": "simulated", "page_size": 100})).json()["items"]
    envs = {s["environment"]["name"] for s in items}
    assert envs == {"Production", "DR", "UAT", "Development", "Test"}
    assert {s["engine"] for s in items} == {"postgresql", "oracle", "db2", "mssql", "mysql", "mariadb"}
    assert any(s["deployment"] != "self-managed" for s in items)  # cloud-managed services
    assert any(s["cluster"] for s in items)
    recs = (await admin.get("/api/v1/recommendations", params={"source": "simulated", "page_size": 200})).json()
    assert recs["total"] > 10
    for rec in recs["items"]:
        assert rec["evidence"] and rec["recommendation"] and rec["risk"] and rec["confidence"] > 0
    sec = (await admin.get("/api/v1/security/findings", params={"source": "simulated", "page_size": 200})).json()
    assert {f["severity"] for f in sec["items"]} >= {"critical", "high", "medium", "low"}
    # the real/simulated filter keeps the two apart
    real = (await admin.get("/api/v1/recommendations", params={"source": "real"})).json()
    assert all(not x["is_demo"] for x in real["items"])


async def test_simulated_collectors_never_touch_the_network(monkeypatch, admin) -> None:  # type: ignore[no-untyped-def]
    import asyncpg

    from app.db.session import session_scope
    from app.models import Server
    from app.services import advisor, collection

    calls: list[str] = []

    def refuse(*a, **k):  # type: ignore[no-untyped-def]
        calls.append("network")
        raise AssertionError("simulated server attempted a network connection")

    async def refuse_async(*a, **k):  # type: ignore[no-untyped-def]
        refuse()

    from sqlalchemy import select

    async with session_scope() as db:
        ids = list((await db.execute(select(Server.id).where(Server.data_source == "simulated"))).scalars())
    assert ids
    monkeypatch.setattr(asyncpg, "connect", refuse_async)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(asyncio, "open_connection", refuse_async)
    for sid in ids:
        async with session_scope() as db:
            s = await db.get(Server, sid)
            assert s is not None
            if "down" in s.options["demo_profile"]["scenarios"]:
                continue
            await collection.collect_metrics(db, s)
            await collection.collect_query_stats(db, s)
            await collection.discover(db, s)
            await advisor.run_performance(db, s)
            await advisor.run_security(db, s)
    assert calls == []


async def test_real_adapter_refuses_simulated_spec() -> None:
    from app.adapters.base import ConnectionSpec
    from app.adapters.registry import create_adapter

    with pytest.raises(ValueError):
        create_adapter("postgresql", ConnectionSpec(host="x", port=1, demo_profile={"engine": "postgresql"}))


async def test_restore_ci_fleet() -> None:
    await ensure_ci_fleet()
    await ensure_test_users()
