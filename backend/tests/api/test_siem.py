"""SIEM forwarding (sprint 0.11.4): RFC 5424 formatting, UDP and TCP (octet counting) syslog
receivers, webhook batches with signature, cursor/at-least-once, category filters, RBAC."""

from __future__ import annotations

import json
import socket
import socketserver
import threading
from datetime import UTC, datetime, timedelta

import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---- receivers ----------------------------------------------------------------------------------------
class _UDP(socketserver.BaseRequestHandler):
    got: list[bytes] = []

    def handle(self) -> None:
        _UDP.got.append(self.request[0])


class _TCP(socketserver.StreamRequestHandler):
    got: list[bytes] = []

    def handle(self) -> None:
        data = b""
        while chunk := self.request.recv(65536):
            data += chunk
        while data:  # octet counting: "<len> <msg>"
            n, _, rest = data.partition(b" ")
            _TCP.got.append(rest[: int(n)])
            data = rest[int(n) :]


def _serve(server_cls, handler):  # type: ignore[no-untyped-def]
    srv = server_cls(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


async def _export(at: datetime) -> dict:
    from app.db.session import session_scope
    from app.services import siem

    async with session_scope() as db:
        return await siem.export(db, at)


async def test_syslog_udp_and_tcp_forwarding_with_cursor(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    _UDP.got, _TCP.got = [], []
    udp = _serve(socketserver.ThreadingUDPServer, _UDP)
    tcp = _serve(socketserver.ThreadingTCPServer, _TCP)
    try:
        dests = []
        for name, transport, port in (
            ("it-udp", "udp", udp.server_address[1]),
            ("it-tcp", "tcp", tcp.server_address[1]),
        ):
            r = await admin.post(
                "/api/v1/platform/siem-destinations",
                json={
                    "name": name,
                    "kind": "syslog",
                    "config": {"host": "127.0.0.1", "port": port, "transport": transport},
                    "categories": ["auth"],
                },
            )
            assert r.status_code == 201, r.text
            dests.append(r.json())
        assert dests[0]["pending"] == 0  # start_from=now: history is not replayed
        # produce two auth events (a failed and a successful login)
        anon = await make_client()
        await anon.post("/api/v1/auth/login", json={"username": "monitor", "password": "wrong"})
        await make_client(("monitor", DEMO_PW))
        later = datetime.now(UTC) + timedelta(seconds=10)
        st = await _export(later)
        assert st["forwarded"] >= 4 and st["failed"] == 0  # 2 events × 2 destinations
        import asyncio

        await asyncio.sleep(0.3)
        for got in (_UDP.got, _TCP.got):
            actions = [g.split(b" ")[5] for g in got]
            assert actions.count(b"auth.login") >= 2 and all(g.startswith(b"<") for g in got)
            assert all(b'category="auth"' in g for g in got)  # category filter
        # nothing new: nothing sent again (cursor advanced)
        before = (len(_UDP.got), len(_TCP.got))
        assert (await _export(later))["forwarded"] == 0 and (len(_UDP.got), len(_TCP.got)) == before
        listed = {d["name"]: d for d in (await admin.get("/api/v1/platform/siem-destinations")).json()}
        assert listed["it-tcp"]["pending"] == 0 and listed["it-tcp"]["exported_count"] >= 2
        assert listed["it-tcp"]["last_success_at"] is not None
    finally:
        udp.shutdown()
        tcp.shutdown()
        for d in dests:
            await admin.delete(f"/api/v1/platform/siem-destinations/{d['id']}")


async def test_unreachable_destination_keeps_its_position(admin) -> None:  # type: ignore[no-untyped-def]
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    r = await admin.post(
        "/api/v1/platform/siem-destinations",
        json={
            "name": "it-down",
            "kind": "syslog",
            "config": {"host": "127.0.0.1", "port": closed_port, "transport": "tcp", "timeout": 2},
            "start_from": "beginning",
        },
    )
    d = r.json()
    cursor = d["cursor_id"]
    st = await _export(datetime.now(UTC) + timedelta(seconds=10))
    assert st["failed"] >= 1
    after = next(x for x in (await admin.get("/api/v1/platform/siem-destinations")).json() if x["id"] == d["id"])
    assert after["cursor_id"] == cursor and after["last_error"] and after["pending"] > 0  # retried next run
    t = (await admin.post(f"/api/v1/platform/siem-destinations/{d['id']}/test")).json()
    assert t["ok"] is False and t["error"]
    await admin.delete(f"/api/v1/platform/siem-destinations/{d['id']}")


async def test_webhook_destination_batches_signed_events(admin, make_client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.core.config import get_settings
    from app.services import notify

    seen: list[tuple[str, bytes]] = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append((self.headers.get(notify.SIGNATURE_HEADER, ""), body))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *a) -> None:  # type: ignore[no-untyped-def]
            pass

    monkeypatch.setattr(get_settings(), "webhook_allow_private", True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = await admin.post(
            "/api/v1/platform/siem-destinations",
            json={
                "name": "it-hook",
                "kind": "webhook",
                "config": {"url": f"http://127.0.0.1:{srv.server_address[1]}/siem"},
                "signing_secret": "Siem-Secret-9",
                "categories": ["auth"],
            },
        )
        assert r.status_code == 201 and "Siem-Secret-9" not in r.text and r.json()["has_signing_secret"]
        did = r.json()["id"]
        await make_client(("monitor", DEMO_PW))
        await _export(datetime.now(UTC) + timedelta(seconds=10))
        sig, body = seen[-1]
        assert sig == notify.sign("Siem-Secret-9", body)
        payload = json.loads(body)
        assert payload["source"] == "dbmanager" and payload["events"][-1]["action"] == "auth.login"
        assert (await admin.post(f"/api/v1/platform/siem-destinations/{did}/test")).json()["ok"] is True
        await admin.delete(f"/api/v1/platform/siem-destinations/{did}")
    finally:
        srv.shutdown()


async def test_siem_configuration_needs_permission_and_validation(admin, make_client) -> None:  # type: ignore[no-untyped-def]
    auditor = await make_client(("auditor", DEMO_PW))
    assert (await auditor.get("/api/v1/platform/siem-destinations")).status_code == 403
    bad = [
        {"name": "x", "kind": "syslog", "config": {}},
        {"name": "x", "kind": "syslog", "config": {"host": "h", "transport": "smoke"}},
        {"name": "x", "kind": "syslog", "config": {"host": "h", "facility": "nope"}},
        {"name": "x", "kind": "webhook", "config": {"url": "ftp://x"}},
        {"name": "x", "kind": "webhook", "config": {"url": "https://x.example.com", "token": "abc"}},
    ]
    for body in bad:
        assert (await admin.post("/api/v1/platform/siem-destinations", json=body)).status_code == 422, body


async def test_syslog_over_tls_verifies_the_server(admin, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import ssl

    from tests.pki import make_pki

    ca, cert, key = make_pki(tmp_path)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert, key)
    got: list[bytes] = []

    class TLSHandler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            conn = ctx.wrap_socket(self.request, server_side=True)
            data = b""
            try:
                while chunk := conn.recv(65536):
                    data += chunk
            except (OSError, ssl.SSLError):
                pass  # an abrupt close after the data still counts
            finally:
                if data:
                    got.append(data)

    srv = _serve(socketserver.ThreadingTCPServer, TLSHandler)
    port = srv.server_address[1]
    try:
        base = {"name": "it-tls", "kind": "syslog", "start_from": "beginning"}
        r = await admin.post(
            "/api/v1/platform/siem-destinations",
            json={**base, "config": {"host": "localhost", "port": port, "transport": "tls", "ca_pem": ca.read_text()}},
        )
        d = r.json()
        res = (await admin.post(f"/api/v1/platform/siem-destinations/{d['id']}/test")).json()
        assert res["ok"] is True, res
        import asyncio

        for _ in range(200):  # the receiver thread may lag behind on a busy machine
            if got:
                break
            await asyncio.sleep(0.1)
        assert got, "the TLS receiver got nothing"
        n, _, msg = got[-1].partition(b" ")
        assert int(n) == len(msg) and b"siem.test" in msg
        # without the CA the server is not trusted
        await admin.put(
            f"/api/v1/platform/siem-destinations/{d['id']}",
            json={**base, "config": {"host": "localhost", "port": port, "transport": "tls"}},
        )
        res = (await admin.post(f"/api/v1/platform/siem-destinations/{d['id']}/test")).json()
        assert res["ok"] is False and "CERTIFICATE_VERIFY_FAILED" in res["error"]
        await admin.delete(f"/api/v1/platform/siem-destinations/{d['id']}")
    finally:
        srv.shutdown()
