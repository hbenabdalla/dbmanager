"""Alerting end to end: channels (secret masking), rules, evaluation lifecycle, dedupe,
acknowledgement, auto-resolve, maintenance-window suppression, RBAC."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")
SECRET = "Hook-Signing-Secret-42"
METRIC = "connections.utilization"


async def _set_metric(server_id: str, value: float, at: datetime) -> None:
    from app.db.session import session_scope
    from app.models import Server

    async with session_scope() as db:
        s = await db.get(Server, uuid.UUID(server_id))
        s.monitoring_enabled = True
        s.latest_metrics = {**(s.latest_metrics or {}), METRIC: value, "_collected_at": at.isoformat()}


async def _evaluate(at: datetime) -> dict[str, int]:
    from app.db.session import session_scope
    from app.services import alerting

    async with session_scope() as db:
        return await alerting.evaluate(db, at)


async def test_alerting_lifecycle(admin, make_client, servers, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services import notify

    sent: list[tuple[str, str, dict[str, Any] | None]] = []

    async def fake_send(channel, event, alert):  # type: ignore[no-untyped-def]
        sent.append((channel.name, event, alert))
        return 200

    monkeypatch.setattr(notify, "send", fake_send)
    target = servers["PG-PROD-01"]

    # channel: the signing secret is stored but never returned
    r = await admin.post(
        "/api/v1/notification-channels",
        json={
            "name": "it-webhook",
            "kind": "webhook",
            "config": {"url": "https://hooks.example.com/dbm"},
            "signing_secret": SECRET,
            "send_for_simulated": True,
        },
    )
    assert r.status_code == 201, r.text
    assert SECRET not in r.text and r.json()["has_signing_secret"] is True
    channel = r.json()
    listed = await admin.get("/api/v1/notification-channels")
    assert SECRET not in listed.text
    bad = await admin.post(
        "/api/v1/notification-channels",
        json={"name": "bad", "kind": "webhook", "config": {"url": "https://x.example.com", "token": "abc"}},
    )
    assert bad.status_code == 422

    # rule with a for-duration, scoped to one (simulated) server
    r = await admin.post(
        "/api/v1/alert-rules",
        json={
            "name": "it connection pressure",
            "kind": "metric",
            "metric": METRIC,
            "operator": ">=",
            "threshold": 90,
            "for_seconds": 120,
            "severity": "high",
            "scope_type": "server",
            "scope_id": target["id"],
            "channel_ids": [channel["id"]],
        },
    )
    assert r.status_code == 201, r.text
    rule = r.json()
    unknown = await admin.post(
        "/api/v1/alert-rules",
        json={
            "name": "x",
            "kind": "metric",
            "metric": "no.such.metric",
            "operator": ">",
            "threshold": 1,
            "severity": "low",
        },
    )
    assert unknown.status_code == 422

    t0 = datetime.now(UTC)
    await _set_metric(target["id"], 95.0, t0)
    st = await _evaluate(t0)
    assert st["pending"] == 1 and st["fired"] == 0 and not sent
    alerts = (await admin.get("/api/v1/alerts", params={"status": ["pending"], "rule_id": rule["id"]})).json()
    assert alerts["total"] == 1 and alerts["items"][0]["is_demo"] is True

    t1 = t0 + timedelta(seconds=130)
    await _set_metric(target["id"], 96.0, t1)
    st = await _evaluate(t1)
    assert st["fired"] == 1 and [e for _, e, _ in sent] == ["firing"]
    assert sent[0][2]["server_name"] == "PG-PROD-01" and sent[0][2]["value"] == 96.0
    # dedupe: still one active alert, no repeat notification
    await _evaluate(t1 + timedelta(seconds=60))
    active = (await admin.get("/api/v1/alerts", params={"rule_id": rule["id"]})).json()
    assert active["total"] == 1 and len(sent) == 1
    alert = active["items"][0]
    summary = (await admin.get("/api/v1/alerts/summary")).json()
    assert summary["active"] >= 1 and summary["by_severity"].get("high", 0) >= 1

    # RBAC: the monitoring user sees the alert but cannot acknowledge or manage
    monitor = await make_client(("monitor", DEMO_PW))
    seen = (await monitor.get("/api/v1/alerts", params={"rule_id": rule["id"]})).json()
    assert seen["total"] == 1
    assert (await monitor.post(f"/api/v1/alerts/{alert['id']}/acknowledge", json={})).status_code == 403
    assert (
        await monitor.post(
            "/api/v1/alert-rules",
            json={**{k: rule[k] for k in ("kind", "metric", "operator", "threshold", "severity")}, "name": "nope"},
        )
    ).status_code == 403
    assert (await monitor.get("/api/v1/notification-channels")).status_code == 403
    # a user without alerts.view at all (Log Viewer only) sees nothing
    perf = await make_client(("perf.oracle", DEMO_PW))
    other = (await perf.get("/api/v1/alerts", params={"rule_id": rule["id"]})).json()
    assert other["total"] == 0  # ORA-PROD-01 scope does not include PG-PROD-01

    r = await admin.post(f"/api/v1/alerts/{alert['id']}/acknowledge", json={"comment": "looking"})
    assert r.status_code == 200 and r.json()["status"] == "acknowledged" and r.json()["acknowledged_by"] == "admin"
    assert (await admin.post(f"/api/v1/alerts/{alert['id']}/acknowledge", json={})).status_code == 409

    # condition clears: auto-resolve + resolve notification
    t2 = t1 + timedelta(seconds=180)
    await _set_metric(target["id"], 40.0, t2)
    st = await _evaluate(t2)
    assert st["resolved"] == 1 and sent[-1][1] == "resolved"
    resolved = (await admin.get("/api/v1/alerts", params={"rule_id": rule["id"], "status": ["resolved"]})).json()
    assert resolved["items"][0]["resolved_by"] == "auto"

    # maintenance window: the next alert is suppressed and no notification goes out
    r = await admin.post(
        "/api/v1/maintenance-windows",
        json={
            "name": "it patching",
            "scope_type": "server",
            "scope_id": target["id"],
            "starts_at": (t2 - timedelta(minutes=1)).isoformat(),
            "ends_at": (t2 + timedelta(hours=2)).isoformat(),
        },
    )
    assert r.status_code == 201, r.text
    window = r.json()
    n_before = len(sent)
    t3 = t2 + timedelta(seconds=30)
    await _set_metric(target["id"], 99.0, t3)
    await _evaluate(t3)
    t4 = t3 + timedelta(seconds=130)
    await _set_metric(target["id"], 99.0, t4)
    st = await _evaluate(t4)
    assert st["fired"] == 1 and st["suppressed"] == 1 and len(sent) == n_before
    firing = (await admin.get("/api/v1/alerts", params={"rule_id": rule["id"]})).json()["items"][0]
    assert firing["suppressed"] is True
    log = (await admin.get(f"/api/v1/notification-channels/{channel['id']}/deliveries")).json()
    assert log[0]["status"] == "skipped" and "maintenance" in log[0]["error"]
    assert any(d["status"] == "sent" and d["event"] == "resolved" for d in log)

    # disabling the rule closes its alerts; audit trail records the changes
    upd = {
        k: rule[k]
        for k in (
            "name",
            "kind",
            "metric",
            "operator",
            "threshold",
            "for_seconds",
            "severity",
            "scope_type",
            "scope_id",
            "channel_ids",
        )
    }
    r = await admin.put(f"/api/v1/alert-rules/{rule['id']}", json={**upd, "enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert (await admin.get("/api/v1/alerts", params={"rule_id": rule["id"]})).json()["total"] == 0
    audit = (await admin.get("/api/v1/audit", params={"q": "alert_rule"})).json()
    assert any(e["action"] == "alert_rule.create" for e in audit["items"])

    # clean up
    assert (await admin.delete(f"/api/v1/maintenance-windows/{window['id']}")).status_code == 200
    assert (await admin.delete(f"/api/v1/alert-rules/{rule['id']}")).status_code == 200
    assert (await admin.delete(f"/api/v1/notification-channels/{channel['id']}")).status_code == 200


async def test_channel_test_endpoint_and_simulated_policy(admin, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.services import notify

    async def failing(channel, event, alert):  # type: ignore[no-untyped-def]
        raise notify.DeliveryError("receiver answered HTTP 503", 503)

    monkeypatch.setattr(notify, "send", failing)
    r = await admin.post(
        "/api/v1/notification-channels",
        json={
            "name": "it-mail",
            "kind": "email",
            "config": {"recipients": ["dba@example.com"]},
            "smtp_password": "Mail-Secret-9",
        },
    )
    assert r.status_code == 201 and "Mail-Secret-9" not in r.text and r.json()["has_smtp_password"] is True
    ch = r.json()
    t = (await admin.post(f"/api/v1/notification-channels/{ch['id']}/test")).json()
    assert t["status"] == "failed" and t["attempts"] == 2 and "503" in t["error"]
    # keep the stored password when it is omitted on update
    r = await admin.put(
        f"/api/v1/notification-channels/{ch['id']}",
        json={"name": "it-mail", "kind": "email", "config": {"recipients": ["dba@example.com", "ops@example.com"]}},
    )
    assert r.json()["has_smtp_password"] is True and r.json()["config"]["recipients"][1] == "ops@example.com"
    assert (await admin.delete(f"/api/v1/notification-channels/{ch['id']}")).status_code == 200


async def test_about_and_version(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    anon = await make_client()
    v = (await anon.get("/api/v1/version")).json()
    assert v == {"product": "DB Manager", "version": v["version"]} and v["version"]
    assert (await anon.get("/api/v1/about")).status_code == 401
    a = (await admin.get("/api/v1/about")).json()
    from app import __version__

    head = _alembic_head()
    assert a["version"]["application"] == __version__ and a["version"]["schema"] == head
    assert a["links"]["github"] == "https://github.com/hbenabdalla/dbmanager"
    assert (
        a["links"]["roadmap"].endswith("/ROADMAP.md")
        and a["links"]["documentation"] == "https://db-manager.readthedocs.io/en/latest/"
    )
    assert a["license"] == "Apache-2.0" and a["milestone"]["version"] == "v0.11"
    engines = {e["engine"]: e for e in a["engines"]}
    assert "demo" not in engines and engines["postgresql"]["validation"] == "validated"
    assert engines["db2zos"]["support_level"] == "partial"
    assert a["status"]["database"] == "ok"


def _alembic_head() -> str | None:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    base = Path(__file__).resolve().parents[2]
    cfg = Config(str(base / "alembic.ini"))
    cfg.set_main_option("script_location", str(base / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()
