"""Backup paths not covered elsewhere: which servers a policy scope selects (server, environment
and tag scopes; simulated servers never), the VictoriaMetrics export inside a platform backup
(and a failing export), and the Prometheus counters a backup run moves."""

from __future__ import annotations

import hashlib
import io
import tarfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

pytestmark = pytest.mark.asyncio(loop_scope="session")
NATIVE = b"\x00native-export-bytes\x01" * 64


class FakeVM(BaseHTTPRequestHandler):
    status = 200
    seen: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        FakeVM.seen.append(self.path)
        if not self.path.startswith("/api/v1/export/native"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(FakeVM.status)
        self.send_header("Content-Length", str(len(NATIVE) if FakeVM.status == 200 else 0))
        self.end_headers()
        if FakeVM.status == 200:
            self.wfile.write(NATIVE)

    def log_message(self, *a) -> None:  # type: ignore[no-untyped-def]
        pass


@pytest.fixture
def backup_root(tmp_path_factory, monkeypatch) -> Path:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    root = tmp_path_factory.mktemp("backups-extra")
    monkeypatch.setattr(get_settings(), "backup_allowed_roots", [str(root)])
    return root


@pytest.fixture
def fake_vm(monkeypatch):  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    FakeVM.status, FakeVM.seen = 200, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeVM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = get_settings()
    monkeypatch.setattr(s, "timeseries_enabled", True)
    monkeypatch.setattr(s, "timeseries_backend", "victoriametrics")
    monkeypatch.setattr(s, "timeseries_url", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setattr(s, "timeseries_username", "")
    yield FakeVM
    srv.shutdown()


async def _target(admin, root: Path, name: str) -> dict:  # type: ignore[no-untyped-def, type-arg]
    r = await admin.post(
        "/api/v1/backup/targets", json={"name": name, "kind": "local", "config": {"path": str(root / name)}}
    )
    assert r.status_code == 201, r.text
    return r.json()


def _jobs(kind: str, result: str) -> float:
    return REGISTRY.get_sample_value("dbm_backup_jobs_total", {"kind": kind, "result": result}) or 0.0


async def test_policy_scopes_select_real_servers_by_id_environment_and_tag(admin, servers) -> None:  # type: ignore[no-untyped-def]
    from app.backup import service
    from app.db.session import session_scope

    envs = {e["name"]: e["id"] for e in (await admin.get("/api/v1/environments")).json()}
    made = []
    for name, env, tags in (
        ("scope-a", "Production", [{"key": "team", "value": "payments"}]),
        ("scope-b", "Production", [{"key": "team", "value": "web"}]),
        ("scope-c", "Development", []),
    ):
        r = await admin.post(
            "/api/v1/servers",
            json={
                "name": f"{name}-{uuid.uuid4().hex[:4]}",
                "engine": "postgresql",
                "host": "db.invalid",
                "port": 5432,
                "monitoring_enabled": False,
                "environment_id": envs[env],
                "tags": tags,
            },
        )
        assert r.status_code == 201, r.text
        made.append(r.json())
    a, b, c = (m["id"] for m in made)
    try:
        async with session_scope() as db:

            async def ids(scope: dict) -> set[str]:  # type: ignore[type-arg]
                return {str(s.id) for s in await service.scope_servers(db, scope)}

            assert await ids({"server_ids": [a]}) == {a}
            prod = await ids({"environment_ids": [envs["Production"]]})
            assert {a, b} <= prod and c not in prod
            assert await ids({"tags": ["team=payments"]}) == {a}
            assert {a, b} <= await ids({"tags": ["team"]})
            # union of the selectors
            assert {a, c} <= await ids({"server_ids": [c], "tags": ["team=payments"]})
            # an empty scope selects nothing (never "everything")
            assert await ids({}) == set()
            # simulated servers are never backed up, even when named explicitly
            demo = servers["PG-PROD-01"]["id"]
            assert await ids({"server_ids": [demo]}) == set()
    finally:
        for m in made:
            await admin.delete(f"/api/v1/servers/{m['id']}")


async def test_platform_backup_carries_the_victoriametrics_export(admin, backup_root, fake_vm) -> None:  # type: ignore[no-untyped-def]
    from app.backup import service

    t = await _target(admin, backup_root, f"vm-{uuid.uuid4().hex[:6]}")
    before = _jobs("platform_backup", "SUCCESS")
    try:
        job = (
            await admin.post(
                "/api/v1/backups/platform",
                json={
                    "target_id": t["id"],
                    "encryption": {"mode": "none"},
                    "scope": {"metrics": "export", "metrics_days": 7},
                },
            )
        ).json()
        done = await service.run(uuid.UUID(job["id"]))
        assert done.status == "SUCCESS", done.error
        assert any("start=" in p for p in fake_vm.seen)  # only the last 7 days were requested
        bid = done.result["backup_ids"][0]
        b = (await admin.get(f"/api/v1/backups/{bid}")).json()
        m = b["manifest"]["metrics"]
        assert m["included"] is True and m["days"] == 7 and m["size"] == len(NATIVE)
        assert m["sha256"] == hashlib.sha256(NATIVE).hexdigest()
        # the export is inside the stored (unencrypted) archive, byte for byte
        archive = Path(b["manifest"]["archive"]["object"])
        if not archive.is_absolute():
            archive = backup_root / t["name"] / archive
        with tarfile.open(fileobj=io.BytesIO(archive.read_bytes())) as tar:
            member = tar.extractfile("metrics/victoriametrics.native")
            assert member is not None and member.read() == NATIVE
        assert _jobs("platform_backup", "SUCCESS") == before + 1

        # a failing export fails the backup with the reason, and is counted as a failure
        fake_vm.status = 503
        failed_before = _jobs("platform_backup", "FAILED")
        job = (
            await admin.post(
                "/api/v1/backups/platform",
                json={"target_id": t["id"], "encryption": {"mode": "none"}, "scope": {"metrics": "export"}},
            )
        ).json()
        done = await service.run(uuid.UUID(job["id"]))
        assert done.status == "FAILED" and "VictoriaMetrics export answered HTTP 503" in (done.error or "")
        assert _jobs("platform_backup", "FAILED") == failed_before + 1
    finally:
        await admin.delete(f"/api/v1/backup/targets/{t['id']}")
