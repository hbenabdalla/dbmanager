"""Reporting (milestone 0.14): the report catalog, CSV and server-side PDF downloads with
parameters, previews, RBAC scoping, and scheduled reports delivered by e-mail to a real local
SMTP server (aiosmtpd) with the owner's access, run history and audit."""

from __future__ import annotations

import email
import socket

import pytest

aiosmtpd = pytest.importorskip("aiosmtpd")
from aiosmtpd.controller import Controller  # noqa: E402

from tests.api.conftest import DEMO_PW  # noqa: E402

pytestmark = pytest.mark.asyncio

NEW_REPORTS = {"security-findings", "query-statistics", "query-regressions", "capacity-forecast"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Inbox:
    def __init__(self) -> None:
        self.messages: list[tuple[list[str], bytes]] = []

    async def handle_DATA(self, server, session, envelope):  # type: ignore[no-untyped-def] # noqa: N802
        self.messages.append((list(envelope.rcpt_tos), envelope.content))
        return "250 OK"


@pytest.fixture
def smtp():  # type: ignore[no-untyped-def]
    inbox = Inbox()
    port = _free_port()
    ctl = Controller(inbox, hostname="127.0.0.1", port=port)
    ctl.start()
    try:
        yield inbox, port
    finally:
        ctl.stop()


async def _mail_channel(admin, port: int, name: str) -> dict:  # type: ignore[no-untyped-def]
    r = await admin.post(
        "/api/v1/notification-channels",
        json={
            "name": name,
            "kind": "email",
            "config": {
                "recipients": ["dba@example.com", "ops@example.com"],
                "smtp_host": "127.0.0.1",
                "smtp_port": port,
                "security": "none",
                "from": "dbmanager@example.com",
            },
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _attachment(raw: bytes) -> tuple[str, bytes]:
    msg = email.message_from_bytes(raw)
    for part in msg.walk():
        if part.get_filename():
            return part.get_filename(), part.get_payload(decode=True)
    raise AssertionError("no attachment")


async def test_catalog_lists_new_reports_with_formats_and_params(admin) -> None:  # type: ignore[no-untyped-def]
    cat = {r["key"]: r for r in (await admin.get("/api/v1/reports")).json()}
    assert NEW_REPORTS <= set(cat)
    for r in cat.values():
        assert r["formats"] == ["csv", "pdf"] and r["available"] is True
    assert {"statuses", "min_severity"} <= set(cat["security-findings"]["params"])
    assert "window_hours" in cat["query-statistics"]["params"]


async def test_every_report_downloads_as_csv_and_pdf(admin, servers) -> None:  # type: ignore[no-untyped-def]
    for r in (await admin.get("/api/v1/reports")).json():
        csv = await admin.get(f"/api/v1/reports/{r['key']}.csv")
        assert csv.status_code == 200, (r["key"], csv.text)
        assert csv.headers["content-type"].startswith("text/csv")
        pdf = await admin.get(f"/api/v1/reports/{r['key']}.pdf")
        assert pdf.status_code == 200, (r["key"], pdf.text)
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF") and pdf.content.rstrip().endswith(b"%%EOF")
        assert f"{r['key']}" in pdf.headers["content-disposition"]
    assert (await admin.get("/api/v1/reports/nope.csv")).status_code == 404
    assert (await admin.get("/api/v1/reports/fleet-health.docx")).status_code == 422


async def test_parameters_filter_and_validate(admin, servers) -> None:  # type: ignore[no-untyped-def]
    ora = servers["ORA-PROD-01"]
    all_rows = (await admin.get("/api/v1/reports/security-findings/preview")).json()
    one = (await admin.get("/api/v1/reports/security-findings/preview", params={"server_id": ora["id"]})).json()
    assert one["total_rows"] <= all_rows["total_rows"]
    assert any("ORA-PROD-01" in s for s in one["scope"])
    server_col = one["columns"].index("server")
    assert all(row[server_col] == "ORA-PROD-01" for row in one["rows"])
    crit = (await admin.get("/api/v1/reports/security-findings/preview", params={"min_severity": "critical"})).json()
    sev = crit["columns"].index("severity")
    assert all(row[sev] == "critical" for row in crit["rows"])
    pg = (await admin.get("/api/v1/reports/query-statistics/preview", params={"engine": "postgresql"})).json()
    eng = pg["columns"].index("engine")
    assert pg["rows"] and all(row[eng] == "PostgreSQL" for row in pg["rows"])
    assert len(pg["rows"]) <= 50
    bad = await admin.get("/api/v1/reports/security-findings.csv", params={"min_severity": "apocalyptic"})
    assert bad.status_code == 422
    assert (await admin.get("/api/v1/reports/query-statistics.csv", params={"window_hours": 0})).status_code == 422


async def test_reports_respect_scope_and_permissions(make_client, admin) -> None:  # type: ignore[no-untyped-def]
    monitor = await make_client(("monitor", DEMO_PW))
    assert (await monitor.get("/api/v1/reports/security-findings.csv")).status_code == 403
    assert (await monitor.get("/api/v1/report-schedules")).status_code == 403
    perf = await make_client(("perf.oracle", DEMO_PW))
    cat = {r["key"]: r for r in (await perf.get("/api/v1/reports")).json()}
    assert cat["query-statistics"]["available"] is True
    rows = (await perf.get("/api/v1/reports/query-statistics/preview")).json()
    srv = rows["columns"].index("server")
    assert {r[srv] for r in rows["rows"]} <= {"ORA-PROD-01"}
    assert (await perf.get("/api/v1/report-schedules")).status_code == 403
    # the audit report is platform-only
    assert (await perf.get("/api/v1/reports/audit-activity.csv")).status_code == 403
    # downloads are audited
    events = (await admin.get("/api/v1/audit", params={"action": "report.download", "page_size": 5})).json()
    assert events["items"] and events["items"][0]["action"] == "report.download"


async def test_query_text_is_masked_in_query_statistics(admin) -> None:  # type: ignore[no-untyped-def]
    csv = (await admin.get("/api/v1/reports/query-statistics.csv")).text
    assert "Passw0rd" not in csv and "Mail-Secret" not in csv


async def test_schedule_lifecycle_delivers_pdf_by_email(admin, servers, smtp) -> None:  # type: ignore[no-untyped-def]
    inbox, port = smtp
    ch = await _mail_channel(admin, port, "reports-mail")
    body = {
        "name": "weekly security",
        "report_key": "security-findings",
        "format": "pdf",
        "params": {"min_severity": "medium"},
        "schedule": {"frequency": "weekly", "time": "06:30", "time_zone": "Europe/Paris", "weekday": 1},
        "channel_ids": [ch["id"]],
    }
    r = await admin.post("/api/v1/report-schedules", json=body)
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["owner"] == "admin" and s["next_run_at"] and s["report_title"]
    assert (await admin.post("/api/v1/report-schedules", json=body)).status_code == 409

    run = (await admin.post(f"/api/v1/report-schedules/{s['id']}/run")).json()
    assert run["status"] == "success", run
    assert run["trigger"] == "manual" and run["size_bytes"] > 0
    assert run["deliveries"][0]["status"] == "sent" and run["deliveries"][0]["recipients"] == 2
    assert len(inbox.messages) == 1
    rcpts, raw = inbox.messages[0]
    assert sorted(rcpts) == ["dba@example.com", "ops@example.com"]
    msg = email.message_from_bytes(raw)
    assert "Security findings" in msg["Subject"] and "weekly security" in msg["Subject"]
    name, data = _attachment(raw)
    assert name.endswith(".pdf") and data.startswith(b"%PDF")

    runs = (await admin.get(f"/api/v1/report-schedules/{s['id']}/runs")).json()
    assert runs[0]["id"] == run["id"]
    listed = {x["id"]: x for x in (await admin.get("/api/v1/report-schedules")).json()}
    assert listed[s["id"]]["last_status"] == "success"

    # CSV variant via update
    body2 = {**body, "format": "csv", "name": "weekly security csv"}
    up = await admin.put(f"/api/v1/report-schedules/{s['id']}", json=body2)
    assert up.status_code == 200 and up.json()["format"] == "csv"
    await admin.post(f"/api/v1/report-schedules/{s['id']}/run")
    name, data = _attachment(inbox.messages[-1][1])
    assert name.endswith(".csv") and data.decode().startswith("server,")

    audit = (await admin.get("/api/v1/audit", params={"action": "report.delivered", "page_size": 5})).json()
    assert audit["items"] and audit["items"][0]["target_id"] == s["id"]
    assert (await admin.delete(f"/api/v1/report-schedules/{s['id']}")).status_code == 200
    assert (await admin.get(f"/api/v1/report-schedules/{s['id']}/runs")).status_code == 404
    await admin.delete(f"/api/v1/notification-channels/{ch['id']}")


async def test_schedule_validation(admin, smtp) -> None:  # type: ignore[no-untyped-def]
    _, port = smtp
    ch = await _mail_channel(admin, port, "reports-mail-2")
    hook = await admin.post(
        "/api/v1/notification-channels",
        json={"name": "reports-hook", "kind": "webhook", "config": {"url": "https://hooks.example.com/x"}},
    )
    assert hook.status_code == 201, hook.text
    base = {
        "name": "bad",
        "report_key": "fleet-health",
        "schedule": {"frequency": "daily", "time": "07:00"},
        "channel_ids": [ch["id"]],
    }
    cases = [
        {"report_key": "nope"},
        {"schedule": {"frequency": "hourly"}},
        {"schedule": {"frequency": "daily", "time": "25:00"}},
        {"schedule": {"frequency": "daily", "time_zone": "Mars/Olympus"}},
        {"schedule": {"frequency": "monthly", "day_of_month": 31}},
        {"channel_ids": []},
        {"format": "xlsx"},
    ]
    for override in cases:
        r = await admin.post("/api/v1/report-schedules", json={**base, **override})
        assert r.status_code == 422, (override, r.text)
    r = await admin.post("/api/v1/report-schedules", json={**base, "channel_ids": [hook.json()["id"]]})
    assert r.status_code == 422 and "e-mail" in r.text
    await admin.delete(f"/api/v1/notification-channels/{hook.json()['id']}")
    await admin.delete(f"/api/v1/notification-channels/{ch['id']}")


async def test_disabled_owner_fails_the_run_without_sending(admin, smtp) -> None:  # type: ignore[no-untyped-def]
    inbox, port = smtp
    ch = await _mail_channel(admin, port, "reports-mail-3")
    users = (await admin.get("/api/v1/users", params={"page_size": 200})).json()
    items = users["items"] if isinstance(users, dict) else users
    auditor = next(u for u in items if u["username"] == "auditor")
    r = await admin.post(
        "/api/v1/report-schedules",
        json={
            "name": "auditor posture",
            "report_key": "security-posture",
            "format": "csv",
            "schedule": {"frequency": "monthly", "time": "05:00", "day_of_month": 2},
            "channel_ids": [ch["id"]],
            "owner_id": auditor["id"],
        },
    )
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["owner"] == "auditor"
    ok = (await admin.post(f"/api/v1/report-schedules/{s['id']}/run")).json()
    assert ok["status"] == "success"
    sent = len(inbox.messages)

    from app.db.session import session_scope
    from app.models import User

    async with session_scope() as db:
        u = await db.get(User, auditor["id"])
        u.is_active = False
    try:
        bad = (await admin.post(f"/api/v1/report-schedules/{s['id']}/run")).json()
        assert bad["status"] == "failed" and "disabled" in bad["error"]
        assert len(inbox.messages) == sent
    finally:
        async with session_scope() as db:
            u = await db.get(User, auditor["id"])
            u.is_active = True
    await admin.delete(f"/api/v1/report-schedules/{s['id']}")
    await admin.delete(f"/api/v1/notification-channels/{ch['id']}")


async def test_scheduler_job_runs_due_schedules(admin, smtp) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from app.db.session import session_scope
    from app.models import ReportSchedule
    from app.services import report_schedules

    inbox, port = smtp
    ch = await _mail_channel(admin, port, "reports-mail-4")
    s = (
        await admin.post(
            "/api/v1/report-schedules",
            json={
                "name": "daily fleet",
                "report_key": "fleet-health",
                "format": "csv",
                "schedule": {"frequency": "daily", "time": "07:00"},
                "channel_ids": [ch["id"]],
            },
        )
    ).json()
    async with session_scope() as db:
        sched = await db.get(ReportSchedule, s["id"])
        sched.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    before = len(inbox.messages)
    async with session_scope() as db:
        stats = await report_schedules.due(db)
    assert stats["ran"] >= 1
    assert len(inbox.messages) == before + 1
    runs = (await admin.get(f"/api/v1/report-schedules/{s['id']}/runs")).json()
    assert runs[0]["trigger"] == "schedule" and runs[0]["status"] == "success"
    nxt = (await admin.get("/api/v1/report-schedules")).json()
    assert next(x for x in nxt if x["id"] == s["id"])["next_run_at"] > runs[0]["started_at"]
    # nothing is due now: a second pass sends nothing
    async with session_scope() as db:
        await report_schedules.due(db)
    assert len(inbox.messages) == before + 1
    await admin.delete(f"/api/v1/report-schedules/{s['id']}")
    await admin.delete(f"/api/v1/notification-channels/{ch['id']}")


async def _scheduler_user(admin) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """A user who may schedule reports but not manage users."""
    import uuid as _uuid

    roles = {r["name"]: r["id"] for r in (await admin.get("/api/v1/roles")).json()}
    if "Report Scheduler" not in roles:
        r = await admin.post(
            "/api/v1/roles",
            json={"name": "Report Scheduler", "permissions": ["reports.view", "reports.schedule", "server.view"]},
        )
        assert r.status_code == 201, r.text
        roles["Report Scheduler"] = r.json()["id"]
    name, pw = f"sched.{_uuid.uuid4().hex[:6]}", "Sched-Passw0rd!2026"
    uid = (
        await admin.post("/api/v1/users", json={"username": name, "password": pw, "must_change_password": False})
    ).json()["id"]
    r = await admin.post(
        "/api/v1/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": uid,
            "role_id": roles["Report Scheduler"],
            "scope_type": "platform",
        },
    )
    assert r.status_code == 201, r.text
    return name, pw


async def test_only_owner_creator_or_user_admin_change_a_schedule(admin, make_client, smtp) -> None:  # type: ignore[no-untyped-def]
    _, port = smtp
    ch = await _mail_channel(admin, port, "reports-mail-5")
    body = {
        "report_key": "fleet-health",
        "format": "csv",
        "schedule": {"frequency": "daily", "time": "07:00"},
        "channel_ids": [ch["id"]],
    }
    theirs = (await admin.post("/api/v1/report-schedules", json={**body, "name": "admin's report"})).json()
    name, pw = await _scheduler_user(admin)
    other = await make_client((name, pw))
    # sees the schedule, but cannot send, change or delete one owned and created by someone else
    assert any(x["id"] == theirs["id"] for x in (await other.get("/api/v1/report-schedules")).json())
    assert (await other.post(f"/api/v1/report-schedules/{theirs['id']}/run")).status_code == 403
    assert (await other.put(f"/api/v1/report-schedules/{theirs['id']}", json={**body, "name": "x"})).status_code == 403
    assert (await other.delete(f"/api/v1/report-schedules/{theirs['id']}")).status_code == 403
    mine = await other.post("/api/v1/report-schedules", json={**body, "name": f"{name} report"})
    assert mine.status_code == 201, mine.text
    assert (await other.post(f"/api/v1/report-schedules/{mine.json()['id']}/run")).json()["status"] == "success"
    assert (await other.delete(f"/api/v1/report-schedules/{mine.json()['id']}")).status_code == 200
    await admin.delete(f"/api/v1/report-schedules/{theirs['id']}")
    await admin.delete(f"/api/v1/notification-channels/{ch['id']}")


async def test_owner_without_the_permission_or_deleted_and_size_limit(admin, make_client, smtp, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The run fails without sending when the owner lost the report's permission or was
    deleted, and when the file is larger than DBM_REPORT_MAX_ATTACHMENT_MB."""
    from app.core.config import get_settings

    inbox, port = smtp
    ch = await _mail_channel(admin, port, "reports-mail-6")
    name, pw = await _scheduler_user(admin)
    c = await make_client((name, pw))
    s = (
        await c.post(
            "/api/v1/report-schedules",
            json={
                "name": f"{name} fleet",
                "report_key": "fleet-health",
                "format": "csv",
                "schedule": {"frequency": "daily", "time": "07:00"},
                "channel_ids": [ch["id"]],
            },
        )
    ).json()
    sent = len(inbox.messages)
    # size limit
    monkeypatch.setattr(get_settings(), "report_max_attachment_mb", 0.000001)
    run = (await admin.post(f"/api/v1/report-schedules/{s['id']}/run")).json()
    assert run["status"] == "failed" and "DBM_REPORT_MAX_ATTACHMENT_MB" in run["error"]
    monkeypatch.setattr(get_settings(), "report_max_attachment_mb", 10.0)
    # role revoked: the owner may no longer run the report
    uid = next(u["id"] for u in (await admin.get("/api/v1/users", params={"q": name})).json()["items"])
    for a in (await admin.get("/api/v1/role-assignments", params={"principal_id": uid})).json():
        await admin.delete(f"/api/v1/role-assignments/{a['id']}")
    run = (await admin.post(f"/api/v1/report-schedules/{s['id']}/run")).json()
    assert run["status"] == "failed" and "may no longer run" in run["error"]
    # owner deleted
    assert (await admin.delete(f"/api/v1/users/{uid}")).status_code == 200
    run = (await admin.post(f"/api/v1/report-schedules/{s['id']}/run")).json()
    assert run["status"] == "failed" and "no longer exists" in run["error"]
    assert len(inbox.messages) == sent
    await admin.delete(f"/api/v1/report-schedules/{s['id']}")
    await admin.delete(f"/api/v1/notification-channels/{ch['id']}")
