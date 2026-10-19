"""Performance intelligence API (0.13): z-score alert rules, query regressions, period and plan
comparison, capacity forecasts, recommendation validation — RBAC and audit included."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.timeseries.catalog import QUERY_BY_FIELD, name_for
from app.timeseries.model import Sample
from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")


def repo():  # type: ignore[no-untyped-def]
    from app.timeseries.repository import get_repository

    return get_repository()


def _labels(server: dict, **extra: str) -> dict[str, str]:  # type: ignore[type-arg]
    return {"server_id": server["id"], "engine": server["engine"], "data_source": "simulated", **extra}


async def _regressing_statement(server: dict, fp: str) -> None:  # type: ignore[type-arg]
    """48 h of a statement at 10 ms, the last hour at 60 ms."""
    now = datetime.now(UTC)
    lbl = _labels(server, database="itdb", fingerprint=fp)
    calls = total = 0.0
    samples = []
    for m in range(48 * 12, -1, -1):
        ts = now - timedelta(minutes=5 * m)
        calls += 50
        total += 50 * (60.0 if m < 12 else 10.0)
        samples += [
            Sample.make(QUERY_BY_FIELD["calls"].name, lbl, calls, ts),
            Sample.make(QUERY_BY_FIELD["total_ms"].name, lbl, total, ts),
        ]
    await repo().write_metrics_batch(samples)


async def test_query_regressions_and_rbac(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    pg = servers["PG-PROD-01"]
    fp = "f00d" + uuid.uuid4().hex[:12]
    await _regressing_statement(pg, fp)
    r = await admin.get(f"/api/v1/servers/{pg['id']}/query-regressions")
    assert r.status_code == 200, r.text
    body = r.json()
    hit = next(i for i in body["items"] if i["fingerprint"] == fp)
    assert hit["regressed"] and hit["kind"] == "latency"
    assert hit["baseline_mean_ms"] == pytest.approx(10, rel=0.05) and hit["recent_mean_ms"] == pytest.approx(
        60, rel=0.1
    )
    assert body["plan_identity"] is False and body["notes"]  # PostgreSQL exposes no plan identity
    # the recent window must be shorter than the baseline
    assert (
        await admin.get(
            f"/api/v1/servers/{pg['id']}/query-regressions", params={"recent_hours": 48, "baseline_days": 1}
        )
    ).status_code == 422
    # Monitoring User has no query.view; Performance Engineer of another server cannot see PG-PROD-01
    monitor = await make_client(("monitor", DEMO_PW))
    assert (await monitor.get(f"/api/v1/servers/{pg['id']}/query-regressions")).status_code == 403
    perf = await make_client(("perf.oracle", DEMO_PW))
    assert (await perf.get(f"/api/v1/servers/{pg['id']}/query-regressions")).status_code == 404


async def test_query_period_comparison(admin, servers) -> None:  # type: ignore[no-untyped-def]
    pg = servers["PG-PROD-01"]
    fp = "c0de" + uuid.uuid4().hex[:12]
    await _regressing_statement(pg, fp)
    r = await admin.get(f"/api/v1/servers/{pg['id']}/query-compare", params={"preset": "hour_vs_previous"})
    assert r.status_code == 200, r.text
    item = next(i for i in r.json()["items"] if i["fingerprint"] == fp)
    assert item["status"] == "slower" and item["mean_change_pct"] == pytest.approx(500, rel=0.15)
    now = datetime.now(UTC)
    custom = await admin.get(
        f"/api/v1/servers/{pg['id']}/query-compare",
        params={
            "a_start": (now - timedelta(hours=26)).isoformat(),
            "a_end": (now - timedelta(hours=2)).isoformat(),
            "b_start": (now - timedelta(hours=1)).isoformat(),
            "b_end": now.isoformat(),
        },
    )
    assert custom.status_code == 200 and custom.json()["summary"]["b"]["calls"] > 0
    bad = await admin.get(
        f"/api/v1/servers/{pg['id']}/query-compare",
        params={"a_start": now.isoformat(), "a_end": (now - timedelta(hours=1)).isoformat()},
    )
    assert bad.status_code == 422


async def test_capacity_forecasts(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    ora = servers["ORA-PROD-01"]
    now = datetime.now(UTC)
    samples = [
        Sample.make(
            name_for("storage.tablespace_used_bytes"),
            _labels(ora, tablespace="IT_TS"),
            1e9 + d * 5e7,
            now - timedelta(days=20 - d),
        )
        for d in range(21)
    ]
    samples.append(Sample.make(name_for("storage.tablespace_max_bytes"), _labels(ora, tablespace="IT_TS"), 3e9, now))
    await repo().write_metrics_batch(samples)
    r = await admin.get(f"/api/v1/servers/{ora['id']}/capacity-forecast", params={"scope": "tablespace"})
    assert r.status_code == 200, r.text
    ts = next(i for i in r.json()["items"] if i["object"] == "IT_TS")
    assert ts["days_to_full"]["likely"] == pytest.approx(20, abs=2) and ts["band"]
    fleet = await admin.get("/api/v1/capacity/forecast", params={"scope": "tablespace", "source": "all"})
    assert fleet.status_code == 200
    assert any(i["object"] == "IT_TS" and i["server_name"] == "ORA-PROD-01" for i in fleet.json()["items"])
    assert all("band" not in i and "history" not in i for i in fleet.json()["items"])
    assert len(ts["history"]) >= 20
    # RBAC: the monitoring user only sees Production servers
    monitor = await make_client(("monitor", DEMO_PW))
    mine = (await monitor.get("/api/v1/capacity/forecast", params={"source": "all"})).json()["items"]
    prod = {s["id"] for name, s in servers.items() if "-PROD-" in name}
    assert mine and len(prod) == 3
    assert {i["server_id"] for i in mine} <= prod
    assert (await admin.get(f"/api/v1/servers/{ora['id']}/capacity-forecast", params={"days": 3})).status_code == 422


async def test_saved_plans_compare_and_delete(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    pg = servers["PG-DEV-01"]
    first = await admin.post(
        f"/api/v1/servers/{pg['id']}/explain",
        json={"sql": "SELECT * FROM orders WHERE customer_id = 42", "save_label": "before index"},
    )
    assert first.status_code == 200, first.text
    second = await admin.post(
        f"/api/v1/servers/{pg['id']}/explain",
        json={"sql": "SELECT id FROM orders", "save_label": "after index", "fingerprint": "abc123"},
    )
    a, b = first.json()["snapshot_id"], second.json()["snapshot_id"]
    listed = (await admin.get(f"/api/v1/servers/{pg['id']}/plans")).json()
    assert {a, b} <= {p["id"] for p in listed}
    assert [
        p["id"] for p in (await admin.get(f"/api/v1/servers/{pg['id']}/plans", params={"fingerprint": "abc123"})).json()
    ] == [b]
    cmp = await admin.get("/api/v1/plans/compare", params={"a": a, "b": b})
    assert cmp.status_code == 200, cmp.text
    body = cmp.json()
    assert body["same_statement"] is False and body["diff"]["same_engine"] is True
    assert body["a"]["plan"]["root"] and "operations_added" in body["diff"]
    # another server's engineer cannot see the plan; a dev DBA can see it but not delete it
    perf = await make_client(("perf.oracle", DEMO_PW))
    assert (await perf.get(f"/api/v1/plans/{a}")).status_code == 404
    dba = await make_client(("dba.dev", DEMO_PW))
    seen = await dba.get(f"/api/v1/plans/{a}")
    assert seen.status_code == 200
    deleted = await dba.delete(f"/api/v1/plans/{a}")
    assert deleted.status_code in (200, 403)
    if deleted.status_code == 403:
        assert (await admin.delete(f"/api/v1/plans/{a}")).status_code == 200
    assert (await admin.get(f"/api/v1/plans/{a}")).status_code == 404
    events = (await admin.get("/api/v1/audit", params={"page_size": 200, "category": "query"})).json()["items"]
    actions = {e["action"] for e in events}
    assert {"query.plan_saved", "query.plan_deleted"} <= actions


async def test_zscore_alert_rule(admin, servers) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import session_scope
    from app.models import Alert
    from app.services import alerting, perf_intel

    pg = servers["PG-PROD-01"]
    bad = await admin.post(
        "/api/v1/alert-rules",
        json={
            "name": "z bad",
            "kind": "metric",
            "metric": "cpu.percent",
            "operator": ">",
            "threshold": 3,
            "aggregation": "zscore",
            "window_seconds": 30,
            "severity": "low",
        },
    )
    assert bad.status_code == 422
    r = await admin.post(
        "/api/v1/alert-rules",
        json={
            "name": f"it cpu anomaly {uuid.uuid4().hex[:6]}",
            "kind": "metric",
            "metric": "cpu.percent",
            "operator": ">",
            "threshold": 3,
            "aggregation": "zscore",
            "window_seconds": 600,
            "baseline_days": 2,
            "severity": "medium",
            "scope_type": "server",
            "scope_id": pg["id"],
        },
    )
    assert r.status_code == 201, r.text
    rule = r.json()
    assert rule["aggregation"] == "zscore" and rule["baseline_days"] == 2
    now = datetime.now(UTC)
    lbl = _labels(pg)
    base = [
        Sample.make(name_for("cpu.percent"), lbl, 20.0 + (i % 7), now - timedelta(minutes=15 + 5 * i))
        for i in range(200)
    ]
    spike = [Sample.make(name_for("cpu.percent"), lbl, 99.0, now - timedelta(minutes=m)) for m in range(1, 9)]
    await repo().write_metrics_batch(base + spike)
    perf_intel._BASELINE_CACHE.clear()
    try:
        async with session_scope() as db:
            await alerting.evaluate(db)
        async with session_scope() as db:
            from sqlalchemy import select

            alert = (await db.execute(select(Alert).where(Alert.rule_id == uuid.UUID(rule["id"])))).scalar_one()
            assert alert.status in ("pending", "firing") and "z-score of avg(cpu.percent, 10m)" in alert.message
            assert alert.value is not None and alert.value > 3
    finally:
        assert (await admin.delete(f"/api/v1/alert-rules/{rule['id']}")).status_code == 200


async def test_recommendation_validation_lifecycle(admin, servers) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import Recommendation
    from app.services import advisor

    items = (await admin.get("/api/v1/recommendations", params={"page_size": 100, "source": "all"})).json()["items"]
    rec = next(i for i in items if i["rule_id"] in ("GEN-PERF-007", "GEN-PERF-002", "GEN-PERF-001", "GEN-PERF-009"))
    r = await admin.patch(f"/api/v1/recommendations/{rec['id']}", json={"status": "resolved", "reason": "index added"})
    assert r.status_code == 200, r.text
    v = r.json()["validation"]
    assert v["status"] in ("measuring", "pending") and v["window_hours"] == 24
    if v["status"] == "measuring":
        assert "before" in v and v["method"]["kind"] in ("query", "metric")
    # make it due and let the job measure the after-window
    async with session_scope() as db:
        row = (await db.execute(select(Recommendation).where(Recommendation.id == uuid.UUID(rec["id"])))).scalar_one()
        if row.validation and row.validation.get("status") == "measuring":
            past = datetime.now(UTC) - timedelta(hours=25)
            row.resolved_at = past
            row.validation = {
                **row.validation,
                "resolved_at": past.isoformat(),
                "due_at": (past + timedelta(hours=24)).isoformat(),
            }
    async with session_scope() as db:
        stats = await advisor.run_validations(db)
    assert stats["failed"] == 0
    summary = (await admin.get("/api/v1/recommendations/validation-summary", params={"source": "all"})).json()
    assert sum(summary["counts"].values()) >= 1
    reopened = await admin.patch(f"/api/v1/recommendations/{rec['id']}", json={"status": "open"})
    assert reopened.json()["validation"]["status"] == "reopened"


async def test_performance_settings_are_validated(admin) -> None:  # type: ignore[no-untyped-def]
    r = await admin.put("/api/v1/settings", json={"performance.regression_threshold_pct": 1})
    assert r.status_code == 422
    ok = await admin.put("/api/v1/settings", json={"performance.regression_threshold_pct": 60})
    assert ok.status_code == 200, ok.text
    await admin.put("/api/v1/settings", json={"performance.regression_threshold_pct": 50})


async def test_advisor_raises_regression_and_forecast_findings(admin, servers) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import session_scope
    from app.models import Server
    from app.services import advisor

    pg = servers["PG-PROD-01"]
    fp = "beef" + uuid.uuid4().hex[:12]
    await _regressing_statement(pg, fp)
    now = datetime.now(UTC)
    lbl = _labels(pg, tablespace="IT_FULL")
    await repo().write_metrics_batch(
        [
            Sample.make(name_for("storage.tablespace_used_bytes"), lbl, 9e8 + d * 1e7, now - timedelta(days=10 - d))
            for d in range(11)
        ]
        + [Sample.make(name_for("storage.tablespace_max_bytes"), lbl, 1.05e9, now)]
    )
    async with session_scope() as db:
        server = await db.get(Server, uuid.UUID(pg["id"]))
        assert server is not None
        await advisor.run_performance(db, server)
    items = (
        await admin.get("/api/v1/recommendations", params={"server_id": pg["id"], "page_size": 200, "source": "all"})
    ).json()["items"]
    reg = next(i for i in items if i["rule_id"] == "GEN-PERF-011" and i["affected_object"] == f"query:{fp}")
    assert reg["evidence"]["recent_mean_ms"] > reg["evidence"]["baseline_mean_ms"]
    cap = next(i for i in items if i["rule_id"] == "GEN-PERF-012" and i["affected_object"] == "tablespace:IT_FULL")
    assert cap["severity"] in ("critical", "high") and cap["evidence"]["days_to_full"]["earliest"] <= 30
