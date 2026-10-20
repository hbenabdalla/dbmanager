"""Backup & restore building blocks (milestone 0.12) that need no database: table classification,
encryption round trips, storage key safety, archive safety, schedules, retention, compatibility."""

from __future__ import annotations

import io
import json
import tarfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.backup import encryption, engines, platform, service, storage


def test_every_table_is_in_a_component_or_explicitly_excluded() -> None:
    assert platform.unclassified() == [], "classify new tables in app/backup/platform.py (COMPONENTS or EXCLUDED)"
    overlap = set(platform.TABLE_COMPONENT) & set(platform.EXCLUDED)
    assert not overlap
    every = [t for ts in platform.COMPONENTS.values() for t in ts]
    assert len(every) == len(set(every)), "a table belongs to exactly one component"
    # secrets stay encrypted in the archive; transient state is excluded
    assert platform.TABLE_COMPONENT["credentials"] == "inventory"
    assert {"user_sessions", "rate_limit_windows", "backups", "backup_jobs"} <= set(platform.EXCLUDED)
    assert "audit" in platform.MERGE_ONLY and "audit" not in platform.DEFAULT_COMPONENTS


def test_unknown_or_empty_component_selection_is_refused() -> None:
    with pytest.raises(platform.PlatformBackupError, match="unknown"):
        platform.check_components(["inventory", "everything"])
    with pytest.raises(platform.PlatformBackupError):
        platform.check_components([])
    assert platform.check_components(["identity", "organization"]) == ["organization", "identity"]


def test_values_round_trip_through_the_jsonl_encoding() -> None:
    table = platform.tables()["backups"]
    row = {
        "id": uuid.uuid4(),
        "created_at": datetime(2026, 10, 18, 8, 30, tzinfo=UTC),
        "size_bytes": 12,
        "manifest": {"a": [1, 2]},
        "dropped_column": "x",
    }
    encoded = json.loads(json.dumps(row, default=platform._enc))
    back = platform.decode_row(table, encoded)
    assert back["id"] == row["id"] and back["created_at"] == row["created_at"] and back["manifest"] == {"a": [1, 2]}
    assert "dropped_column" not in back
    found: set[str] = set()
    platform._key_ids(
        {
            "secret": {"key_id": "k2", "wrapped_dek": "x", "nonce": "n", "ciphertext": "c"},
            "l": [{"key_id": "k1", "wrapped_dek": "y"}],
        },
        found,
    )
    assert found == {"k1", "k2"}


@pytest.mark.parametrize("mode", ["recipient", "passphrase"])
def test_age_encryption_round_trip(tmp_path: Path, mode: str) -> None:
    src = tmp_path / "a.tar"
    src.write_bytes(b"backup bytes " * 10000)
    identity, recipient = encryption.generate_identity()
    assert recipient.startswith("age1") and identity.startswith("AGE-SECRET-KEY-1")
    out = tmp_path / "a.tar.age"
    wrapped = encryption.encrypt_file(src, out, mode, recipient=recipient, passphrase="correct horse battery")
    assert out.read_bytes()[:21] == b"age-encryption.org/v1"
    assert b"backup bytes" not in out.read_bytes()
    plain = tmp_path / "b.tar"
    if mode == "recipient":
        assert wrapped is None
        encryption.decrypt_file(out, plain, mode, identity=identity)
        other, _ = encryption.generate_identity()
        with pytest.raises(encryption.EncryptionError):
            encryption.decrypt_file(out, tmp_path / "c", mode, identity=other)
    else:
        assert wrapped and wrapped.startswith(b"age-encryption.org/v1")
        encryption.decrypt_file(out, plain, mode, passphrase="correct horse battery", wrapped_key=wrapped)
        with pytest.raises(encryption.EncryptionError, match="wrong passphrase"):
            encryption.decrypt_file(out, tmp_path / "c", mode, passphrase="wrong passphrase!!", wrapped_key=wrapped)
    assert plain.read_bytes() == src.read_bytes()


def test_encryption_input_validation(tmp_path: Path) -> None:
    with pytest.raises(encryption.EncryptionError, match="recipient"):
        encryption.parse_recipient("ssh-rsa AAAA")
    with pytest.raises(encryption.EncryptionError, match="identity"):
        encryption.parse_identity("# comment only")
    (tmp_path / "x").write_bytes(b"x")
    with pytest.raises(encryption.EncryptionError, match="12 characters"):
        encryption.encrypt_file(tmp_path / "x", tmp_path / "y", "passphrase", passphrase="short")
    with pytest.raises(encryption.EncryptionError):
        encryption.resolve_secret_ref("plain-text")


def test_filesystem_storage_stays_inside_allowed_roots(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_allowed_roots", [str(tmp_path / "allowed")])
    (tmp_path / "allowed").mkdir()
    with pytest.raises(storage.StorageError, match="outside the allowed"):
        storage.FilesystemStorage("local", str(tmp_path / "elsewhere"))
    with pytest.raises(storage.StorageError, match="absolute"):
        storage.FilesystemStorage("local", "relative/dir")
    # a symlink pointing outside the root is resolved first
    (tmp_path / "allowed" / "link").symlink_to(tmp_path)
    with pytest.raises(storage.StorageError):
        storage.FilesystemStorage("local", str(tmp_path / "allowed" / "link"))
    fs = storage.FilesystemStorage("local", str(tmp_path / "allowed" / "t1"))
    for bad in ("../x", "/etc/passwd", "a/../../b", "a//", ".hidden/../.."):
        with pytest.raises(storage.StorageError):
            fs._path(bad)
    assert fs._path("platform/2026/10/x/archive.tar").is_relative_to(fs.root)


async def test_filesystem_storage_put_get_list_delete(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_allowed_roots", [str(tmp_path)])
    fs = storage.FilesystemStorage("local", str(tmp_path / "store"))
    info = await fs.check()
    assert info["created"] is True and info["free_bytes"] > 0
    src = tmp_path / "f"
    src.write_bytes(b"hello")
    assert await fs.put(src, "a/b/c.bin") == 5
    assert await fs.size("a/b/c.bin") == 5 and await fs.size("a/b/none") is None
    assert await fs.list("a/") == ["a/b/c.bin"]
    await fs.get("a/b/c.bin", tmp_path / "g")
    assert (tmp_path / "g").read_bytes() == b"hello"
    await fs.delete("a/b/c.bin")
    assert await fs.list("") == [] and not (fs.root / "a").exists()  # empty folders removed
    with pytest.raises(storage.StorageError, match="does not exist"):
        await fs.get("a/b/c.bin", tmp_path / "h")


def _tar(path: Path, entries: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as t:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


@pytest.mark.parametrize("evil", ["../escape.txt", "/abs.txt", "tables/../../x", "other/file", "tables/a/b/c"])
def test_archive_extraction_refuses_unexpected_entries(tmp_path: Path, evil: str) -> None:
    arc = tmp_path / "a.tar"
    _tar(arc, {"manifest.json": json.dumps({"format": platform.FORMAT}).encode(), evil: b"x"})
    with pytest.raises(platform.PlatformBackupError, match="unexpected entry"):
        platform.extract(arc, tmp_path / "out")


def test_archive_checksums_detect_tampering(tmp_path: Path) -> None:
    work = tmp_path / "w"
    (work / "tables").mkdir(parents=True)
    (work / "tables" / "roles.jsonl").write_text('{"id": "x"}\n')
    manifest = {
        "format": platform.FORMAT,
        "tables": {
            "roles": {"sha256": platform.sha256_file(work / "tables" / "roles.jsonl"), "rows": 1, "columns": ["id"]}
        },
    }
    arc = tmp_path / "a.tar"
    platform.build_archive(work, manifest, arc)
    out = tmp_path / "x"
    inner = platform.extract(arc, out)
    assert platform.verify_contents(out, inner) == []
    (out / "tables" / "roles.jsonl").write_text('{"id": "y"}\n')
    assert platform.verify_contents(out, inner) == ["tables/roles.jsonl checksum mismatch"]
    (out / "tables" / "roles.jsonl").unlink()
    assert "missing" in platform.verify_contents(out, inner)[0]
    with pytest.raises(platform.PlatformBackupError, match="unsupported archive format"):
        _tar(arc, {"manifest.json": b'{"format": "other/9"}'})
        platform.extract(arc, tmp_path / "y")


def test_compatibility_statuses() -> None:
    chain = platform.schema_chain()
    head = chain[-1]
    cols = {name: {"columns": [c.name for c in t.columns]} for name, t in platform.tables().items() if name == "roles"}
    assert (
        platform.compatibility({"format": platform.FORMAT, "schema_version": head, "tables": cols})["status"]
        == "compatible"
    )
    older = platform.compatibility({"format": platform.FORMAT, "schema_version": chain[0], "tables": cols})
    assert older["status"] == "migration_required" and "default" in older["notes"][0]
    newer = platform.compatibility({"format": platform.FORMAT, "schema_version": "9999", "tables": {}})
    assert newer["status"] == "unsupported"
    assert platform.compatibility({"format": "x", "schema_version": head})["status"] == "unsupported"
    # a required column with no default that the backup does not have → unsupported
    partial = {"roles": {"columns": ["id"]}}
    assert (
        platform.compatibility({"format": platform.FORMAT, "schema_version": chain[0], "tables": partial})["status"]
        == "unsupported"
    )


def test_schedule_next_run_follows_local_time_and_dst() -> None:
    paris = {"frequency": "daily", "time": "04:00", "time_zone": "Europe/Paris"}
    # 2026-10-25: CEST → CET; 04:00 local is 02:00Z before and 03:00Z after the change
    assert service.next_run(paris, datetime(2026, 10, 23, 12, tzinfo=UTC)) == datetime(2026, 10, 24, 2, 0, tzinfo=UTC)
    assert service.next_run(paris, datetime(2026, 10, 24, 12, tzinfo=UTC)) == datetime(2026, 10, 25, 3, 0, tzinfo=UTC)
    # 02:30 exists twice that night: the run happens once, at the first occurrence
    twice = {**paris, "time": "02:30"}
    assert service.next_run(twice, datetime(2026, 10, 24, 12, tzinfo=UTC)) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    weekly = {"frequency": "weekly", "time": "23:00", "weekday": 1, "time_zone": "UTC"}
    nxt = service.next_run(weekly, datetime(2026, 10, 19, 23, 0, tzinfo=UTC))  # a Monday, exactly at 23:00
    assert nxt == datetime(2026, 10, 26, 23, 0, tzinfo=UTC)
    monthly = {"frequency": "monthly", "time": "01:00", "day_of_month": 31, "time_zone": "UTC"}  # capped at 28
    assert service.next_run(monthly, datetime(2026, 2, 1, tzinfo=UTC)) == datetime(2026, 2, 28, 1, 0, tzinfo=UTC)
    assert service.next_run({"frequency": "manual"}, datetime.now(UTC)) is None


def _b(days_ago: float, status: str = "SUCCESS", pinned: bool = False, now: datetime | None = None) -> SimpleNamespace:
    now = now or datetime(2026, 10, 18, 12, tzinfo=UTC)
    return SimpleNamespace(id=uuid.uuid4(), status=status, pinned=pinned, created_at=now - timedelta(days=days_ago))


def test_retention_rules() -> None:
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    tz = ZoneInfo("UTC")
    daily = [_b(d) for d in range(0, 60)]  # one backup per day for 60 days
    keep = service.retention_keep(daily, {"keep_last": 3}, now, tz)
    assert keep == {b.id for b in daily[:3]}
    keep = service.retention_keep(daily, {"keep_days": 10}, now, tz)
    assert len(keep) == 11  # today … 10 days ago
    keep = service.retention_keep(daily, {"keep_daily": 7, "keep_weekly": 4, "keep_monthly": 2}, now, tz)
    assert {b.id for b in daily[:7]} <= keep and 7 < len(keep) <= 13
    # the newest success is always kept, pinned ones too, failures never count as generations
    failed_newest = [_b(0, "FAILED"), _b(5), _b(30, pinned=True)]
    keep = service.retention_keep(failed_newest, {"keep_last": 0}, now, tz)
    assert failed_newest[1].id in keep and failed_newest[2].id in keep and failed_newest[0].id not in keep
    assert not service._has_rules({"keep_last": 0})


async def test_external_tool_errors_are_redacted(tmp_path: Path) -> None:
    script = tmp_path / "tool.sh"
    script.write_text("#!/bin/sh\necho 'connect failed for password=Sup3r-S3cret' >&2\nexit 3\n")
    script.chmod(0o755)
    with pytest.raises(engines.BackupProviderError) as exc:
        await engines.run_tool([str(script)], secrets=("Sup3r-S3cret",))
    assert "Sup3r-S3cret" not in str(exc.value) and "exit 3" in str(exc.value)


async def test_tools_stop_when_cancelled(tmp_path: Path) -> None:
    script = tmp_path / "slow.sh"
    script.write_text("#!/bin/sh\nsleep 30\n")
    script.chmod(0o755)

    async def yes() -> bool:
        return True

    with pytest.raises(engines.Cancelled):
        await engines.run_tool([str(script)], cancelled=yes)


def test_mysql_option_file_is_private_and_quotes_the_password(tmp_path: Path) -> None:
    server = SimpleNamespace(host="db", port=3306, tls_mode="verify-full", tls_ca_pem=None)
    ctx = engines.Context(server=server, username="bk", password='p"w\\d', database="shop", workdir=tmp_path)
    p = engines.MySQLFamilyProvider()._defaults(ctx, "mysql")
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    text = p.read_text()
    assert 'password="p\\"w\\\\d"' in text and "ssl-mode=VERIFY_IDENTITY" in text
    m = engines.MySQLFamilyProvider()._defaults(ctx, "mariadb").read_text()
    assert "ssl-verify-server-cert" in m


def test_identifiers_and_server_paths_are_validated() -> None:
    for bad in ("x; DROP", "a b", "", "'q'"):
        with pytest.raises(engines.BackupProviderError):
            engines.check_identifier(bad)
    assert engines._SERVER_DIR.match("C:\\Backups\\SQL") and engines._SERVER_DIR.match("/var/opt/mssql/backup")
    for bad in ("relative", "C:\\x';--", "/srv/a|b"):
        assert not engines._SERVER_DIR.match(bad)


async def test_capabilities_never_overclaim() -> None:
    found = {n: {"path": None, "version": None} for n in engines._TOOLS}
    srv = lambda e: SimpleNamespace(engine=e, deployment="self-managed", host="h", is_demo=False)  # noqa: E731
    pg = await engines.capabilities(srv("postgresql"), found)
    assert pg["status"] == engines.TOOL_MISSING and pg["pitr"] is False and pg["physical"] is False
    zos = await engines.capabilities(srv("db2zos"), found)
    assert zos["status"] == engines.EXTERNAL_TOOL_REQUIRED and not zos["restore"]
    db2 = await engines.capabilities(srv("db2"), found)
    assert db2["restore"] is False and "z/OS" in " ".join(db2["notes"])
    ora = await engines.capabilities(srv("oracle"), found)
    assert "RMAN" in " ".join(ora["notes"]) and ora["physical"] is False
    azure = await engines.capabilities(
        SimpleNamespace(engine="mssql", deployment="azure-sql", host="x", is_demo=False), found
    )
    assert azure["status"] == engines.NOT_SUPPORTED
    demo = await engines.capabilities(
        SimpleNamespace(engine="postgresql", deployment="self-managed", host="h", is_demo=True), found
    )
    assert demo["status"] == engines.NOT_SUPPORTED


def test_schedule_in_the_skipped_spring_forward_hour_runs_once_that_day() -> None:
    # 2027-03-28 02:00 → 03:00 in Paris: 02:30 does not exist. The run happens once, at 03:30
    # local (01:30Z), and the next days return to 02:30 CEST (00:30Z).
    s = {"frequency": "daily", "time": "02:30", "time_zone": "Europe/Paris"}
    runs, at = [], datetime(2027, 3, 27, 12, tzinfo=UTC)
    for _ in range(3):
        at = service.next_run(s, at)
        runs.append(at)
    assert runs == [
        datetime(2027, 3, 28, 1, 30, tzinfo=UTC),
        datetime(2027, 3, 29, 0, 30, tzinfo=UTC),
        datetime(2027, 3, 30, 0, 30, tzinfo=UTC),
    ]
