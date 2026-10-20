"""Backup visibility for scoped grants, job cancellation, and backup event notifications.

A DBA Administrator scoped to one environment (``dba.dev``) sees database backups, jobs,
policies and coverage of their servers only — never platform backups, platform jobs or platform
policies — and can cancel only jobs they could have started. Cancelling a running platform
backup stops it. Backup events reach the policy's channels, and events of backups no policy owns
reach the channels named in ``backup.notification_channels``."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def backup_root(tmp_path_factory, monkeypatch) -> Path:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    root = tmp_path_factory.mktemp("backups-scope")
    monkeypatch.setattr(get_settings(), "backup_allowed_roots", [str(root)])
    return root


async def _target(admin, root: Path, name: str) -> dict:  # type: ignore[no-untyped-def, type-arg]
    r = await admin.post(
        "/api/v1/backup/targets", json={"name": name, "kind": "local", "config": {"path": str(root / name)}}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_scoped_grants_see_only_their_database_backups(admin, make_client, backup_root) -> None:  # type: ignore[no-untyped-def]
    t = await _target(admin, backup_root, f"scope-{uuid.uuid4().hex[:6]}")
    job = (
        await admin.post("/api/v1/backups/platform", json={"target_id": t["id"], "encryption": {"mode": "none"}})
    ).json()
    pol = (
        await admin.post(
            "/api/v1/backup/policies",
            json={
                "name": f"platform-nightly-{uuid.uuid4().hex[:4]}",
                "kind": "platform",
                "target_id": t["id"],
                "encryption": {"mode": "none"},
                "schedule": {"frequency": "manual"},
            },
        )
    ).json()
    try:
        dev = await make_client(("dba.dev", DEMO_PW))
        # the platform job and policy exist for the platform administrator ...
        assert any(j["id"] == job["id"] for j in (await admin.get("/api/v1/backup/jobs")).json())
        assert any(p["id"] == pol["id"] for p in (await admin.get("/api/v1/backup/policies")).json())
        # ... and are invisible to the environment-scoped DBA
        assert all(j["id"] != job["id"] for j in (await dev.get("/api/v1/backup/jobs")).json())
        assert (await dev.get(f"/api/v1/backup/jobs/{job['id']}")).status_code == 404
        assert (await dev.post(f"/api/v1/backup/jobs/{job['id']}/cancel")).status_code == 404
        assert all(p["id"] != pol["id"] for p in (await dev.get("/api/v1/backup/policies")).json())
        assert (await dev.get(f"/api/v1/backup/policies/{pol['id']}/retention")).status_code == 404
        ov = (await dev.get("/api/v1/backup/overview")).json()
        assert ov["platform_protection"] is None and ov["last_platform_backup_at"] is None
        assert all(j["id"] != job["id"] for j in ov["running_jobs"])
        targets = (await dev.get("/api/v1/backup/targets")).json()
        mine = next(x for x in targets if x["id"] == t["id"])
        assert set(mine) == {"id", "name", "kind", "enabled"}  # no paths, buckets or usage
        # the administrator still has everything
        full = (await admin.get("/api/v1/backup/overview")).json()
        assert full["platform_protection"] is not None
        # cancelling the queued platform job works for the administrator
        r = await admin.post(f"/api/v1/backup/jobs/{job['id']}/cancel")
        assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    finally:
        await admin.delete(f"/api/v1/backup/policies/{pol['id']}")
        await admin.delete(f"/api/v1/backup/targets/{t['id']}")


async def test_cancelling_a_running_platform_backup_stops_it(admin, backup_root, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.backup import platform, service

    t = await _target(admin, backup_root, f"cancel-{uuid.uuid4().hex[:6]}")
    job = (
        await admin.post("/api/v1/backups/platform", json={"target_id": t["id"], "encryption": {"mode": "none"}})
    ).json()
    real_export = platform.export_tables

    async def export_then_cancel(db, components, workdir):  # type: ignore[no-untyped-def]
        out = await real_export(db, components, workdir)
        # the user presses Cancel while the export runs
        r = await admin.post(f"/api/v1/backup/jobs/{job['id']}/cancel")
        assert r.status_code == 200, r.text
        return out

    monkeypatch.setattr(platform, "export_tables", export_then_cancel)
    done = await service.run(uuid.UUID(job["id"]))
    assert done.status == "CANCELLED", (done.status, done.error)
    backups = (await admin.get("/api/v1/backups", params={"kind": "platform", "page_size": 5})).json()["items"]
    mine = [b for b in backups if b["job_id"] == job["id"]] if backups and "job_id" in backups[0] else backups[:1]
    assert mine and mine[0]["status"] == "FAILED" and "cancelled" in (mine[0]["error"] or "")
    await admin.delete(f"/api/v1/backup/targets/{t['id']}")


async def test_backup_events_reach_policy_and_platform_channels(admin, backup_root, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.backup import service, storage
    from app.services import alerting

    sent: list[tuple[str, str]] = []

    async def capture(db, channel, event, alert, data):  # type: ignore[no-untyped-def]
        sent.append((channel.name, data["title"]))

    monkeypatch.setattr(alerting, "deliver", capture)
    ch = (
        await admin.post(
            "/api/v1/notification-channels",
            json={
                "name": f"backup-events-{uuid.uuid4().hex[:4]}",
                "kind": "email",
                "config": {"recipients": ["dba@example.com"]},
            },
        )
    ).json()
    t = await _target(admin, backup_root, f"events-{uuid.uuid4().hex[:6]}")
    pol = (
        await admin.post(
            "/api/v1/backup/policies",
            json={
                "name": f"evt-{uuid.uuid4().hex[:4]}",
                "kind": "platform",
                "target_id": t["id"],
                "encryption": {"mode": "none"},
                "schedule": {"frequency": "manual"},
                "channel_ids": [ch["id"]],
            },
        )
    ).json()
    try:
        # storage unavailable: classified from the exception type
        async def broken_store(*a, **k):  # type: ignore[no-untyped-def]
            raise storage.StorageError("the storage target is not reachable")

        monkeypatch.setattr(service, "_store", broken_store)
        job = (await admin.post(f"/api/v1/backup/policies/{pol['id']}/run")).json()
        done = await service.run(uuid.UUID(job["id"]))
        assert done.status == "FAILED"
        assert (ch["name"], "Backup storage unavailable") in sent
        # a manual backup (no policy) notifies nobody until a platform channel is named ...
        sent.clear()
        job = (
            await admin.post("/api/v1/backups/platform", json={"target_id": t["id"], "encryption": {"mode": "none"}})
        ).json()
        await service.run(uuid.UUID(job["id"]))
        assert sent == []
        # ... then the named channel receives it
        r = await admin.put("/api/v1/settings", json={"backup.notification_channels": ch["name"]})
        assert r.status_code == 200, r.text
        job = (
            await admin.post("/api/v1/backups/platform", json={"target_id": t["id"], "encryption": {"mode": "none"}})
        ).json()
        await service.run(uuid.UUID(job["id"]))
        assert (ch["name"], "Backup storage unavailable") in sent
    finally:
        await admin.put("/api/v1/settings", json={"backup.notification_channels": ""})
        await admin.delete(f"/api/v1/backup/policies/{pol['id']}")
        await admin.delete(f"/api/v1/backup/targets/{t['id']}")
        await admin.delete(f"/api/v1/notification-channels/{ch['id']}")


async def test_platform_restore_keeps_its_safety_backup_unless_allowed(admin, backup_root) -> None:  # type: ignore[no-untyped-def]
    from app.backup import service

    t = await _target(admin, backup_root, f"safety-{uuid.uuid4().hex[:6]}")
    job = (
        await admin.post("/api/v1/backups/platform", json={"target_id": t["id"], "encryption": {"mode": "none"}})
    ).json()
    done = await service.run(uuid.UUID(job["id"]))
    assert done.status == "SUCCESS", done.error
    bid = done.result["backup_id"] if done.result and "backup_id" in done.result else None
    if bid is None:
        items = (await admin.get("/api/v1/backups", params={"kind": "platform", "page_size": 1})).json()["items"]
        bid = items[0]["id"]
    try:
        # the preview decrypts the archive and is audited
        pv = await admin.post(f"/api/v1/backups/{bid}/restore/preview", json={})
        assert pv.status_code == 200, pv.text
        ev = (await admin.get("/api/v1/audit", params={"action": "restore.preview", "page_size": 1})).json()["items"]
        assert ev and ev[0]["target_id"] == bid
        body = {"confirm": "RESTORE PLATFORM", "pre_restore_backup": False}
        r = await admin.post(f"/api/v1/backups/{bid}/restore", json=body)
        assert r.status_code == 422 and r.json()["error"]["code"] == "safety_backup_required"
        await admin.put("/api/v1/settings", json={"backup.allow_restore_without_safety_backup": True})
        r = await admin.post(f"/api/v1/backups/{bid}/restore", json=body)
        assert r.status_code == 202, r.text
        # never run in the test database: cancel the queued restore
        assert (await admin.post(f"/api/v1/backup/jobs/{r.json()['id']}/cancel")).json()["status"] == "CANCELLED"
        ev = (await admin.get("/api/v1/audit", params={"action": "restore.request", "page_size": 1})).json()["items"]
        assert ev[0]["details"]["pre_restore_backup"] is False
        # checking a storage target is audited too
        assert (await admin.post(f"/api/v1/backup/targets/{t['id']}/check")).json()["ok"] is True
        ev = (await admin.get("/api/v1/audit", params={"action": "backup.target.check", "page_size": 1})).json()[
            "items"
        ]
        assert ev and ev[0]["target_id"] == t["id"]
    finally:
        await admin.put("/api/v1/settings", json={"backup.allow_restore_without_safety_backup": False})
        await admin.delete(f"/api/v1/backup/targets/{t['id']}")
