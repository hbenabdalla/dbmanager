"""Backup & restore end to end (milestone 0.12) against the test PostgreSQL:

* platform backup (age recipient) → verify → change → restore wizard preview → restore → validate,
  with the pre-restore safety backup, typed confirmation, tamper detection and audit trail;
* passphrase encryption on an S3-compatible target (moto server), policies, retention, scheduler,
  disaster-recovery scan of a target into an empty catalog;
* a managed PostgreSQL database: pg_dump backup → engine verification → pg_restore into a new
  database (never over an existing one), asset protection status;
* RBAC and validation.
"""

from __future__ import annotations

import os
import shutil
import socket
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url

from tests.api.conftest import DEMO_PW

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def backup_root(tmp_path_factory, monkeypatch) -> Path:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    root = tmp_path_factory.mktemp("backups")
    monkeypatch.setattr(get_settings(), "backup_allowed_roots", [str(root)])
    return root


async def _run(job: dict) -> dict:  # type: ignore[type-arg]
    from app.backup import service

    done = await service.run(uuid.UUID(job["id"]))
    return {"status": done.status, "error": done.error, "result": done.result, "progress": done.progress}


async def _target(admin, root: Path, name: str) -> dict:  # type: ignore[no-untyped-def, type-arg]
    r = await admin.post(
        "/api/v1/backup/targets", json={"name": name, "kind": "local", "config": {"path": str(root / name)}}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _rules(admin) -> set[str]:  # type: ignore[no-untyped-def]
    data = (await admin.get("/api/v1/alert-rules", params={"page_size": 500})).json()
    return {x["name"] for x in (data["items"] if isinstance(data, dict) else data)}


async def test_platform_backup_verify_restore_end_to_end(admin, backup_root) -> None:  # type: ignore[no-untyped-def]
    key = (await admin.post("/api/v1/backup/keys/generate")).json()
    assert key["recipient"].startswith("age1") and key["identity"].startswith("AGE-SECRET-KEY-1")
    t = await _target(admin, backup_root, "bk-local")
    chk = (await admin.post(f"/api/v1/backup/targets/{t['id']}/check")).json()
    assert chk["ok"] is True, chk
    # an envelope-encrypted secret in the platform (stays encrypted inside the archive)
    siem = await admin.post(
        "/api/v1/platform/siem-destinations",
        json={
            "name": f"bk-siem-{uuid.uuid4().hex[:4]}",
            "kind": "webhook",
            "config": {"url": "https://siem.example.com/in"},
            "signing_secret": "Bk-Signing-Secret-1",
        },
    )
    assert siem.status_code == 201, siem.text

    job = (
        await admin.post(
            "/api/v1/backups/platform",
            json={"target_id": t["id"], "encryption": {"mode": "recipient", "recipient": key["recipient"]}},
        )
    ).json()
    assert job["status"] == "QUEUED" and "sealed" not in job["params"]
    done = await _run(job)
    assert done["status"] == "SUCCESS", done
    bid = done["result"]["backup_ids"][0]
    b = (await admin.get(f"/api/v1/backups/{bid}")).json()
    assert b["status"] == "SUCCESS" and b["encrypted"] is True and b["verification"]["level"] == "checksum"
    assert b["encryption"]["algorithm"].startswith("age") and len(b["sha256"]) == 64
    from app.core.config import get_settings

    assert "inventory" in b["manifest"]["components"]
    assert get_settings().master_key_id in b["manifest"]["key_ids"]  # encrypted values need the master key
    # the archive on disk is age ciphertext; the catalog entry next to it has no secrets
    folder = backup_root / "bk-local" / Path(b["location"]).parent
    assert (folder / "archive.tar.age").read_bytes().startswith(b"age-encryption.org/v1")
    sidecar = (folder / "manifest.json").read_text()
    assert key["identity"] not in sidecar and "Admin-Passw0rd" not in sidecar and "Bk-Signing-Secret-1" not in sidecar

    # content verification needs the identity (supplied for this run only)
    v = await _run((await admin.post(f"/api/v1/backups/{bid}/verify", json={"identity": key["identity"]})).json())
    assert v["status"] == "SUCCESS" and v["result"]["level"] == "content" and v["result"]["rows"] > 0, v

    # change the platform after the backup
    marker = f"bk-probe-{uuid.uuid4().hex[:6]}"
    r = await admin.post(
        "/api/v1/alert-rules",
        json={"name": marker, "kind": "health", "health_states": ["unavailable"], "severity": "low"},
    )
    assert r.status_code == 201, r.text
    assert marker in await _rules(admin)

    # restore wizard: wrong key is refused, the right one shows the exact impact
    other, _ = __import__("app.backup.encryption", fromlist=["x"]).generate_identity()
    bad = await admin.post(f"/api/v1/backups/{bid}/restore/preview", json={"identity": other})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "restore_blocked"
    pv = (await admin.post(f"/api/v1/backups/{bid}/restore/preview", json={"identity": key["identity"]})).json()
    assert pv["integrity"]["ok"] is True and pv["compatibility"]["status"] == "compatible"
    assert pv["keys"]["missing"] == [] and pv["confirm_phrase"] == "RESTORE PLATFORM"
    assert pv["impact"]["error"] is None and pv["impact"]["tables"]["alert_rules"]["deleted"] >= 1
    assert marker in await _rules(admin)  # the preview changed nothing

    no = await admin.post(f"/api/v1/backups/{bid}/restore", json={"identity": key["identity"], "confirm": "yes"})
    assert no.status_code == 428
    rj = (
        await admin.post(
            f"/api/v1/backups/{bid}/restore",
            json={"identity": key["identity"], "confirm": "RESTORE PLATFORM", "components": ["alerting", "settings"]},
        )
    ).json()
    res = await _run(rj)
    assert res["status"] == "SUCCESS", res
    assert marker not in await _rules(admin)
    pre = res["result"]["pre_restore_backup"]
    pre_b = (await admin.get(f"/api/v1/backups/{pre}")).json()
    assert pre_b["status"] == "SUCCESS" and pre_b["manifest"]["purpose"] == "pre_restore"
    assert set(pre_b["manifest"]["components"]) >= {"identity", "inventory", "audit"}
    events = (await admin.get("/api/v1/audit", params={"page_size": 200, "q": "restore"})).json()["items"]
    actions = {e["action"] for e in events}
    assert {"restore.request", "restore.start", "restore.complete"} <= actions
    assert all(key["identity"] not in str(e) for e in events)

    # tampering with the stored archive is detected; a corrupted backup cannot be restored
    arc = folder / "archive.tar.age"
    data = bytearray(arc.read_bytes())
    data[-10] ^= 0xFF
    arc.write_bytes(bytes(data))
    v2 = await _run((await admin.post(f"/api/v1/backups/{bid}/verify", json={"identity": key["identity"]})).json())
    assert v2["status"] == "FAILED"
    assert (await admin.get(f"/api/v1/backups/{bid}")).json()["status"] == "CORRUPTED"
    blocked = await admin.post(
        f"/api/v1/backups/{bid}/restore", json={"identity": key["identity"], "confirm": "RESTORE PLATFORM"}
    )
    assert blocked.status_code == 422

    # deletion needs a typed confirmation and removes the stored folder
    assert (await admin.delete(f"/api/v1/backups/{pre}")).status_code == 428
    assert (await admin.delete(f"/api/v1/backups/{pre}", params={"confirm": pre[:8]})).status_code == 200
    assert (await admin.get(f"/api/v1/backups/{pre}")).json()["status"] == "DELETED"
    assert not (backup_root / "bk-local" / Path(pre_b["location"]).parent).exists()
    await admin.delete(f"/api/v1/platform/siem-destinations/{siem.json()['id']}")


@pytest.fixture
def s3(monkeypatch):  # type: ignore[no-untyped-def]
    from moto.server import ThreadedMotoServer

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    srv.start()
    import boto3

    boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{port}",
        aws_access_key_id="it",
        aws_secret_access_key="it-secret",
        region_name="us-east-1",
    ).create_bucket(Bucket="dbm-backups")
    yield f"http://127.0.0.1:{port}"
    srv.stop()


async def test_s3_passphrase_policy_retention_scheduler_and_dr_scan(admin, s3, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.backup import service
    from app.db.session import session_scope
    from app.models import Backup, BackupPolicy

    monkeypatch.setenv("BK_IT_PASSPHRASE", "it-backup-passphrase-0001")
    r = await admin.post(
        "/api/v1/backup/targets",
        json={
            "name": "bk-s3",
            "kind": "s3",
            "config": {
                "endpoint_url": s3,
                "bucket": "dbm-backups",
                "prefix": "dbm",
                "region": "us-east-1",
                "access_key_id": "it",
                "addressing_style": "path",
            },
            "secret_access_key": "it-secret",
        },
    )
    assert r.status_code == 201 and "it-secret" not in r.text and r.json()["has_secret"], r.text
    t = r.json()
    assert (await admin.post(f"/api/v1/backup/targets/{t['id']}/check")).json()["ok"] is True
    body = {
        "name": "bk-nightly-config",
        "kind": "platform",
        "target_id": t["id"],
        "platform_scope": {"components": ["settings", "alerting"]},
        "schedule": {"frequency": "daily", "time": "01:30", "time_zone": "Europe/Paris"},
        "encryption": {"mode": "passphrase", "passphrase_ref": "env:BK_IT_PASSPHRASE"},
        "retention": {"keep_last": 2},
    }
    pol = (await admin.post("/api/v1/backup/policies", json=body)).json()
    assert pol["next_run_at"] and pol["effective_max_age_hours"] == 26
    ids = []
    for _ in range(3):
        res = await _run((await admin.post(f"/api/v1/backup/policies/{pol['id']}/run")).json())
        assert res["status"] == "SUCCESS", res
        ids.append(res["result"]["backup_ids"][0])
    # passphrase known through the policy reference → content verified at creation
    first_ok = (await admin.get(f"/api/v1/backups/{ids[-1]}")).json()
    assert first_ok["verification"]["level"] == "content" and first_ok["encryption"]["mode"] == "passphrase"
    states = {i: (await admin.get(f"/api/v1/backups/{i}")).json()["status"] for i in ids}
    assert states == {ids[0]: "DELETED", ids[1]: "SUCCESS", ids[2]: "SUCCESS"}
    import boto3

    keys = [
        o["Key"]
        for o in boto3.client(
            "s3", endpoint_url=s3, aws_access_key_id="it", aws_secret_access_key="it-secret", region_name="us-east-1"
        )
        .list_objects_v2(Bucket="dbm-backups")
        .get("Contents", [])
    ]
    assert all(k.startswith("dbm/platform/") for k in keys)
    assert not any(ids[0] in k for k in keys) and sum(k.endswith("key.age") for k in keys) == 2
    prev = (await admin.get(f"/api/v1/backup/policies/{pol['id']}/retention")).json()
    assert prev["remove"] == [] and set(prev["keep"]) == {ids[1], ids[2]}

    # scheduler: a due policy is queued once; an overdue policy is reported
    async with session_scope() as db:
        await db.execute(
            update(BackupPolicy)
            .where(BackupPolicy.id == uuid.UUID(pol["id"]))
            .values(next_run_at=__import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").UTC))
        )
    async with session_scope() as db:
        out = await service.schedule(db)
    assert out["queued"] == 1
    async with session_scope() as db:
        out = await service.schedule(db)
    assert out["queued"] == 0  # not twice while the first run is queued
    jobs = (await admin.get("/api/v1/backup/jobs", params={"active": True})).json()
    queued = [j for j in jobs if j["policy_id"] == pol["id"]]
    assert len(queued) == 1 and queued[0]["trigger"] == "schedule"
    assert (await admin.post(f"/api/v1/backup/jobs/{queued[0]['id']}/cancel")).json()["status"] == "CANCELLED"

    # disaster recovery: the catalog is lost, the storage target is scanned, backups come back
    async with session_scope() as db:
        await db.execute(delete(Backup).where(Backup.id.in_([uuid.UUID(i) for i in ids[1:]])))
    scan = (await admin.post(f"/api/v1/backup/targets/{t['id']}/scan")).json()
    assert scan["registered"] == 2, scan
    back = (await admin.get(f"/api/v1/backups/{ids[2]}")).json()
    assert back["status"] == "VERIFYING" and back["verification"]["level"] == "imported"
    v = await _run(
        (await admin.post(f"/api/v1/backups/{ids[2]}/verify", json={"passphrase": "it-backup-passphrase-0001"})).json()
    )
    assert v["status"] == "SUCCESS" and (await admin.get(f"/api/v1/backups/{ids[2]}")).json()["status"] == "SUCCESS"
    # the policy cannot be deleted from under its target; the target cannot be removed while used
    assert (await admin.delete(f"/api/v1/backup/targets/{t['id']}")).status_code == 409
    assert (await admin.delete(f"/api/v1/backup/policies/{pol['id']}")).status_code == 200
    for i in ids[1:]:
        await admin.delete(f"/api/v1/backups/{i}", params={"confirm": i[:8]})
    assert (await admin.delete(f"/api/v1/backup/targets/{t['id']}")).status_code == 200


async def _pg(sql: str, database: str | None = None) -> list:  # type: ignore[type-arg]
    import asyncpg

    u = make_url(os.environ["DBM_TEST_DATABASE_URL"])
    conn = await asyncpg.connect(
        host=u.host, port=u.port, user=u.username, password=u.password, database=database or u.database
    )
    try:
        if not sql.lstrip().upper().startswith("SELECT"):
            await conn.execute(sql)
            return []
        return list(await conn.fetch(sql))
    finally:
        await conn.close()


@pytest.mark.skipif(not shutil.which("pg_dump"), reason="pg_dump / pg_restore not installed")
async def test_postgresql_database_backup_and_restore_into_a_new_database(admin, backup_root) -> None:  # type: ignore[no-untyped-def]
    u = make_url(os.environ["DBM_TEST_DATABASE_URL"])
    src, dst = f"bk_src_{uuid.uuid4().hex[:6]}", f"bk_dst_{uuid.uuid4().hex[:6]}"
    await _pg(f'CREATE DATABASE "{src}"')
    await _pg(
        "CREATE TABLE orders(id int primary key, note text); INSERT INTO orders SELECT g, 'n'||g FROM generate_series(1,250) g; CREATE VIEW v_orders AS SELECT * FROM orders",
        src,
    )
    r = await admin.post(
        "/api/v1/servers",
        json={
            "name": f"bk-pg-{uuid.uuid4().hex[:6]}",
            "engine": "postgresql",
            "host": u.host or "localhost",
            "port": u.port or 5432,
            "default_database": src,
            "tls_mode": "disable",
            "monitoring_enabled": False,
            "management_mode": "administration",
            "admin_credential": {"username": u.username, "password": u.password},
        },
    )
    assert r.status_code == 201, r.text
    s = r.json()
    t = await _target(admin, backup_root, "bk-db")
    try:
        prot = (await admin.get(f"/api/v1/servers/{s['id']}/backup-protection")).json()
        assert prot["status"] == "not_protected" and prot["capabilities"]["logical"] is True
        job = (
            await admin.post(
                "/api/v1/backups/database",
                json={"target_id": t["id"], "scope": {"server_ids": [s["id"]]}, "encryption": {"mode": "none"}},
            )
        ).json()
        res = await _run(job)
        assert res["status"] == "SUCCESS", res
        assert res["progress"]["assets"][0]["status"] == "SUCCESS" and res["progress"]["done"] == 1
        bid = res["result"]["backup_ids"][0]
        b = (await admin.get(f"/api/v1/backups/{bid}")).json()
        assert b["method"] == "logical" and b["tool"].startswith("pg_dump") and b["database"] == src
        assert b["verification"]["level"] == "engine" and b["verification"]["tables"] >= 1, b["verification"]
        assert u.password not in str(b)
        # a policy covering the server makes it "protected"
        pol = (
            await admin.post(
                "/api/v1/backup/policies",
                json={
                    "name": f"bk-db-{s['name']}",
                    "kind": "database",
                    "target_id": t["id"],
                    "database_scope": {"server_ids": [s["id"]]},
                    "encryption": {"mode": "none"},
                    "schedule": {"frequency": "manual"},
                    "max_age_hours": 24,
                },
            )
        ).json()
        from datetime import UTC, datetime, timedelta

        from app.db.session import session_scope
        from app.models import Backup

        assert (await admin.get(f"/api/v1/servers/{s['id']}/backup-protection")).json()["status"] == "protected"
        async with session_scope() as db:  # the only backup is now older than the policy allows
            await db.execute(
                update(Backup)
                .where(Backup.id == uuid.UUID(bid))
                .values(created_at=datetime.now(UTC) - timedelta(days=2))
            )
        assert (await admin.get(f"/api/v1/servers/{s['id']}/backup-protection")).json()["status"] == "at_risk"
        res2 = await _run((await admin.post(f"/api/v1/backup/policies/{pol['id']}/run")).json())
        assert res2["status"] == "SUCCESS"
        assert (await admin.get(f"/api/v1/servers/{s['id']}/backup-protection")).json()["status"] == "protected"

        pv = (await admin.post(f"/api/v1/backups/{bid}/restore/preview", json={"target_database": dst})).json()
        assert pv["restore_supported"] is True and pv["warnings"] == [] and pv["confirm_phrase"] == dst
        assert (await admin.post(f"/api/v1/backups/{bid}/restore", json={"target_database": dst})).status_code == 428
        rj = (await admin.post(f"/api/v1/backups/{bid}/restore", json={"target_database": dst, "confirm": dst})).json()
        out = await _run(rj)
        assert out["status"] == "SUCCESS", out
        rows = await _pg("SELECT count(*) AS n, max(note) AS m FROM v_orders", dst)
        assert rows[0]["n"] == 250
        # never over an existing database
        again = await _run(
            (await admin.post(f"/api/v1/backups/{bid}/restore", json={"target_database": src, "confirm": src})).json()
        )
        assert again["status"] == "FAILED" and "already exists" in again["error"]
        await admin.delete(f"/api/v1/backup/policies/{pol['id']}")
    finally:
        await admin.delete(f"/api/v1/servers/{s['id']}", params={"confirm": s["name"]})
        for d in (src, dst):
            await _pg(f'DROP DATABASE IF EXISTS "{d}" WITH (FORCE)')


async def test_a_failing_asset_gives_partial_success_and_wrong_credentials_fail(admin, backup_root) -> None:  # type: ignore[no-untyped-def]
    u = make_url(os.environ["DBM_TEST_DATABASE_URL"])
    good = (
        await admin.post(
            "/api/v1/servers",
            json={
                "name": f"bk-ok-{uuid.uuid4().hex[:6]}",
                "engine": "postgresql",
                "host": u.host or "localhost",
                "port": u.port or 5432,
                "default_database": u.database,
                "tls_mode": "disable",
                "monitoring_enabled": False,
                "management_mode": "administration",
                "admin_credential": {"username": u.username, "password": u.password},
            },
        )
    ).json()
    bad = (
        await admin.post(
            "/api/v1/servers",
            json={
                "name": f"bk-bad-{uuid.uuid4().hex[:6]}",
                "engine": "postgresql",
                "host": u.host or "localhost",
                "port": u.port or 5432,
                "default_database": u.database,
                "tls_mode": "disable",
                "monitoring_enabled": False,
                "management_mode": "administration",
                "admin_credential": {"username": u.username, "password": "Wrong-Password-1"},
            },
        )
    ).json()
    t = await _target(admin, backup_root, "bk-bulk")
    try:
        if not shutil.which("pg_dump"):
            pytest.skip("pg_dump not installed")
        job = (
            await admin.post(
                "/api/v1/backups/database",
                json={
                    "target_id": t["id"],
                    "scope": {"server_ids": [good["id"], bad["id"]]},
                    "encryption": {"mode": "none"},
                },
            )
        ).json()
        res = await _run(job)
        assert res["status"] == "PARTIAL_SUCCESS", res
        by = {a["name"].split(" / ")[0]: a for a in res["progress"]["assets"]}
        assert by[good["name"]]["status"] == "SUCCESS" and by[bad["name"]]["status"] == "FAILED"
        assert "Wrong-Password-1" not in str(res)
        failed = (await admin.get("/api/v1/backups", params={"server_id": bad["id"]})).json()["items"]
        assert failed[0]["status"] == "FAILED" and "Wrong-Password-1" not in str(failed)
        ov = (await admin.get("/api/v1/backup/overview")).json()
        assert ov["last_30_days"].get("FAILED", 0) >= 1 and any(
            f["server_name"] == bad["name"] for f in ov["recent_failures"]
        )
    finally:
        for s in (good, bad):
            await admin.delete(f"/api/v1/servers/{s['id']}", params={"confirm": s["name"]})


async def test_backup_permissions_and_validation(admin, make_client, backup_root) -> None:  # type: ignore[no-untyped-def]
    auditor = await make_client(("auditor", DEMO_PW))
    monitor = await make_client(("monitor", DEMO_PW))
    assert (await auditor.get("/api/v1/backups")).status_code == 200  # Security Auditor: backup.view
    assert (await monitor.get("/api/v1/backups")).status_code == 403
    t = await _target(admin, backup_root, "bk-rbac")
    body = {"target_id": t["id"], "encryption": {"mode": "none"}}
    assert (await auditor.post("/api/v1/backups/platform", json=body)).status_code == 403
    assert (
        await auditor.post(
            "/api/v1/backup/targets", json={"name": "x", "kind": "local", "config": {"path": str(backup_root / "x")}}
        )
    ).status_code == 403
    roles = {r["name"]: r for r in (await admin.get("/api/v1/roles")).json()}
    op = set(roles["Backup Operator"]["permissions"])
    assert {"backup.create", "restore.database"} <= op and "restore.platform" not in op and "backup.delete" not in op
    # validation
    outside = await admin.post(
        "/api/v1/backup/targets", json={"name": "out", "kind": "local", "config": {"path": "/etc"}}
    )
    assert outside.status_code == 422 and "outside the allowed" in outside.text
    assert (
        await admin.post(
            "/api/v1/backup/targets", json={"name": "s", "kind": "s3", "config": {"bucket": "b", "secret_key": "x"}}
        )
    ).status_code == 422
    bad_policy = {
        "name": "p",
        "kind": "platform",
        "target_id": t["id"],
        "encryption": {"mode": "passphrase"},
        "schedule": {"frequency": "daily"},
    }
    assert (await admin.post("/api/v1/backup/policies", json=bad_policy)).status_code == 422
    assert (
        await admin.post(
            "/api/v1/backup/policies", json={**bad_policy, "encryption": {"mode": "recipient", "recipient": "nope"}}
        )
    ).status_code == 422
    assert (
        await admin.post(
            "/api/v1/backup/policies",
            json={**bad_policy, "encryption": {"mode": "none"}, "schedule": {"frequency": "daily", "time": "25:00"}},
        )
    ).status_code == 422
    assert (
        await admin.post("/api/v1/backups/platform", json={**body, "scope": {"components": ["everything"]}})
    ).status_code == 422
    caps = (await admin.get("/api/v1/backup/capabilities")).json()
    assert (
        caps["engines"]["db2zos"]["status"] == "EXTERNAL_TOOL_REQUIRED"
        and "inventory" in caps["platform"]["components"]
    )
    await admin.delete(f"/api/v1/backup/targets/{t['id']}")


async def test_backup_tables_exist_after_migrations() -> None:
    from app.db.session import session_scope
    from app.models import BackupTarget

    async with session_scope() as db:
        await db.execute(select(BackupTarget.id).limit(1))
