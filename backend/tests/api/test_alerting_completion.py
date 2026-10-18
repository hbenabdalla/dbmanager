"""Alerting against the database (sprints 0.10.1–0.10.4): evaluator lifecycle on a real (not
simulated) server, maintenance windows releasing suppressed alerts, re-notification until
acknowledgement, routing, range rules over time-series history, persistent retries with backoff,
alert comments, scoped visibility, RBAC denial and audit."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")
METRIC = "connections.utilization"


@pytest.fixture
async def real_server(app):  # type: ignore[no-untyped-def]
    """A non-simulated server row (nothing connects to it: the evaluator reads cached values)."""
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models import Environment, Organization, Server, ServerTag

    async with session_scope() as db:
        org = (await db.execute(select(Organization).limit(1))).scalar_one()
        env = (await db.execute(select(Environment).where(Environment.name == "Production"))).scalar_one()
        s = Server(
            id=uuid.uuid4(),
            organization_id=org.id,
            name=f"real-alert-{uuid.uuid4().hex[:6]}",
            engine="postgresql",
            host="10.10.0.5",
            port=5432,
            environment_id=env.id,
            monitoring_enabled=True,
            latest_metrics={},
        )
        db.add(s)
        await db.flush()
        db.add(ServerTag(server_id=s.id, key="team", value="payments"))
        sid, env_id = s.id, env.id
    yield {"id": str(sid), "environment_id": str(env_id)}
    async with session_scope() as db:
        s = await db.get(Server, sid)
        if s is not None:
            await db.delete(s)


@pytest.fixture
def sent(monkeypatch):  # type: ignore[no-untyped-def]
    from app.services import notify

    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def fake_send(channel, event, alert):  # type: ignore[no-untyped-def]
        calls.append((channel.name, event, alert))
        return 200

    monkeypatch.setattr(notify, "send", fake_send)
    return calls


async def _metric(server_id: str, value: float | None, at: datetime) -> None:
    from app.db.session import session_scope
    from app.models import Server

    async with session_scope() as db:
        s = await db.get(Server, uuid.UUID(server_id))
        m = {k: v for k, v in (s.latest_metrics or {}).items() if k != METRIC}
        if value is not None:
            m[METRIC] = value
        s.latest_metrics = {**m, "_collected_at": at.isoformat()}


async def _evaluate(at: datetime) -> dict[str, int]:
    from app.db.session import session_scope
    from app.services import alerting

    async with session_scope() as db:
        return await alerting.evaluate(db, at)


async def _channel(admin, name: str, **extra: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/notification-channels",
        json={"name": name, "kind": "webhook", "config": {"url": "https://hooks.example.com/x"}, **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _rule(admin, name: str, **extra: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    body = {"name": name, "kind": "metric", "metric": METRIC, "operator": ">=", "threshold": 90, "severity": "high"}
    r = await admin.post("/api/v1/alert-rules", json={**body, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def _cleanup(admin, rules=(), channels=(), windows=()) -> None:  # type: ignore[no-untyped-def]
    for r in rules:
        await admin.delete(f"/api/v1/alert-rules/{r['id']}")
    for c in channels:
        await admin.delete(f"/api/v1/notification-channels/{c['id']}")
    for w in windows:
        await admin.delete(f"/api/v1/maintenance-windows/{w['id']}")


async def test_lifecycle_on_a_real_server(admin, real_server, sent) -> None:  # type: ignore[no-untyped-def]
    ch = await _channel(admin, "lc-hook")
    rule = await _rule(
        admin,
        "lc rule",
        for_seconds=120,
        scope_type="server",
        scope_id=real_server["id"],
        channel_ids=[ch["id"]],
        notify_on_resolve=False,
    )
    t0 = datetime.now(UTC)
    # pending that clears before for_seconds: deleted silently, nobody notified
    await _metric(real_server["id"], 95, t0)
    assert (await _evaluate(t0))["pending"] == 1
    await _metric(real_server["id"], 10, t0 + timedelta(seconds=60))
    await _evaluate(t0 + timedelta(seconds=60))
    gone = (await admin.get("/api/v1/alerts", params={"status": ["pending", "firing"], "rule_id": rule["id"]})).json()
    assert gone["total"] == 0 and sent == []

    # fires exactly when for_seconds has elapsed
    t1 = t0 + timedelta(seconds=120)
    await _metric(real_server["id"], 95, t1)
    await _evaluate(t1)
    await _metric(real_server["id"], 95, t1 + timedelta(seconds=119))
    assert (await _evaluate(t1 + timedelta(seconds=119)))["fired"] == 0
    await _metric(real_server["id"], 95, t1 + timedelta(seconds=120))
    assert (await _evaluate(t1 + timedelta(seconds=120)))["fired"] == 1
    assert [e for _, e, _ in sent] == ["firing"] and sent[0][2]["is_demo"] is False

    # no data keeps the state; condition false resolves without a resolve notification
    await _metric(real_server["id"], None, t1 + timedelta(seconds=180))
    await _evaluate(t1 + timedelta(seconds=180))
    firing = (await admin.get("/api/v1/alerts", params={"rule_id": rule["id"]})).json()
    assert firing["total"] == 1 and firing["items"][0]["status"] == "firing"
    await _metric(real_server["id"], 10, t1 + timedelta(seconds=240))
    assert (await _evaluate(t1 + timedelta(seconds=240)))["resolved"] == 1
    assert [e for _, e, _ in sent] == ["firing"]  # notify_on_resolve = false
    await _cleanup(admin, [rule], [ch])


async def test_window_suppresses_then_releases_and_renotify_stops_on_ack(admin, real_server, sent) -> None:  # type: ignore[no-untyped-def]
    ch = await _channel(admin, "mw-hook")
    rule = await _rule(
        admin, "mw rule", scope_type="tag", scope_id="team=payments", channel_ids=[ch["id"]], renotify_minutes=10
    )
    now = datetime.now(UTC)
    r = await admin.post(
        "/api/v1/maintenance-windows",
        json={
            "name": "mw",
            "scope_type": "server",
            "scope_id": real_server["id"],
            "starts_at": (now - timedelta(minutes=5)).isoformat(),
            "ends_at": (now + timedelta(minutes=30)).isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    window = r.json()
    await _metric(real_server["id"], 99, now)
    st = await _evaluate(now)
    assert st["fired"] == 1 and st["suppressed"] == 1 and sent == []
    alert = (await admin.get("/api/v1/alerts", params={"rule_id": rule["id"]})).json()["items"][0]
    assert alert["suppressed"] is True and alert["notified_at"] is None
    detail = (await admin.get(f"/api/v1/alerts/{alert['id']}")).json()
    assert detail["notifications"][0]["status"] == "skipped"

    # window over, alert still firing: the firing notification goes out now
    after = now + timedelta(minutes=31)
    await _metric(real_server["id"], 99, after)
    st = await _evaluate(after)
    assert st["released"] == 1 and [e for _, e, _ in sent] == ["firing"]
    # re-notification every 10 minutes while unacknowledged
    await _metric(real_server["id"], 99, after + timedelta(minutes=10))
    assert (await _evaluate(after + timedelta(minutes=10)))["renotified"] == 1
    assert sent[-1][2]["renotify"] is True and sent[-1][2]["notify_count"] == 2
    # acknowledging stops re-notification (and is audited)
    r = await admin.post(f"/api/v1/alerts/{alert['id']}/acknowledge", json={"comment": "investigating"})
    assert r.status_code == 200
    await _metric(real_server["id"], 99, after + timedelta(minutes=30))
    assert (await _evaluate(after + timedelta(minutes=30)))["renotified"] == 0
    assert len(sent) == 2

    # comments on the alert
    r = await admin.post(f"/api/v1/alerts/{alert['id']}/comments", json={"body": "failover scheduled"})
    assert r.status_code == 201
    detail = (await admin.get(f"/api/v1/alerts/{alert['id']}")).json()
    assert [c["body"] for c in detail["comments"]] == ["failover scheduled"]
    by_server = (await admin.get("/api/v1/alerts/summary", params={"by_server": True})).json()["by_server"]
    assert by_server[real_server["id"]]["active"] == 1 and by_server[real_server["id"]]["unacknowledged"] == 0
    await _cleanup(admin, [rule], [ch], [window])


async def test_routing_subscribe_all_and_simulated_policy(admin, real_server, servers, sent) -> None:  # type: ignore[no-untyped-def]
    prod = await _channel(
        admin, "route-prod", routes={"subscribe_all": True, "environment_ids": [real_server["environment_id"]]}
    )
    web = await _channel(admin, "route-web", routes={"subscribe_all": True, "tags": ["team=web"]})
    sim = await _channel(admin, "route-sim", routes={"subscribe_all": True}, send_for_simulated=False)
    rule = await _rule(admin, "route rule")  # lists no channel: routing decides
    now = datetime.now(UTC)
    await _metric(real_server["id"], 97, now)
    await _metric(servers["PG-PROD-01"]["id"], 97, now)
    await _evaluate(now)
    names = sorted(n for n, _, a in sent if a and a["server_id"] == real_server["id"])
    assert names == ["route-prod", "route-sim"]  # route-web: tag filter does not match
    # the simulated server's alert is not sent by a channel without send_for_simulated
    assert not [n for n, _, a in sent if a and a["server_id"] == servers["PG-PROD-01"]["id"] and n == "route-sim"]
    deliveries = (await admin.get(f"/api/v1/notification-channels/{sim['id']}/deliveries")).json()
    assert any(d["status"] == "skipped" and "simulated" in (d["error"] or "") for d in deliveries)
    await _cleanup(admin, [rule], [prod, web, sim])
    await _metric(servers["PG-PROD-01"]["id"], 50, now)


async def test_range_rule_reads_time_series_history(admin, real_server, sent) -> None:  # type: ignore[no-untyped-def]
    from app.timeseries.catalog import name_for
    from app.timeseries.model import Sample
    from app.timeseries.repository import get_repository

    now = datetime.now(UTC)
    labels = {"server_id": real_server["id"], "engine": "postgresql", "data_source": "real"}
    samples = [
        Sample.make(name_for(METRIC), labels, v, now - timedelta(seconds=s))
        for s, v in ((540, 70.0), (360, 95.0), (180, 98.0), (30, 99.0))
    ]
    await get_repository().write_metrics_batch(samples)
    r = await admin.post(
        "/api/v1/alert-rules",
        json={
            "name": "range",
            "kind": "metric",
            "metric": METRIC,
            "operator": ">",
            "threshold": 85,
            "aggregation": "avg",
            "window_seconds": 60,
            "severity": "low",
            "scope_type": "server",
            "scope_id": real_server["id"],
        },
    )
    assert r.status_code == 201, r.text
    rule = r.json()
    assert (await _evaluate(now))["fired"] == 1  # avg over the last minute = 99
    alert = (await admin.get("/api/v1/alerts", params={"rule_id": rule["id"]})).json()["items"][0]
    assert alert["message"].startswith("avg(connections.utilization, 1m) = 99")
    bad = await admin.put(f"/api/v1/alert-rules/{rule['id']}", json={**rule, "window_seconds": 30})
    assert bad.status_code == 422  # avg/min/max need at least a minute of history
    await _cleanup(admin, [rule])


async def test_failed_deliveries_are_retried_with_backoff(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.db.session import session_scope
    from app.models import NotificationChannel
    from app.services import alerting, notify

    outcomes = [notify.DeliveryError("busy", 503), notify.DeliveryError("still busy", 502), 200]

    async def flaky(channel, event, alert):  # type: ignore[no-untyped-def]
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o

    monkeypatch.setattr(notify, "send", flaky)
    ch = await _channel(admin, "retry-hook")
    t0 = datetime.now(UTC)
    async with session_scope() as db:
        channel = await db.get(NotificationChannel, uuid.UUID(ch["id"]))
        d = await alerting.deliver(db, channel, "firing", None, {"title": "x"}, now=t0)
        await db.flush()
        assert d.status == "retrying" and d.attempts == 1 and d.next_attempt_at == t0 + timedelta(seconds=60)
    async with session_scope() as db:
        assert (await alerting.retry_due(db, t0 + timedelta(seconds=30)))["due"] == 0  # not yet
    async with session_scope() as db:
        st = await alerting.retry_due(db, t0 + timedelta(seconds=61))
        assert st["due"] == 1 and st["retrying"] == 1  # second attempt failed: next in 300 s
    async with session_scope() as db:
        st = await alerting.retry_due(db, t0 + timedelta(seconds=61 + 301))
        assert st["sent"] == 1
    log = (await admin.get(f"/api/v1/notification-channels/{ch['id']}/deliveries")).json()
    assert log[0]["status"] == "sent" and log[0]["attempts"] == 3 and log[0]["next_attempt_at"] is None
    await _cleanup(admin, channels=[ch])


async def test_rbac_denial_scoped_lists_and_audit(admin, make_client, servers) -> None:  # type: ignore[no-untyped-def]
    # a user with no role at all sees nothing
    name = f"noroles.{uuid.uuid4().hex[:6]}"
    r = await admin.post("/api/v1/users", json={"username": name, "password": DEMO_PW, "must_change_password": False})
    assert r.status_code == 201, r.text
    nobody = await make_client((name, DEMO_PW))
    for path in ("/api/v1/alerts", "/api/v1/alert-rules", "/api/v1/maintenance-windows"):
        assert (await nobody.get(path)).status_code == 403, path
    assert (await nobody.get("/api/v1/alerts/summary")).json()["visible"] is False

    monitor = await make_client(("monitor", DEMO_PW))
    now = datetime.now(UTC)
    win = {"name": "rbac", "starts_at": now.isoformat(), "ends_at": (now + timedelta(hours=1)).isoformat()}
    assert (await monitor.post("/api/v1/maintenance-windows", json=win)).status_code == 403
    ch = await _channel(admin, "rbac-hook")
    assert (await monitor.post(f"/api/v1/notification-channels/{ch['id']}/test")).status_code == 403

    # perf.oracle (scoped to ORA-PROD-01) sees rules that can apply to its server only
    ora = await _rule(admin, "scoped-ora", scope_type="server", scope_id=servers["ORA-PROD-01"]["id"])
    pg = await _rule(admin, "scoped-pg", scope_type="server", scope_id=servers["PG-PROD-01"]["id"])
    perf = await make_client(("perf.oracle", DEMO_PW))
    seen = {x["name"] for x in (await perf.get("/api/v1/alert-rules", params={"page_size": 500})).json()["items"]}
    assert "scoped-ora" in seen and "scoped-pg" not in seen

    # audited configuration changes
    w = (await admin.post("/api/v1/maintenance-windows", json=win)).json()
    assert (await admin.put(f"/api/v1/maintenance-windows/{w['id']}", json={**win, "name": "rbac2"})).status_code == 200
    await admin.put(
        f"/api/v1/notification-channels/{ch['id']}",
        json={"name": "rbac-hook", "kind": "webhook", "config": {"url": "https://hooks.example.com/y"}},
    )
    await _cleanup(admin, [ora, pg], [ch], [w])
    events = (await admin.get("/api/v1/audit", params={"page_size": 200, "category": "alerting"})).json()["items"]
    actions = {e["action"] for e in events}
    for a in (
        "maintenance_window.create",
        "maintenance_window.update",
        "maintenance_window.delete",
        "notification_channel.create",
        "notification_channel.update",
        "notification_channel.delete",
    ):
        assert a in actions, a


async def test_recurring_window_api(admin, real_server) -> None:  # type: ignore[no-untyped-def]
    start = datetime(2026, 10, 19, 22, 0, tzinfo=UTC)
    base = {"name": "nightly", "starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=2)).isoformat()}
    too_long = {**base, "recurrence": "daily", "ends_at": (start + timedelta(hours=25)).isoformat()}
    assert (await admin.post("/api/v1/maintenance-windows", json=too_long)).status_code == 422
    bad_tz = {**base, "recurrence": "daily", "time_zone": "Mars/Olympus"}
    assert (await admin.post("/api/v1/maintenance-windows", json=bad_tz)).status_code == 422
    naive = {**base, "starts_at": "2026-10-19T22:00:00"}
    assert (await admin.post("/api/v1/maintenance-windows", json=naive)).status_code == 422
    r = await admin.post(
        "/api/v1/maintenance-windows",
        json={
            **base,
            "recurrence": "weekly",
            "time_zone": "Europe/Berlin",
            "scope_type": "environment",
            "scope_id": real_server["environment_id"],
        },
    )
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["weekdays"] == [2] and w["state"] in ("scheduled", "active")  # 22:00 UTC Mon = Tue 00:00 Berlin
    listed = (await admin.get("/api/v1/maintenance-windows", params={"server_id": real_server["id"]})).json()
    assert any(x["id"] == w["id"] for x in listed["items"])
    await admin.delete(f"/api/v1/maintenance-windows/{w['id']}")
