"""Alert conditions, scopes, maintenance windows, webhook signing/SSRF guard, SMTP sending."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.errors import ValidationFailed
from app.services import alerting, notify

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def server(**kw):  # type: ignore[no-untyped-def]
    base = dict(
        id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        latest_metrics={"connections.utilization": 91.0, "_collected_at": NOW.isoformat()},
        collection_interval_s=60,
        health_status="healthy",
        health_reasons=[],
        last_error=None,
        collector_state={},
        consecutive_failures=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def rule(**kw):  # type: ignore[no-untyped-def]
    base = dict(
        kind="metric",
        metric="connections.utilization",
        operator=">=",
        threshold=90.0,
        health_states=[],
        scope_type="all",
        scope_id=None,
        aggregation="latest",
        window_seconds=0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_metric_threshold_condition() -> None:
    holds, value, desc = alerting.condition(rule(), server(), NOW)
    assert holds is True and value == 91.0 and "connections.utilization = 91" in desc
    holds, *_ = alerting.condition(rule(threshold=95.0), server(), NOW)
    assert holds is False
    for opname, expect in ((">", True), ("<", False), ("<=", False), ("==", False), ("!=", True)):
        assert alerting.condition(rule(operator=opname), server(), NOW)[0] is expect


def test_missing_or_stale_metric_is_no_data() -> None:
    assert alerting.condition(rule(metric="cpu.percent"), server(), NOW)[0] is None
    stale = server(
        latest_metrics={"connections.utilization": 99.0, "_collected_at": (NOW - timedelta(minutes=10)).isoformat()}
    )
    assert alerting.condition(rule(), stale, NOW)[0] is None  # 3 x 60 s


def test_health_and_collector_conditions() -> None:
    down = server(health_status="unavailable", last_error="connection refused")
    holds, _, desc = alerting.condition(rule(kind="health", health_states=["unavailable"]), down, NOW)
    assert holds is True and "connection refused" in desc
    assert alerting.condition(rule(kind="health", health_states=["unavailable"]), server(), NOW)[0] is False
    failing = server(collector_state={"components": {"metrics": {"status": "failed"}}})
    assert alerting.condition(rule(kind="collector", threshold=None), failing, NOW)[0] is True
    assert alerting.condition(rule(kind="collector", threshold=None), server(), NOW)[0] is False
    assert alerting.condition(rule(kind="collector", threshold=3), server(consecutive_failures=3), NOW)[0] is True


def test_scopes_and_maintenance_windows() -> None:
    s = server()
    assert alerting.in_scope("all", None, s)
    assert alerting.in_scope("environment", str(s.environment_id), s)
    assert not alerting.in_scope("environment", str(uuid.uuid4()), s)
    assert alerting.in_scope("server", str(s.id), s)
    w_active = SimpleNamespace(
        starts_at=NOW - timedelta(hours=1),
        ends_at=NOW + timedelta(hours=1),
        scope_type="server",
        scope_id=str(s.id),
        recurrence="none",
    )
    w_other = SimpleNamespace(
        starts_at=NOW - timedelta(hours=1),
        ends_at=NOW + timedelta(hours=1),
        scope_type="server",
        scope_id=str(uuid.uuid4()),
        recurrence="none",
    )
    w_past = SimpleNamespace(
        starts_at=NOW - timedelta(hours=3),
        ends_at=NOW - timedelta(hours=2),
        scope_type="all",
        scope_id=None,
        recurrence="none",
    )
    assert alerting.active_window([w_other, w_past, w_active], s, NOW) is w_active
    assert alerting.active_window([w_other, w_past], s, NOW) is None


def test_webhook_signature_is_hmac_sha256() -> None:
    body = json.dumps({"a": 1}).encode()
    sig = notify.sign("s3cret", body)
    assert sig == "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()


def test_webhook_url_validation() -> None:
    notify.validate_webhook_url("https://hooks.example.com/x")
    for bad in ("ftp://x/y", "https://", "https://user:pw@hooks.example.com/x", "not a url"):
        with pytest.raises(ValidationFailed):
            notify.validate_webhook_url(bad)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9/x",
        "http://10.1.2.3/x",
        "http://169.254.169.254/latest",
        "http://[::1]/x",
        "http://100.64.0.1/x",
    ],
)
async def test_ssrf_guard_refuses_internal_targets(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "webhook_allow_private", False)
    with pytest.raises(notify.DeliveryError, match="non-public"):
        await notify.check_target(url)


@pytest.mark.asyncio
async def test_ssrf_guard_can_be_relaxed(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "webhook_allow_private", True)
    await notify.check_target("http://127.0.0.1:9/x")


@pytest.mark.asyncio
async def test_webhook_send_signs_body(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "webhook_allow_private", True)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["sig"] = request.headers.get(notify.SIGNATURE_HEADER)
        seen["body"] = request.content
        seen["custom"] = request.headers.get("X-Team")
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(notify.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    ch = SimpleNamespace(
        id=uuid.uuid4(),
        kind="webhook",
        preset="generic",
        config={"url": "http://127.0.0.1/hook", "headers": {"X-Team": "dba"}},
        secret=None,
    )
    ch.secret = notify.seal_secret(ch.id, {"signing_secret": "k3y"})
    assert "k3y" not in json.dumps(ch.secret)  # stored encrypted
    code = await notify.send(ch, "test", None)
    assert code == 204 and seen["custom"] == "dba"
    assert seen["sig"] == notify.sign("k3y", seen["body"]) and json.loads(seen["body"])["event"] == "test"


@pytest.mark.asyncio
async def test_webhook_http_error_is_delivery_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "webhook_allow_private", True)
    real = httpx.AsyncClient
    transport = httpx.MockTransport(lambda r: httpx.Response(500))
    monkeypatch.setattr(notify.httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    ch = SimpleNamespace(
        id=uuid.uuid4(), kind="webhook", preset="generic", config={"url": "http://127.0.0.1/hook"}, secret=None
    )
    with pytest.raises(notify.DeliveryError) as e:
        await notify.send(ch, "firing", {"title": "x"})
    assert e.value.code == 500


class FakeSMTP:
    sent: list = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.log = host, port, []

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *a):  # type: ignore[no-untyped-def]
        return False

    def starttls(self, context=None):  # type: ignore[no-untyped-def]
        self.log.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.log.append(("login", user, password))

    def send_message(self, msg) -> None:  # type: ignore[no-untyped-def]
        FakeSMTP.sent.append((self.host, self.port, self.log, msg))


@pytest.mark.asyncio
async def test_email_channel_uses_channel_smtp_and_encrypted_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent.clear()
    ch = SimpleNamespace(
        id=uuid.uuid4(),
        kind="email",
        config={
            "recipients": ["dba@example.com"],
            "smtp_host": "mail.example.com",
            "smtp_port": 2525,
            "smtp_user": "dbm",
            "from": "dbm@example.com",
        },
        secret=None,
    )
    ch.secret = notify.seal_secret(ch.id, {"smtp_password": "Mail-Pw-1"})
    alert = {
        "title": "High connection use on PG-1",
        "message": "connections.utilization = 91",
        "severity": "high",
        "server_name": "PG-1",
        "engine": "postgresql",
        "value": 91.0,
        "threshold": 90.0,
        "operator": ">=",
    }
    assert await notify.send(ch, "firing", alert) == 250
    host, port, log, msg = FakeSMTP.sent[-1]
    assert (host, port) == ("mail.example.com", 2525)
    assert log == ["starttls", ("login", "dbm", "Mail-Pw-1")]
    assert msg["To"] == "dba@example.com" and "HIGH" in msg["Subject"] and "91" in msg.get_content()


@pytest.mark.asyncio
async def test_email_without_smtp_configuration_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "smtp_host", "")
    ch = SimpleNamespace(id=uuid.uuid4(), kind="email", config={"recipients": ["a@b.c"]}, secret=None)
    with pytest.raises(notify.DeliveryError, match="SMTP is not configured"):
        await notify.send(ch, "test", None)


@pytest.mark.asyncio
async def test_plaintext_webhook_urls_move_into_the_secret() -> None:
    """Channels saved by older versions keep url/headers in config: startup moves them into
    the encrypted secret without losing the signing secret."""
    import uuid as _uuid
    from types import SimpleNamespace as NS

    from app.services import notify

    cid = _uuid.uuid4()
    ch = NS(
        id=cid,
        kind="webhook",
        config={"url": "https://hooks.slack.com/services/T0/B0/xyz", "headers": {"X-Team": "dba"}},
        secret=notify.seal_secret(cid, {"signing_secret": "s3cr3t"}),
    )

    class _Res:
        def scalars(self):  # type: ignore[no-untyped-def]
            return [ch]

    class _Db:
        async def execute(self, _stmt):  # type: ignore[no-untyped-def]
            return _Res()

    assert await notify.migrate_plaintext_webhooks(_Db()) == 1
    assert "url" not in ch.config and "headers" not in ch.config
    assert ch.config["url_display"] == "https://hooks.slack.com/…" and ch.config["header_names"] == ["X-Team"]
    assert notify.open_secret(ch) == {
        "signing_secret": "s3cr3t",
        "url": "https://hooks.slack.com/services/T0/B0/xyz",
        "headers": {"X-Team": "dba"},
    }
    assert notify.webhook_target(ch) == ("https://hooks.slack.com/services/T0/B0/xyz", {"X-Team": "dba"})
    assert await notify.migrate_plaintext_webhooks(_Db()) == 0  # idempotent
