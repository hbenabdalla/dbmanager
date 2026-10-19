"""Vault credential provider (sprint 0.11.5) against a local HTTP server implementing the parts
of the Vault API DB Manager uses (KV v2 read, AppRole login, sys/health, namespaces), and end to
end: a registered PostgreSQL whose password lives only in "Vault" passes Test connection."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.asyncio(loop_scope="session")
ROOT_TOKEN = "hvs.root-test-token"
APPROLE_TOKEN = "hvs.approle-issued"


class FakeVault(BaseHTTPRequestHandler):
    kv: dict[str, dict[str, str]] = {}
    calls: list[tuple[str, str, dict[str, str]]] = []

    def _reply(self, code: int, body: dict) -> None:  # type: ignore[type-arg]
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        FakeVault.calls.append(("GET", self.path, dict(self.headers)))
        if self.path.startswith("/v1/sys/health"):
            return self._reply(200, {"initialized": True, "sealed": False, "version": "1.17.0"})
        if self.headers.get("X-Vault-Token") not in (ROOT_TOKEN, APPROLE_TOKEN):
            return self._reply(403, {"errors": ["permission denied"]})
        mount, _, rest = self.path.removeprefix("/v1/").partition("/data/")
        secret = FakeVault.kv.get(f"{mount}/{rest}")
        if secret is None:
            return self._reply(404, {"errors": []})
        return self._reply(200, {"data": {"data": secret, "metadata": {"version": 1}}})

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        FakeVault.calls.append(("POST", self.path, dict(self.headers)))
        if self.path == "/v1/auth/approle/login" and body == {"role_id": "dbm-role", "secret_id": "dbm-secret-id"}:
            return self._reply(200, {"auth": {"client_token": APPROLE_TOKEN, "lease_duration": 3600}})
        return self._reply(400, {"errors": ["invalid role or secret id"]})

    def log_message(self, *a) -> None:  # type: ignore[no-untyped-def]
        pass


@pytest.fixture
def vault(monkeypatch):  # type: ignore[no-untyped-def]
    from app.core.config import get_settings
    from app.services import vault as vault_mod

    FakeVault.kv = {
        "secret/databases/pg-it": {"password": make_url(os.environ["DBM_TEST_DATABASE_URL"]).password or ""},
        "kv-dba/oracle/erp": {"password": "Erp-Secret-1"},
    }
    FakeVault.calls = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeVault)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = get_settings()
    monkeypatch.setattr(s, "vault_addr", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setattr(s, "vault_auth", "token")
    monkeypatch.setattr(s, "vault_token", __import__("pydantic").SecretStr(ROOT_TOKEN))
    monkeypatch.setattr(s, "vault_namespace", "dba-team")
    vault_mod.client.clear()
    yield s
    vault_mod.client.clear()
    srv.shutdown()


async def test_kv2_read_auth_methods_cache_and_errors(vault, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    from app.services import vault as v

    assert await v.client.read("kv-dba:oracle/erp#password") == "Erp-Secret-1"
    assert FakeVault.calls[-1][2]["X-Vault-Namespace"] == "dba-team"
    n = len(FakeVault.calls)
    assert await v.client.read("kv-dba:oracle/erp#password") == "Erp-Secret-1"
    assert len(FakeVault.calls) == n  # served from the in-memory cache
    with pytest.raises(v.VaultError, match="not found"):
        await v.client.read("databases/missing#password")
    with pytest.raises(v.VaultError, match="no key 'user'"):
        await v.client.read("kv-dba:oracle/erp#user")
    for bad in ("no-key", "../escape#x", "/abs#x", "a#b c"):
        with pytest.raises(Exception, match="Vault references"):
            v.parse_ref(bad)
    # AppRole: logs in once, then reuses the issued token
    v.client.clear()
    monkeypatch.setattr(vault, "vault_auth", "approle")
    monkeypatch.setattr(vault, "vault_role_id", "dbm-role")
    monkeypatch.setattr(vault, "vault_secret_id", SecretStr("dbm-secret-id"))
    monkeypatch.setattr(vault, "vault_cache_seconds", 0)
    assert await v.client.read("kv-dba:oracle/erp#password") == "Erp-Secret-1"
    assert await v.client.read("kv-dba:oracle/erp#password") == "Erp-Secret-1"
    assert sum(1 for m, p, _ in FakeVault.calls if p == "/v1/auth/approle/login") == 1
    monkeypatch.setattr(vault, "vault_secret_id", SecretStr("wrong"))
    v.client.clear()
    with pytest.raises(v.VaultError, match="approle login failed"):
        await v.client.read("kv-dba:oracle/erp#password")
    health = await v.client.health()
    assert health["reachable"] is True and health["sealed"] is False


async def test_registered_server_connects_with_a_vault_secret(admin, vault) -> None:  # type: ignore[no-untyped-def]
    u = make_url(os.environ["DBM_TEST_DATABASE_URL"])
    r = await admin.post(
        "/api/v1/servers",
        json={
            "name": "it-vault-pg",
            "engine": "postgresql",
            "host": u.host or "localhost",
            "port": u.port or 5432,
            "default_database": u.database,
            "tls_mode": "disable",
            "monitoring_enabled": False,
            "monitoring_credential": {
                "username": u.username,
                "provider": "vault",
                "secret_ref": "databases/pg-it#password",
            },
        },
    )
    assert r.status_code == 201, r.text
    s = (await admin.get(f"/api/v1/servers/{r.json()['id']}")).json()
    assert s["credentials"]["monitoring"]["provider"] == "vault"
    assert s["credentials"]["monitoring"]["secret_ref"] == "databases/pg-it#password"
    assert (u.password or "@@") not in r.text
    test = await admin.post(f"/api/v1/servers/{s['id']}/test-connection")
    assert test.status_code == 200 and test.json()["ok"] is True, test.text
    # the secret disappears from Vault: the next connection fails with a clear message
    FakeVault.kv.pop("secret/databases/pg-it")
    from app.services import vault as v

    v.client.clear()
    failed = (await admin.post(f"/api/v1/servers/{s['id']}/test-connection")).json()
    assert failed["ok"] is False and "Vault" in json.dumps(failed)
    providers = (await admin.get("/api/v1/platform/secret-providers")).json()
    assert providers["vault"]["available"] is True and providers["vault"]["reachable"] is True
    bad = await admin.put(
        f"/api/v1/servers/{s['id']}/credentials/monitoring",
        json={"username": "x", "provider": "vault", "secret_ref": "not a ref"},
    )
    assert bad.status_code == 422
    await admin.delete(f"/api/v1/servers/{s['id']}", params={"confirm": s["name"]})
