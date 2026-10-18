"""Metrics API on the time-series layer: RBAC scoping, aggregation, query telemetry, outage
handling, health, monitoring-storage administration, deletion and the legacy migration."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")


def repo():  # type: ignore[no-untyped-def]
    from app.timeseries.repository import get_repository

    return get_repository()


async def test_server_metrics_come_from_the_timeseries_store(admin, servers) -> None:  # type: ignore[no-untyped-def]
    sid = servers["PG-PROD-01"]["id"]
    for rng in ("15m", "30m", "1h", "6h", "12h", "24h", "7d", "30d", "90d"):
        r = await admin.get(f"/api/v1/servers/{sid}/metrics", params={"metric": ["connections.total"], "range": rng})
        assert r.status_code == 200, r.text
        body = r.json()
        for s in body["series"]:
            assert len(s["points"]) <= 241
    body = (
        await admin.get(
            f"/api/v1/servers/{sid}/metrics", params={"metric": ["connections.total", "cpu.percent"], "range": "24h"}
        )
    ).json()
    assert {s["metric"] for s in body["series"]} == {"connections.total", "cpu.percent"}
    assert all(s["server_id"] == sid for s in body["series"])
    for agg in ("avg", "min", "max", "p95", "p99", "count"):
        r = await admin.get(
            f"/api/v1/servers/{sid}/metrics", params={"metric": ["connections.total"], "range": "6h", "agg": agg}
        )
        assert r.status_code == 200 and r.json()["aggregation"] == agg
    bad = await admin.get(f"/api/v1/servers/{sid}/metrics", params={"metric": ["cpu.percent"], "agg": "median"})
    assert bad.status_code == 422
    labeled = (
        await admin.get(f"/api/v1/servers/{sid}/metrics", params={"metric": ["wait.active_sessions"], "labeled": True})
    ).json()
    assert labeled["series"] and all(s["label"] for s in labeled["series"])


async def test_fleet_range_is_scoped_by_rbac_before_querying(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    perf = await make_client(("perf.oracle", DEMO_PW))  # performance rights on ORA-PROD-01 only
    ora, pg = servers["ORA-PROD-01"]["id"], servers["PG-PROD-01"]["id"]
    r = await perf.get("/api/v1/metrics/range", params={"metric": ["connections.total"], "range": "6h"})
    assert r.status_code == 200
    assert {s["server_id"] for s in r.json()["series"]} == {ora}
    assert set(r.json()["servers"]) == {ora}
    # asking for a server outside the scope returns nothing for it (no existence leak)
    r = await perf.get("/api/v1/metrics/range", params={"metric": ["connections.total"], "server_id": [pg]})
    assert r.status_code == 200 and r.json()["series"] == []
    cur = (await perf.get("/api/v1/metrics/current", params={"server_id": [pg, ora]})).json()
    assert set(cur["values"]) <= {ora}
    # per-server endpoint is protected too
    assert (await perf.get(f"/api/v1/servers/{pg}/metrics")).status_code in (403, 404)
    # fleet aggregation
    agg = (
        await admin.get(
            "/api/v1/metrics/range",
            params={"metric": ["connections.total"], "range": "1h", "group": "sum", "source": "simulated"},
        )
    ).json()
    assert len(agg["series"]) == 1 and agg["series"][0]["server_id"] is None


async def test_query_telemetry_endpoints(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    sid = servers["PG-PROD-01"]["id"]
    body = (await admin.get(f"/api/v1/servers/{sid}/query-stats", params={"range": "6h"})).json()
    assert body["items"], body
    first = body["items"][0]
    assert len(first["fingerprint"]) == 16 and first["query_id"] == first["fingerprint"]
    assert first["calls"] >= 0 and first["interval"]["seconds"] == 6 * 3600
    totals = [i["total_ms"] for i in body["items"]]
    assert totals == sorted(totals, reverse=True)
    hist = (await admin.get(f"/api/v1/servers/{sid}/query-stats/{first['fingerprint']}/history")).json()
    assert hist["fingerprint"] == first["fingerprint"] and hist["query_text"]
    fps = (await admin.get(f"/api/v1/servers/{sid}/query-fingerprints")).json()
    assert {"fingerprint", "normalized_query", "first_seen", "last_seen", "engine", "database"} <= set(fps[0])
    assert (await admin.get(f"/api/v1/servers/{sid}/query-stats/not-a-fp!/history")).status_code == 422
    top = (await admin.get("/api/v1/queries/top", params={"source": "all", "range": "6h"})).json()
    assert top and all("server_name" in t for t in top)
    # RBAC: a scoped user only sees statements of their servers
    perf = await make_client(("perf.oracle", DEMO_PW))
    mine = (await perf.get("/api/v1/queries/top", params={"source": "all", "range": "24h"})).json()
    assert {t["server_id"] for t in mine} <= {servers["ORA-PROD-01"]["id"]}


async def test_baseline_and_capacity_endpoints(admin, servers) -> None:  # type: ignore[no-untyped-def]
    sid = servers["PG-PROD-01"]["id"]
    b = (await admin.get(f"/api/v1/servers/{sid}/metrics/baseline", params={"metric": "cpu.percent"})).json()
    assert b["metric"] == "cpu.percent" and "sufficient_data" in b
    if b["sufficient_data"]:
        assert b["band"]["low"] <= b["mean"] <= b["band"]["high"]
    cap = (await admin.get(f"/api/v1/servers/{sid}/metrics/capacity")).json()
    # the CI fleet has one day of history: no forecast is fabricated
    assert cap["forecast"] is None and cap["days_until_full"] is None and cap["reason"]
    fleet = (await admin.get("/api/v1/metrics/capacity", params={"source": "all"})).json()
    assert all(r["forecast"] is None for r in fleet)


async def test_timeseries_outage_degrades_gracefully(admin, servers) -> None:  # type: ignore[no-untyped-def]
    r0 = repo()
    sid = servers["PG-PROD-01"]["id"]
    r0.available = False
    try:
        r = await admin.get(f"/api/v1/servers/{sid}/metrics")
        assert r.status_code == 503 and r.json()["error"]["code"] == "timeseries_unavailable"
        d = await admin.get("/api/v1/dashboard/summary", params={"source": "all"})
        assert d.status_code == 200 and d.json()["trends"].get("unavailable") is True
        assert d.json()["kpis"]["servers"] >= 9  # PostgreSQL-backed parts still render
        cur = (await admin.get("/api/v1/metrics/current", params={"source": "all"})).json()
        assert cur["source"] == "collector_cache" and cur["timeseries"] == "unavailable"
        assert (await admin.get("/api/v1/servers", params={"source": "all"})).status_code == 200
        ready = await admin.get("/readyz")
        body = ready.json()
        assert ready.status_code == 200 and body["degraded"] is True
        assert body["components"]["postgresql"]["status"] == "ok"
        assert body["components"]["timeseries"]["status"] == "unavailable"
        st = (await admin.get("/api/v1/platform/monitoring-storage")).json()
        assert st["status"] == "unavailable"
        # collection keeps working: samples are buffered by the writer, nothing raises
        from app.db.session import session_scope
        from app.models import Server
        from app.services import collection
        from app.timeseries.writer import get_writer

        async with session_scope() as db:
            s = await db.get(Server, uuid.UUID(sid))
            await collection.collect_metrics(db, s)
        assert get_writer().pending > 0
        assert await get_writer().flush_all() is False
    finally:
        r0.available = True
    from app.timeseries.writer import get_writer

    assert await get_writer().flush_all() is True and get_writer().pending == 0
    ready = (await admin.get("/readyz")).json()
    assert ready["degraded"] is False and ready["components"]["timeseries"]["status"] == "ok"


async def test_monitoring_storage_admin_and_retention_policy(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    st = (await admin.get("/api/v1/platform/monitoring-storage")).json()
    assert st["status"] == "available" and st["engine"] and st["ingestion"]["state"] in ("ok", "idle", "retrying")
    assert {"desired_days", "effective", "in_sync"} <= set(st["retention"])
    r = await admin.put("/api/v1/platform/monitoring-storage/retention", json={"days": 120})
    assert r.status_code == 200 and r.json()["retention"]["desired_days"] == 120
    assert (await admin.put("/api/v1/platform/monitoring-storage/retention", json={"days": 0})).status_code == 422
    ev = (await admin.get("/api/v1/audit", params={"action": "settings.update"})).json()["items"]
    assert any("monitoring.retention_days" in (e.get("details") or {}) for e in ev)
    monitor = await make_client(("monitor", DEMO_PW))
    assert (await monitor.put("/api/v1/platform/monitoring-storage/retention", json={"days": 7})).status_code == 403
    await admin.put("/api/v1/platform/monitoring-storage/retention", json={"days": 90})
    cat = (await admin.get("/api/v1/metrics/catalog")).json()
    assert any(c["key"] == "connections.total" and c["name"] == "db_connections" for c in cat)


async def test_server_delete_removes_its_telemetry_even_after_an_outage(admin) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import session_scope
    from app.services import collection
    from app.timeseries.model import Sample, Selector

    r = await admin.post(
        "/api/v1/servers",
        json={
            "name": "ts-delete-01",
            "engine": "postgresql",
            "host": "db.real.example",
            "port": 5432,
            "monitoring_enabled": False,
        },
    )
    sid = r.json()["id"]
    lbl = {"server_id": sid, "engine": "postgresql", "data_source": "real"}
    now = datetime.now(UTC)
    await repo().write_metrics_batch([Sample.make("db_connections", lbl, 3, now - timedelta(minutes=1))])
    repo().available = False
    try:
        d = await admin.delete(f"/api/v1/servers/{sid}", params={"confirm": "ts-delete-01"})
        assert d.status_code == 200
    finally:
        repo().available = True
    assert await repo().get_latest(Selector.of("db_connections", [sid]))  # still there: delete was queued
    st = (await admin.get("/api/v1/platform/monitoring-storage")).json()
    assert st["pending_deletes"] >= 1
    async with session_scope() as db:
        out = await collection.retention(db)
    assert out["timeseries_pending_deletes"] >= 1
    assert not await repo().get_latest(Selector.of("db_connections", [sid]))
    assert (await admin.get("/api/v1/platform/monitoring-storage")).json()["pending_deletes"] == 0


async def test_legacy_migration_is_idempotent_and_resumable(admin, servers) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import delete, insert

    from app.db.session import session_scope
    from app.models import MetricSample, QueryStatSnapshot
    from app.timeseries.migration import MigrationOptions, migrate
    from app.timeseries.model import Selector

    sid = uuid.UUID(servers["PG-DEV-01"]["id"])
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    async with session_scope() as db:
        await db.execute(delete(MetricSample))
        await db.execute(delete(QueryStatSnapshot))
        await db.execute(
            insert(MetricSample),
            [
                {
                    "server_id": sid,
                    "metric": "connections.total",
                    "ts": t0 + timedelta(minutes=i),
                    "value": float(i),
                    "labels": None,
                }
                for i in range(250)
            ]
            + [
                {"server_id": sid, "metric": "wait.active_sessions", "ts": t0, "value": 2.0, "labels": {"type": "IO"}},
                {"server_id": sid, "metric": "x.y", "ts": t0, "value": 1.0, "labels": {"session_id": "123"}},
            ],
        )
        await db.execute(
            insert(QueryStatSnapshot),
            [
                {
                    "server_id": sid,
                    "ts": t0 + timedelta(hours=h),
                    "query_id": f"q{u}",
                    "database": "app",
                    "username": f"user{u}",
                    "query_text": "SELECT * FROM t WHERE id = 1",
                    "calls": 10 * (h + 1),
                    "total_ms": 100.0 * (h + 1),
                    "mean_ms": 10.0,
                }
                for h in range(5)
                for u in range(2)
            ],
        )
    lines: list[str] = []
    dry = await migrate(session_scope, MigrationOptions(dry_run=True, restart=True), progress=lines.append)
    assert dry.metric_rows_total == 252 and dry.query_rows_total == 10 and dry.samples_written == 0
    sel = Selector.of("db_connections", [str(sid)])
    before = await repo().aggregate(sel, t0 - timedelta(minutes=1), t0 + timedelta(hours=5), "count")
    assert not before

    opts = MigrationOptions(batch_size=100, restart=True, server_id=sid)
    rep = await migrate(session_scope, opts, progress=lines.append)
    assert rep.completed and rep.metric_rows_done == 252 and rep.rows_skipped == 1  # unbounded label refused
    assert any("%" in line for line in lines)  # progress reported
    cnt = await repo().aggregate(sel, t0 - timedelta(minutes=1), t0 + timedelta(hours=5), "count")
    assert cnt[0].value == 250
    calls = await repo().aggregate(
        Selector.of("db_query_calls_total", [str(sid)]), t0 - timedelta(minutes=1), t0 + timedelta(hours=5), "max"
    )
    assert calls[0].value == 100  # two users' rows of one statement merged per snapshot
    # re-running resumes from the checkpoint: nothing left, nothing duplicated
    again = await migrate(session_scope, MigrationOptions(batch_size=100, server_id=sid), progress=lines.append)
    assert again.metric_rows_done == 0 and again.resumed_from["metric_samples"] > 0
    # a full re-run is idempotent (time-series deduplication by series + timestamp)
    await migrate(session_scope, MigrationOptions(batch_size=100, restart=True, server_id=sid), progress=lines.append)
    cnt = await repo().aggregate(sel, t0 - timedelta(minutes=1), t0 + timedelta(hours=5), "count")
    assert cnt[0].value == 250
    async with session_scope() as db:
        await db.execute(delete(MetricSample))
        await db.execute(delete(QueryStatSnapshot))


async def test_late_samples_after_a_delete_are_removed_by_the_follow_up(admin) -> None:  # type: ignore[no-untyped-def]
    """A collection already in flight (possibly in another worker) can write after the delete;
    the scheduled follow-up removes it, and only by the deleted server's id."""
    from app.db.session import session_scope
    from app.services import monitoring_storage
    from app.timeseries.model import Sample, Selector
    from app.timeseries.writer import get_writer

    gone, other = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC) - timedelta(seconds=30)

    def smp(sid: str) -> Sample:
        return Sample.make("db_up", {"server_id": sid, "engine": "postgresql", "data_source": "simulated"}, 1, now)

    await repo().write_metrics_batch([smp(gone), smp(other)])
    get_writer().submit([smp(gone)])  # buffered sample of the server being deleted
    async with session_scope() as db:
        assert await monitoring_storage.delete_series(db, {"server_id": [gone]})
    assert all(s.label_dict["server_id"] != gone for s in list(get_writer()._buf))  # discarded
    await repo().write_metrics_batch([smp(gone)])  # late write from an in-flight collection
    async with session_scope() as db:
        assert await monitoring_storage.retry_pending_deletes(db) == 0  # follow-up not due yet
        assert await monitoring_storage.retry_pending_deletes(db, now=datetime.now(UTC) + timedelta(minutes=5)) >= 1
    left = {v.labels["server_id"] for v in await repo().get_latest(Selector.of("db_up", [gone, other]))}
    assert left == {other}
    await get_writer().flush_all()
