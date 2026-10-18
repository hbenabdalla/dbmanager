"""Collector diagnostic bundle (sprint 0.10.5): contents, RBAC, audit and redaction — no
credentials, no secrets in error messages, options or job results."""

from __future__ import annotations

import json
import uuid

import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")
SECRETS = ["Monitor-Secret-77", "S3cr3t-In-Error", "tok3n-value-123", "Opt-Passw0rd"]


async def test_diagnostics_bundle_is_complete_and_redacted(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import session_scope
    from app.models import CollectorJob, Server

    r = await admin.post(
        "/api/v1/servers",
        json={
            "name": f"diag-{uuid.uuid4().hex[:6]}",
            "engine": "postgresql",
            "host": "10.20.0.9",
            "port": 5432,
            "monitoring_enabled": False,
            "tls_mode": "verify-full",
            "monitoring_credential": {"username": "dbm_monitor", "password": SECRETS[0]},
        },
    )
    assert r.status_code == 201, r.text
    sid = uuid.UUID(r.json()["id"])
    async with session_scope() as db:
        s = await db.get(Server, sid)
        s.last_error = f"connection to postgresql://dbm_monitor:{SECRETS[1]}@10.20.0.9/x failed; password={SECRETS[1]}"
        s.collector_state = {"components": {"metrics": {"status": "failed", "error": f"token={SECRETS[2]}"}}}
        s.options = {**(s.options or {}), "application_name": f"dbm pwd={SECRETS[3]}"}
        db.add(
            CollectorJob(
                server_id=sid,
                kind="collect_metrics",
                status="failed",
                last_error=f"Authorization: Bearer {SECRETS[2]}",
                result={"password": SECRETS[3], "rows": 3},
                duration_ms=1234.5,
            )
        )

    res = await admin.get(f"/api/v1/servers/{sid}/diagnostics")
    assert res.status_code == 200, res.text
    assert res.headers["content-disposition"].startswith('attachment; filename="dbm-diagnostics-diag-')
    body = res.text
    for secret in SECRETS:
        assert secret not in body, secret
    d = json.loads(body)
    assert d["format"] == "dbmanager-diagnostics/1" and d["platform"]["schema"]
    assert d["server"]["host"] == "10.20.0.9" and d["server"]["tls_mode"] == "verify-full"
    assert d["logins"] == [
        {
            "purpose": "monitoring",
            "username": "d***",
            "provider": "local",
            "reference": None,
            "has_secret": True,
            "rotated_at": d["logins"][0]["rotated_at"],
        }
    ]
    assert d["health"]["last_error"].startswith("connection to postgresql://dbm_monitor:***@10.20.0.9")
    job = d["jobs"][0]
    assert job["kind"] == "collect_metrics" and job["duration_ms"] == 1234.5 and job["result"]["rows"] == 3
    assert job["error"] == "Authorization: Bearer ***" and job["result"]["password"] == "***"
    assert d["driver"]["driver"] == "asyncpg" and d["capabilities"] is not None

    # RBAC: the Log Viewer role has no collectors.view
    monitor = await make_client(("monitor", DEMO_PW))
    assert (await monitor.get(f"/api/v1/servers/{sid}/diagnostics")).status_code in (403, 404)
    events = (await admin.get("/api/v1/audit", params={"page_size": 20, "category": "operations"})).json()["items"]
    assert any(e["action"] == "server.diagnostics" for e in events)
    async with session_scope() as db:
        await db.delete(await db.get(Server, sid))
