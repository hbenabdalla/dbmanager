"""Security posture score: 100 minus a weighted penalty per open or acknowledged finding
(critical 25, high 10, medium 4, low 1), never below 0; resolved and suppressed findings do not
count; scoped users get the score of the servers they can see."""

from __future__ import annotations

import uuid

import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _finding(db, server_id: uuid.UUID, severity: str, status: str = "open") -> None:  # type: ignore[no-untyped-def]
    from app.models import SecurityFinding

    db.add(
        SecurityFinding(
            fingerprint=f"posture-{uuid.uuid4().hex}",
            rule_id="TEST-SEC-001",
            server_id=server_id,
            engine="postgresql",
            category="authentication",
            severity=severity,
            title="test finding",
            finding="f",
            recommendation="r",
            risk="x",
            confidence=1.0,
            status=status,
        )
    )


async def test_posture_score_weights_open_findings_only(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import session_scope

    envs = {e["name"]: e["id"] for e in (await admin.get("/api/v1/environments")).json()}
    made = []
    for name, env in (("posture-a", "Development"), ("posture-b", "Development"), ("posture-c", "Production")):
        r = await admin.post(
            "/api/v1/servers",
            json={
                "name": f"{name}-{uuid.uuid4().hex[:4]}",
                "engine": "postgresql",
                "host": "db.invalid",
                "port": 5432,
                "monitoring_enabled": False,
                "environment_id": envs[env],
            },
        )
        assert r.status_code == 201, r.text
        made.append(r.json())
    a, b, c = (uuid.UUID(m["id"]) for m in made)
    try:
        async with session_scope() as db:
            # a: 100 - (25 + 2*10 + 4) = 51; the resolved critical does not count
            for sev, st in (("critical", "open"), ("high", "open"), ("high", "acknowledged"), ("medium", "open")):
                await _finding(db, a, sev, st)
            await _finding(db, a, "critical", "resolved")
            # b: five criticals -> clamped at 0
            for _ in range(5):
                await _finding(db, b, "critical")
            # c: a suppressed finding only -> 100
            await _finding(db, c, "high", "suppressed")
        data = (await admin.get("/api/v1/security/posture", params={"source": "all"})).json()
        by_id = {s["server_id"]: s for s in data["servers"]}
        assert by_id[str(a)]["score"] == 51 and by_id[str(a)]["by_severity"] == {"critical": 1, "high": 2, "medium": 1}
        assert by_id[str(b)]["score"] == 0
        assert by_id[str(c)]["score"] == 100 and by_id[str(c)]["by_severity"] == {}
        # the fleet score is the mean of the server scores, worst servers first
        assert data["score"] == round(sum(s["score"] for s in data["servers"]) / len(data["servers"]))
        assert [s["score"] for s in data["servers"]] == sorted(s["score"] for s in data["servers"])
        # a Development-scoped DBA sees a and b, never c
        dev = await make_client(("dba.dev", DEMO_PW))
        scoped = {
            s["server_id"]
            for s in (await dev.get("/api/v1/security/posture", params={"source": "all"})).json()["servers"]
        }
        assert {str(a), str(b)} <= scoped and str(c) not in scoped
    finally:
        for m in made:
            await admin.delete(f"/api/v1/servers/{m['id']}")
