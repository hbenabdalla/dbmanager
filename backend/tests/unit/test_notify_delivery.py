"""Notification delivery against real local receivers (no mocks): an SMTP server (aiosmtpd) with
STARTTLS + AUTH and with implicit TLS, verified against a test CA, and an HTTP webhook receiver
checking the HMAC signature and the Slack/Teams presets."""

from __future__ import annotations

import json
import socket
import ssl
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

aiosmtpd = pytest.importorskip("aiosmtpd")
from aiosmtpd.controller import Controller  # noqa: E402
from aiosmtpd.smtp import AuthResult, LoginPassword  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services import notify  # noqa: E402
from tests.pki import make_pki  # noqa: E402

pytestmark = pytest.mark.asyncio


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Inbox:
    def __init__(self) -> None:
        self.messages: list[tuple[str, list[str], str]] = []

    async def handle_DATA(self, server, session, envelope):  # type: ignore[no-untyped-def] # noqa: N802
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos), envelope.content.decode()))
        return "250 OK"


def _auth(server, session, envelope, mechanism, auth_data):  # type: ignore[no-untyped-def]
    ok = isinstance(auth_data, LoginPassword) and auth_data.login == b"dbm" and auth_data.password == b"Mail-Pw-1"
    return AuthResult(success=ok)


def _channel(port: int, security: str) -> SimpleNamespace:
    ch = SimpleNamespace(
        id=uuid.uuid4(),
        kind="email",
        preset="generic",
        config={
            "recipients": ["dba@example.com"],
            "smtp_host": "localhost",
            "smtp_port": port,
            "smtp_user": "dbm",
            "from": "dbm@example.com",
            "security": security,
        },
        secret=None,
    )
    ch.secret = notify.seal_secret(ch.id, {"smtp_password": "Mail-Pw-1"})
    return ch


ALERT = {
    "title": "Disk full on PG-1",
    "message": "storage.used_pct = 97 (>= 90)",
    "server_name": "PG-1",
    "engine": "postgresql",
    "severity": "critical",
    "url": "https://dbm.example.com/alerts?id=1",
}


async def test_smtp_starttls_with_auth_against_a_local_server(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ca, cert, key = make_pki(tmp_path)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)
    inbox, port = Inbox(), _free_port()
    ctl = Controller(
        inbox,
        hostname="127.0.0.1",
        port=port,
        tls_context=server_ctx,
        require_starttls=True,
        authenticator=_auth,
        auth_require_tls=True,
        auth_required=True,  # mail is only accepted after a successful AUTH
    )
    ctl.start()
    try:
        monkeypatch.setattr(get_settings(), "smtp_ca_file", str(ca))
        assert await notify.send(_channel(port, "starttls"), "firing", ALERT) == 250
        mail_from, rcpts, content = inbox.messages[0]
        assert mail_from == "dbm@example.com" and rcpts == ["dba@example.com"]
        assert "Subject: [DBManager] CRITICAL: Disk full on PG-1" in content
        # without the CA the server certificate is not trusted: delivery fails, nothing is sent
        monkeypatch.setattr(get_settings(), "smtp_ca_file", "")
        with pytest.raises(notify.DeliveryError, match="CERTIFICATE_VERIFY_FAILED|certificate verify failed"):
            await notify.send(_channel(port, "starttls"), "firing", ALERT)
        assert len(inbox.messages) == 1
    finally:
        ctl.stop()


async def test_smtp_implicit_tls_against_a_local_server(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ca, cert, key = make_pki(tmp_path)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)
    inbox, port = Inbox(), _free_port()
    ctl = Controller(
        inbox, hostname="127.0.0.1", port=port, ssl_context=server_ctx, authenticator=_auth, auth_require_tls=False
    )
    ctl.start()
    try:
        monkeypatch.setattr(get_settings(), "smtp_ca_file", str(ca))
        assert await notify.send(_channel(port, "tls"), "resolved", ALERT) == 250
        assert "Subject: [DBManager] RESOLVED: Disk full on PG-1" in inbox.messages[0][2]
    finally:
        ctl.stop()


class _Receiver(BaseHTTPRequestHandler):
    seen: list[tuple[dict[str, str], bytes]] = []
    status = 204

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _Receiver.seen.append((dict(self.headers), body))
        self.send_response(_Receiver.status)
        self.end_headers()

    def log_message(self, *args) -> None:  # type: ignore[no-untyped-def]
        pass


@pytest.fixture
def receiver(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(get_settings(), "webhook_allow_private", True)
    _Receiver.seen, _Receiver.status = [], 204
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/hook"
    httpd.shutdown()


def _webhook(url: str, preset: str = "generic") -> SimpleNamespace:
    ch = SimpleNamespace(
        id=uuid.uuid4(), kind="webhook", preset=preset, config={"url": url, "headers": {}}, secret=None
    )
    ch.secret = notify.seal_secret(ch.id, {"signing_secret": "Hook-Secret-1"})
    return ch


async def test_webhook_to_a_local_receiver_is_signed(receiver) -> None:  # type: ignore[no-untyped-def]
    assert await notify.send(_webhook(receiver), "firing", ALERT) == 204
    headers, body = _Receiver.seen[0]
    assert headers["X-DBM-Signature"] == notify.sign("Hook-Secret-1", body)
    assert json.loads(body)["alert"]["title"] == "Disk full on PG-1"
    await notify.send(_webhook(receiver, "slack"), "firing", ALERT)
    assert json.loads(_Receiver.seen[1][1])["text"] == "CRITICAL: Disk full on PG-1"
    await notify.send(_webhook(receiver, "teams"), "firing", ALERT)
    assert json.loads(_Receiver.seen[2][1])["attachments"][0]["content"]["type"] == "AdaptiveCard"


async def test_webhook_receiver_errors_carry_the_status(receiver) -> None:  # type: ignore[no-untyped-def]
    _Receiver.status = 503
    with pytest.raises(notify.DeliveryError) as err:
        await notify.send(_webhook(receiver), "firing", ALERT)
    assert err.value.code == 503
