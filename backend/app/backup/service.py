"""BackupService: asynchronous backup, verification, restore and retention jobs.

Jobs live in ``backup_jobs`` and are executed by the worker (``BackgroundRunner``) outside any
long transaction; every step commits its own short unit of work so the history shows progress.

Life cycle of a backup record::

    CREATING ──► VERIFYING ──► SUCCESS
        │             └──────► CORRUPTED   (the stored copy does not match what was written)
        └──────────────────►  FAILED

``SUCCESS`` is only set after the copy in the storage target was read back and its SHA-256
matched (plus content or engine checks where possible) — never just because a command returned.

The scheduler job ``backup_scheduler`` (every minute, on the scheduler leader) queues policy runs
when they are due, marks jobs of lost workers as failed and raises "no successful backup within
policy" notifications.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import tempfile
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.backup import encryption, engines, platform, storage
from app.core.config import get_settings
from app.core.crypto import CryptoError, get_keyring
from app.core.logging import request_id_ctx
from app.core.metrics import (
    BACKUP_BYTES,
    BACKUP_DURATION,
    BACKUP_FAILURES,
    BACKUP_JOBS,
    BACKUP_LAST_SUCCESS,
    RESTORE_FAILURES,
    RESTORE_JOBS,
)
from app.db.session import session_scope
from app.models import Backup, BackupJob, BackupPolicy, BackupTarget, NotificationChannel, Server
from app.models.backup import JOB_ACTIVE
from app.services import audit

log = logging.getLogger("app.backup")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
DB_FORMAT = "dbmanager-database/1"
JOB_KINDS = ("platform_backup", "database_backup", "verify", "platform_restore", "database_restore", "retention")
RESTORE_KINDS = ("platform_restore", "database_restore")
FREQUENCIES = ("manual", "daily", "weekly", "monthly")


class BackupError(Exception):
    pass


class JobCancelled(BackupError):
    pass


# ---- secrets supplied for one run ----------------------------------------------------------------
def _job_aad(job_id: object) -> str:
    return f"backup-job:{job_id}"


def seal_run_secrets(job_id: uuid.UUID, values: dict[str, str | None]) -> str | None:
    clean = {k: v for k, v in values.items() if v}
    return get_keyring().seal_json(clean, _job_aad(job_id)) if clean else None


def _run_secrets(job: BackupJob) -> dict[str, str]:
    sealed = (job.params or {}).get("sealed")
    if not sealed:
        return {}
    try:
        return get_keyring().open_json(sealed, _job_aad(job.id))
    except CryptoError:
        return {}


def _identity_from_settings() -> str | None:
    path = get_settings().backup_identity_file
    if not path:
        return None
    try:
        return Path(path).read_text()
    except OSError:
        return None


# ---- small helpers -------------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(UTC)


def _workdir() -> Path:
    base = get_settings().backup_work_dir or None
    if base:
        Path(base).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="dbm-backup-", dir=base))


def _prefix(kind: str, backup_id: uuid.UUID, created: datetime) -> str:
    return f"{kind}/{created:%Y/%m}/{backup_id}"


async def _schema_version(db: AsyncSession) -> str | None:
    return (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()


async def _job(job_id: uuid.UUID) -> BackupJob:
    async with session_scope() as db:
        job = await db.get(BackupJob, job_id)
        if job is None:
            raise BackupError("job not found")
        db.expunge(job)
        return job


async def _update_job(job_id: uuid.UUID, **values: Any) -> None:
    async with session_scope() as db:
        await db.execute(update(BackupJob).where(BackupJob.id == job_id).values(heartbeat_at=_now(), **values))


async def _progress(job_id: uuid.UUID, progress: dict[str, Any], status: str | None = None) -> None:
    vals: dict[str, Any] = {"progress": progress}
    if status:
        vals["status"] = status
    await _update_job(job_id, **vals)


async def _cancel_requested(job_id: uuid.UUID) -> bool:
    async with session_scope() as db:
        return bool(
            (await db.execute(select(BackupJob.cancel_requested).where(BackupJob.id == job_id))).scalar_one_or_none()
        )


async def _save_backup(backup_id: uuid.UUID, **values: Any) -> None:
    async with session_scope() as db:
        await db.execute(update(Backup).where(Backup.id == backup_id).values(**values))


async def _target(target_id: uuid.UUID | None) -> BackupTarget:
    async with session_scope() as db:
        t = await db.get(BackupTarget, target_id) if target_id else None
        if t is None:
            raise BackupError("the storage target no longer exists")
        if not t.enabled:
            raise BackupError(f"storage target {t.name} is disabled")
        db.expunge(t)
        return t


def _encryption_settings(enc: dict[str, Any], run: dict[str, str]) -> tuple[str, str | None, str | None]:
    """(mode, recipient, passphrase) for a new backup."""
    mode = enc.get("mode") or "none"
    if mode == "recipient":
        return mode, enc.get("recipient"), None
    if mode == "passphrase":
        pw = run.get("passphrase")
        if not pw and enc.get("passphrase_ref"):
            pw = encryption.resolve_secret_ref(str(enc["passphrase_ref"]))
        if not pw:
            raise BackupError("passphrase encryption is selected but no passphrase was supplied")
        return mode, None, pw
    return "none", None, None


async def _decrypt_material(job: BackupJob | None, backup: Backup) -> dict[str, str | None]:
    """Keys to read an encrypted backup: supplied for this run, the policy's passphrase reference,
    or ``DBM_BACKUP_IDENTITY_FILE``. Missing values stay None (checksum-only verification)."""
    run = _run_secrets(job) if job else {}
    mode = (backup.encryption or {}).get("mode")
    out: dict[str, str | None] = {"passphrase": run.get("passphrase"), "identity": run.get("identity")}
    if mode == "passphrase" and not out["passphrase"] and backup.policy_id:
        async with session_scope() as db:
            pol = await db.get(BackupPolicy, backup.policy_id)
        ref = (pol.encryption or {}).get("passphrase_ref") if pol else None
        if ref:
            with suppress(encryption.EncryptionError):
                out["passphrase"] = encryption.resolve_secret_ref(str(ref))
    if mode == "recipient" and not out["identity"]:
        out["identity"] = _identity_from_settings()
    return out


async def _fetch_plain(
    prov: storage.StorageProvider, backup: Backup, workdir: Path, keys: dict[str, str | None], *, need_plain: bool
) -> tuple[Path, str, Path | None]:
    """Download the stored object, check its SHA-256 and decrypt it when possible.

    Returns (downloaded file, actual sha256, plaintext file or None when it cannot be decrypted)."""
    assert backup.location
    local = workdir / Path(backup.location).name
    await prov.get(backup.location, local)
    actual = await asyncio.to_thread(platform.sha256_file, local)
    if not backup.encrypted:
        return local, actual, local
    mode = (backup.encryption or {}).get("mode")
    plain = workdir / Path(backup.location).name.removesuffix(".age")
    wrapped = None
    if mode == "passphrase":
        key_obj = (backup.manifest or {}).get("key_object")
        if key_obj:
            kpath = workdir / "key.age"
            await prov.get(key_obj, kpath)
            wrapped = kpath.read_bytes()
    can = (mode == "passphrase" and keys.get("passphrase") and wrapped) or (
        mode == "recipient" and keys.get("identity")
    )
    if not can:
        if need_plain:
            what = "passphrase" if mode == "passphrase" else "age identity (AGE-SECRET-KEY-1…)"
            raise BackupError(f"this backup is encrypted: supply its {what}")
        return local, actual, None
    await asyncio.to_thread(
        encryption.decrypt_file,
        local,
        plain,
        str(mode),
        identity=keys.get("identity"),
        passphrase=keys.get("passphrase"),
        wrapped_key=wrapped,
    )
    return local, actual, plain


async def _store(
    prov: storage.StorageProvider, file: Path, prefix: str, enc: tuple[str, str | None, str | None]
) -> dict[str, Any]:
    """Encrypt (if configured) and upload; returns location, size, sha256, encryption metadata."""
    mode, recipient, passphrase = enc
    out: dict[str, Any] = {"encrypted": mode != "none", "encryption": {"mode": mode}}
    upload, key_obj = file, None
    if mode != "none":
        upload = file.with_name(file.name + ".age")
        wrapped = await asyncio.to_thread(
            encryption.encrypt_file, file, upload, mode, recipient=recipient, passphrase=passphrase
        )
        out["encryption"] = {
            "mode": mode,
            "algorithm": encryption.ALGORITHM[mode],
            "recipient": recipient,
        }
        if wrapped is not None:
            kfile = file.with_name("key.age")
            kfile.write_bytes(wrapped)
            key_obj = f"{prefix}/key.age"
            await prov.put(kfile, key_obj)
    out["sha256"] = await asyncio.to_thread(platform.sha256_file, upload)
    out["location"] = f"{prefix}/{upload.name}"
    out["size_bytes"] = await prov.put(upload, out["location"])
    out["key_object"] = key_obj
    return out


async def _write_sidecar(prov: storage.StorageProvider, prefix: str, manifest: dict[str, Any], workdir: Path) -> str:
    """The unencrypted catalog entry next to the archive (no secrets): lets a new installation
    find and register the backup (disaster recovery) without the database."""
    p = workdir / "manifest.json"
    p.write_text(json.dumps(manifest, indent=2, default=str))
    key = f"{prefix}/manifest.json"
    await prov.put(p, key)
    return key


def _outer_manifest(b: Backup, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": platform.FORMAT if b.kind == "platform" else DB_FORMAT,
        "backup_id": str(b.id),
        "type": b.kind,
        "created_at": b.created_at.isoformat(),
        "created_by": b.created_by,
        "dbm_version": b.app_version,
        "schema_version": b.schema_version,
        "method": b.method,
        "tool": b.tool,
        "server": {"id": str(b.server_id) if b.server_id else None, "name": b.server_name, "engine": b.engine}
        if b.kind == "database"
        else None,
        "database": b.database_name,
        **extra,
    }


async def _verify_stored(prov: storage.StorageProvider, location: str, expect_sha: str, expect_size: int) -> None:
    size = await prov.size(location)
    if size is None:
        raise BackupError("the backup object is not in the storage target after upload")
    if size != expect_size:
        raise _Corrupted(f"stored size {size} differs from the {expect_size} bytes written")


class _Corrupted(BackupError):
    pass


def _audit_kw(job: BackupJob) -> dict[str, Any]:
    return {"username": job.requested_by or "scheduler", "auth_source": "system", "category": "backup"}


# ---- platform backup -----------------------------------------------------------------------------
async def _metrics_export(workdir: Path, days: int) -> dict[str, Any]:
    """VictoriaMetrics native export of the telemetry (``/api/v1/export/native``)."""
    from app.core.tlsclient import timeseries_verify

    s = get_settings()
    if not s.timeseries_enabled or s.timeseries_backend != "victoriametrics":
        return {"included": False, "reason": "time-series storage is not VictoriaMetrics"}
    out = workdir / "metrics" / "victoriametrics.native"
    out.parent.mkdir(parents=True, exist_ok=True)
    params: dict[str, Any] = {"match[]": '{__name__=~".+"}'}
    if days > 0:
        params["start"] = int(time.time() - days * 86400)
    auth = (
        httpx.BasicAuth(s.timeseries_username, s.timeseries_password.get_secret_value())
        if s.timeseries_username
        else None
    )
    try:
        async with (
            httpx.AsyncClient(
                base_url=s.timeseries_url.rstrip("/"), auth=auth, timeout=600, verify=timeseries_verify(s)
            ) as c,
            c.stream("GET", "/api/v1/export/native", params=params) as r,
        ):
            if r.status_code != 200:
                raise BackupError(f"VictoriaMetrics export answered HTTP {r.status_code}")
            with open(out, "wb") as f:
                async for chunk in r.aiter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as exc:
        raise BackupError(f"VictoriaMetrics export failed: {exc}") from exc
    return {
        "included": True,
        "format": "victoriametrics-native",
        "days": days or "all",
        "path": "metrics/victoriametrics.native",
        "size": out.stat().st_size,
        "sha256": platform.sha256_file(out),
    }


async def create_platform_backup(
    *,
    target_id: uuid.UUID,
    components: list[str],
    enc_cfg: dict[str, Any],
    run_secrets: dict[str, str],
    created_by: str | None,
    job_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
    metrics: str = "none",
    metrics_days: int = 0,
    purpose: str = "backup",
) -> uuid.UUID:
    """Create, store and verify one platform backup. Returns the backup id (status set)."""
    components = platform.check_components(components)
    target = await _target(target_id)
    prov = storage.provider_for(target)
    enc = _encryption_settings(enc_cfg, run_secrets)
    bid = uuid.uuid4()
    async with session_scope() as db:
        schema = await _schema_version(db)
        b = Backup(
            id=bid,
            kind="platform",
            status="CREATING",
            job_id=job_id,
            policy_id=policy_id,
            target_id=target.id,
            method="platform",
            tool=f"DBManager {__version__}",
            app_version=__version__,
            schema_version=schema,
            format_version=platform.FORMAT,
            created_by=created_by,
            encrypted=enc[0] != "none",
            encryption={"mode": enc[0]},
            manifest={"components": components, "purpose": purpose},
        )
        db.add(b)
    created = _now()
    workdir = _workdir()
    start = time.perf_counter()
    try:
        async with session_scope() as db:
            info = await platform.export_tables(db, components, workdir)

        async def checkpoint() -> None:
            # a cancel request stops the backup between steps (nothing half-stored remains)
            if job_id and await _cancel_requested(job_id):
                raise JobCancelled("cancelled by user")

        await checkpoint()
        files: list[dict[str, Any]] = []
        metrics_info: dict[str, Any] = {"included": False}
        if metrics == "export":
            metrics_info = await _metrics_export(workdir, metrics_days)
            if metrics_info.get("included"):
                files.append(
                    {"path": metrics_info["path"], "sha256": metrics_info["sha256"], "size": metrics_info["size"]}
                )
        inner = {
            "format": platform.FORMAT,
            "backup_id": str(bid),
            "type": "platform",
            "created_at": created.isoformat(),
            "created_by": created_by,
            "dbm_version": __version__,
            "schema_version": schema,
            "components": components,
            "tables": info["tables"],
            "files": files,
            "metrics": metrics_info,
            "key_ids": info["key_ids"],
            "master_key_note": "Encrypted values in this archive need the DBManager master key(s) listed in "
            "key_ids. The master key itself is not included.",
            "excluded": platform.EXCLUDED,
        }
        archive = workdir / "archive.tar"
        await asyncio.to_thread(platform.build_archive, workdir, inner, archive)
        await checkpoint()
        prefix = _prefix("platform", bid, created)
        stored = await _store(prov, archive, prefix, enc)
        await _save_backup(
            bid,
            status="VERIFYING",
            location=stored["location"],
            size_bytes=stored["size_bytes"],
            sha256=stored["sha256"],
            encryption=stored["encryption"],
        )
        await _verify_stored(prov, stored["location"], stored["sha256"], stored["size_bytes"])
        summary = {
            "components": components,
            "tables": {k: {"rows": v["rows"], "component": v["component"]} for k, v in info["tables"].items()},
            "key_ids": info["key_ids"],
            "metrics": metrics_info,
            "purpose": purpose,
            "key_object": stored["key_object"],
            "archive": {"sha256": stored["sha256"], "size": stored["size_bytes"], "object": stored["location"]},
            "encryption": stored["encryption"],
        }
        async with session_scope() as db:
            b = await db.get(Backup, bid)
            assert b is not None
            b.manifest = summary
            await _write_sidecar(prov, prefix, _outer_manifest(b, summary), workdir)
        # read the stored copy back: checksum, and contents when it can be decrypted
        async with session_scope() as db:
            b = await db.get(Backup, bid)
            assert b is not None
            db.expunge(b)
        keys = {"passphrase": enc[2], "identity": run_secrets.get("identity") or _identity_from_settings()}
        verification = await _verify_platform(prov, b, workdir / "verify", keys)
        status = "SUCCESS" if verification["ok"] else "CORRUPTED"
        await _save_backup(
            bid,
            status=status,
            verification=verification,
            verified_at=_now(),
            finished_at=_now(),
            error=None if verification["ok"] else "; ".join(verification.get("problems", []))[:2000],
        )
        BACKUP_BYTES.labels("platform").inc(stored["size_bytes"])
        if status == "SUCCESS":
            BACKUP_LAST_SUCCESS.labels(str(policy_id) if policy_id else "manual").set(time.time())
        else:
            BACKUP_FAILURES.labels("platform").inc()
    except JobCancelled as exc:
        await _save_backup(bid, status="FAILED", error=f"cancelled: {exc}", finished_at=_now())
        raise
    except Exception as exc:
        await _save_backup(bid, status="FAILED", error=_err(exc), finished_at=_now())
        BACKUP_FAILURES.labels("platform").inc()
        raise
    finally:
        BACKUP_DURATION.labels("platform").observe(time.perf_counter() - start)
        shutil.rmtree(workdir, ignore_errors=True)
    return bid


def _err(exc: BaseException) -> str:
    from app.core import redact

    return redact.text(
        f"{exc}" if isinstance(exc, BackupError | storage.StorageError) else f"{type(exc).__name__}: {exc}"
    )[:2000]


async def _verify_platform(
    prov: storage.StorageProvider, b: Backup, workdir: Path, keys: dict[str, str | None]
) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"at": _now().isoformat(), "problems": []}
    try:
        _, actual, plain = await _fetch_plain(prov, b, workdir, keys, need_plain=False)
    except (storage.StorageError, encryption.EncryptionError) as exc:
        return {**out, "ok": False, "level": "checksum", "problems": [str(exc)]}
    if actual != b.sha256:
        return {**out, "ok": False, "level": "checksum", "problems": ["SHA-256 of the stored archive does not match"]}
    if plain is None:
        return {
            **out,
            "ok": True,
            "level": "checksum",
            "note": "the stored archive matches its SHA-256; contents not checked (no decryption key available)",
        }
    extract = workdir / "x"
    extract.mkdir()
    try:
        inner = await asyncio.to_thread(platform.extract, plain, extract)
    except platform.PlatformBackupError as exc:
        return {**out, "ok": False, "level": "content", "problems": [str(exc)]}
    problems = await asyncio.to_thread(platform.verify_contents, extract, inner)
    if str(inner.get("backup_id")) != str(b.id):
        problems.append("the archive belongs to another backup id")
    return {
        **out,
        "ok": not problems,
        "level": "content",
        "problems": problems,
        "tables": len(inner.get("tables") or {}),
        "rows": sum(int(t.get("rows", 0)) for t in (inner.get("tables") or {}).values()),
    }


# ---- database backups ----------------------------------------------------------------------------
async def scope_servers(db: AsyncSession, scope: dict[str, Any]) -> list[Server]:
    from app.services.alerting import tag_matches

    q = select(Server).where(Server.is_demo.is_(False))
    conds = []
    if scope.get("server_ids"):
        conds.append(Server.id.in_([uuid.UUID(str(x)) for x in scope["server_ids"]]))
    if scope.get("environment_ids"):
        conds.append(Server.environment_id.in_([uuid.UUID(str(x)) for x in scope["environment_ids"]]))
    if scope.get("site_ids"):
        conds.append(Server.site_id.in_([uuid.UUID(str(x)) for x in scope["site_ids"]]))
    tags = list(scope.get("tags") or [])
    if not conds and not tags:
        return []
    rows = list((await db.execute(q.order_by(Server.name))).scalars())
    matched = []
    for s in rows:
        ok = False
        if conds:
            ok = bool((await db.execute(select(Server.id).where(Server.id == s.id, or_(*conds)))).first())
        if not ok and tags:
            ok = any(tag_matches(t, s) for t in tags)
        if ok:
            matched.append(s)
    return matched


async def _admin_context(
    server_id: uuid.UUID, database: str, workdir: Path, options: dict[str, Any], job_id: uuid.UUID | None
) -> engines.Context:
    from app.services import connections, secrets

    async with session_scope() as db:
        server = await db.get(Server, server_id)
        if server is None:
            raise BackupError("the server no longer exists")
        cred = await secrets.get_credential(db, server.id, "admin")
        if cred is None:
            raise BackupError("an administration credential is required to back up or restore this server")
        password = await secrets.resolve_password_async(cred)
        username = cred.username
        db.expunge(server)

    async def _adapter() -> Any:
        from app.adapters.registry import create_adapter

        return create_adapter(server.engine, connections.build_spec(server, username, password))

    async def _cancelled() -> bool:
        return bool(job_id) and await _cancel_requested(job_id)  # type: ignore[arg-type]

    return engines.Context(
        server=server,
        username=username,
        password=password,
        database=database,
        workdir=workdir,
        options=options,
        adapter=_adapter,
        cancelled=_cancelled,
    )


async def create_database_backup(
    *,
    server_id: uuid.UUID,
    database: str,
    target_id: uuid.UUID,
    enc_cfg: dict[str, Any],
    run_secrets: dict[str, str],
    created_by: str | None,
    options: dict[str, Any],
    job_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
) -> uuid.UUID:
    target = await _target(target_id)
    prov = storage.provider_for(target)
    enc = _encryption_settings(enc_cfg, run_secrets)
    bid = uuid.uuid4()
    async with session_scope() as db:
        server = await db.get(Server, server_id)
        if server is None:
            raise BackupError("the server no longer exists")
        p = engines.provider(server.engine)
        b = Backup(
            id=bid,
            kind="database",
            status="CREATING",
            job_id=job_id,
            policy_id=policy_id,
            target_id=target.id,
            server_id=server.id,
            server_name=server.name,
            engine=server.engine,
            database_name=database,
            method="logical",
            app_version=__version__,
            schema_version=await _schema_version(db),
            format_version=DB_FORMAT,
            created_by=created_by,
            encrypted=False,
            encryption={"mode": "none"},
            manifest={},
        )
        db.add(b)
    created = _now()
    workdir = _workdir()
    start = time.perf_counter()
    try:
        if not p.supports_backup:
            caps = await engines.capabilities(server)
            raise BackupError(f"{caps['status']}: " + " ".join(caps["notes"]))
        ctx = await _admin_context(server_id, database, workdir, {**options, "tag": bid.hex[:12]}, job_id)
        outcome = await p.backup(ctx)
        values: dict[str, Any] = {"method": outcome.method, "tool": outcome.tool[:128]}
        details: dict[str, Any] = {**outcome.details, "server_version": server.version}
        if outcome.file is not None:
            prefix = _prefix("database", bid, created)
            stored = await _store(prov, outcome.file, prefix, enc)
            values.update(
                status="VERIFYING",
                location=stored["location"],
                location_kind="target",
                size_bytes=stored["size_bytes"],
                sha256=stored["sha256"],
                encrypted=stored["encrypted"],
                encryption=stored["encryption"],
            )
            await _save_backup(bid, **values)
            await _verify_stored(prov, stored["location"], stored["sha256"], stored["size_bytes"])
            details.update(
                key_object=stored["key_object"],
                archive={"sha256": stored["sha256"], "size": stored["size_bytes"], "object": stored["location"]},
                encryption=stored["encryption"],
            )
        else:
            values.update(
                status="VERIFYING",
                location=outcome.server_location,
                location_kind="server",
                size_bytes=outcome.size_bytes,
            )
            details["note"] = (
                "Written on the database server; DBManager records and verifies it. Copy it to protected storage."
            )
            await _save_backup(bid, **values)
        async with session_scope() as db:
            b = await db.get(Backup, bid)
            assert b is not None
            b.manifest = details
            if b.location_kind == "target":
                await _write_sidecar(prov, _prefix("database", bid, created), _outer_manifest(b, details), workdir)
            db.expunge(b)
        keys = {"passphrase": enc[2], "identity": run_secrets.get("identity") or _identity_from_settings()}
        verification = await _verify_database(prov, b, ctx, keys)
        status = "SUCCESS" if verification["ok"] else "CORRUPTED"
        await _save_backup(
            bid,
            status=status,
            verification=verification,
            verified_at=_now(),
            finished_at=_now(),
            error=None if verification["ok"] else "; ".join(verification.get("problems", []))[:2000],
        )
        if outcome.size_bytes or values.get("size_bytes"):
            BACKUP_BYTES.labels("database").inc(int(values.get("size_bytes") or outcome.size_bytes or 0))
        if status == "SUCCESS":
            BACKUP_LAST_SUCCESS.labels(str(policy_id) if policy_id else "manual").set(time.time())
        else:
            BACKUP_FAILURES.labels("database").inc()
    except Exception as exc:
        await _save_backup(bid, status="FAILED", error=_err(exc), finished_at=_now())
        BACKUP_FAILURES.labels("database").inc()
        raise
    finally:
        BACKUP_DURATION.labels("database").observe(time.perf_counter() - start)
        shutil.rmtree(workdir, ignore_errors=True)
    return bid


async def _verify_database(
    prov: storage.StorageProvider | None, b: Backup, ctx: engines.Context, keys: dict[str, str | None]
) -> dict[str, Any]:
    out: dict[str, Any] = {"at": _now().isoformat(), "problems": []}
    p = engines.provider(b.engine or "")
    local = None
    if b.location_kind == "target":
        assert prov is not None
        vdir = ctx.workdir / "verify"
        vdir.mkdir(parents=True, exist_ok=True)
        try:
            _, actual, local = await _fetch_plain(prov, b, vdir, keys, need_plain=False)
        except (storage.StorageError, encryption.EncryptionError) as exc:
            return {**out, "ok": False, "level": "checksum", "problems": [str(exc)]}
        if actual != b.sha256:
            return {**out, "ok": False, "level": "checksum", "problems": ["SHA-256 of the stored dump does not match"]}
        if local is None:
            return {
                **out,
                "ok": True,
                "level": "checksum",
                "note": "checksum verified; dump not readable without its key",
            }
    try:
        detail = await p.verify(ctx, b, local)
    except engines.BackupProviderError as exc:
        return {**out, "ok": False, "level": "engine", "problems": [str(exc)]}
    return {**out, "ok": True, "level": detail.pop("level", "engine"), **detail}


# ---- job execution -------------------------------------------------------------------------------
async def create_job(
    db: AsyncSession,
    kind: str,
    *,
    params: dict[str, Any],
    requested_by: str | None,
    trigger: str = "manual",
    policy_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    run_secrets: dict[str, str | None] | None = None,
) -> BackupJob:
    if kind not in JOB_KINDS:
        raise BackupError(f"unknown job kind {kind}")
    jid = uuid.uuid4()
    p = dict(params)
    sealed = seal_run_secrets(jid, run_secrets or {})
    if sealed:
        p["sealed"] = sealed
    job = BackupJob(
        id=jid,
        kind=kind,
        status="QUEUED",
        trigger=trigger,
        policy_id=policy_id,
        target_id=target_id,
        params=p,
        progress={},
        requested_by=requested_by,
        correlation_id=request_id_ctx.get() or uuid.uuid4().hex,
    )
    db.add(job)
    await db.flush()
    return job


async def claim() -> uuid.UUID | None:
    async with session_scope() as db:
        sub = (
            select(BackupJob.id)
            .where(BackupJob.status == "QUEUED")
            .order_by(BackupJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return (
            await db.execute(
                update(BackupJob)
                .where(BackupJob.id.in_(sub))
                .values(status="RUNNING", locked_by=WORKER_ID, started_at=func.now(), heartbeat_at=func.now())
                .returning(BackupJob.id)
            )
        ).scalar_one_or_none()


async def run(job_id: uuid.UUID) -> BackupJob:
    """Execute a claimed (or, in tests, a queued) job to completion."""
    job = await _job(job_id)
    if job.status == "QUEUED":
        await _update_job(job_id, status="RUNNING", locked_by=WORKER_ID, started_at=_now())
    token = request_id_ctx.set(job.correlation_id)
    start = time.perf_counter()

    async def _beat() -> None:  # keeps the job "alive" while a long engine statement runs in a thread
        while True:
            await asyncio.sleep(60)
            with suppress(Exception):
                await _update_job(job_id)

    beat = asyncio.create_task(_beat())
    status, error, result = "SUCCESS", None, None
    try:
        handler = {
            "platform_backup": _run_platform_backup,
            "database_backup": _run_database_backup,
            "verify": _run_verify,
            "platform_restore": _run_platform_restore,
            "database_restore": _run_database_restore,
            "retention": _run_retention,
        }[job.kind]
        status, result = await handler(job)
    except JobCancelled as exc:
        status, error = "CANCELLED", str(exc)
    except Exception as exc:  # noqa: BLE001 - recorded on the job
        status, error = "FAILED", _err(exc)
        if isinstance(exc, storage.StorageError):
            result = {"storage_errors": 1}
        log.warning("backup job failed", extra={"job_id": str(job_id), "kind": job.kind, "error": error})
    finally:
        beat.cancel()
        request_id_ctx.reset(token)
    params = {k: v for k, v in (job.params or {}).items() if k != "sealed"}  # run secrets end with the job
    await _update_job(job_id, status=status, error=error, result=result, finished_at=_now(), params=params)
    elapsed = time.perf_counter() - start
    if job.kind in RESTORE_KINDS:
        RESTORE_JOBS.labels(job.kind, status).inc()
        if status == "FAILED":
            RESTORE_FAILURES.labels(job.kind).inc()
    else:
        BACKUP_JOBS.labels(job.kind, status).inc()
    async with session_scope() as db:
        if job.policy_id and job.kind in ("platform_backup", "database_backup"):
            await db.execute(update(BackupPolicy).where(BackupPolicy.id == job.policy_id).values(last_status=status))
    if status in ("FAILED", "PARTIAL_SUCCESS", "CANCELLED") or (
        job.kind == "verify" and (result or {}).get("ok") is False
    ):
        await _notify_job(job, status, error, result)
    log.info(
        "backup job finished", extra={"job_id": str(job_id), "kind": job.kind, "status": status, "s": round(elapsed, 1)}
    )
    return await _job(job_id)


async def _run_platform_backup(job: BackupJob) -> tuple[str, dict[str, Any]]:
    p = job.params
    await _progress(job.id, {"step": "exporting", "total": 1, "done": 0})
    enc_cfg, components, metrics, days = await _policy_or_params(job)
    try:
        bid = await create_platform_backup(
            target_id=job.target_id,  # type: ignore[arg-type]
            components=components,
            enc_cfg=enc_cfg,
            run_secrets=_run_secrets(job),
            created_by=job.requested_by,
            job_id=job.id,
            policy_id=job.policy_id,
            metrics=metrics,
            metrics_days=days,
            purpose=p.get("purpose", "backup"),
        )
    except Exception as exc:
        await audit.record(
            "backup.create",
            result="failure",
            failure_reason=_err(exc),
            target_type="platform",
            details={"job_id": str(job.id)},
            **_audit_kw(job),
        )
        raise
    async with session_scope() as db:
        b = await db.get(Backup, bid)
        assert b is not None
        status = b.status
    await audit.record(
        "backup.create",
        result="success" if status == "SUCCESS" else "failure",
        failure_reason=None if status == "SUCCESS" else f"backup {status}",
        target_type="backup",
        target_id=str(bid),
        details={"job_id": str(job.id), "type": "platform", "components": components, "status": status},
        **_audit_kw(job),
    )
    await _progress(
        job.id,
        {
            "step": "done",
            "total": 1,
            "done": 1,
            "assets": [{"key": "platform", "name": "DBManager platform", "status": status, "backup_id": str(bid)}],
        },
    )
    if job.policy_id and status == "SUCCESS":
        await _apply_retention_safely(job.policy_id, job)
    return ("SUCCESS" if status == "SUCCESS" else "FAILED"), {"backup_ids": [str(bid)], "status": status}


async def _policy_or_params(job: BackupJob) -> tuple[dict[str, Any], list[str], str, int]:
    p = job.params
    if job.policy_id:
        async with session_scope() as db:
            pol = await db.get(BackupPolicy, job.policy_id)
        if pol is None:
            raise BackupError("the policy no longer exists")
        scope = pol.scope or {}
        return (
            pol.encryption or {},
            list(scope.get("components") or platform.DEFAULT_COMPONENTS),
            scope.get("metrics", "none"),
            int(scope.get("metrics_days") or 0),
        )
    return (
        p.get("encryption") or {},
        list(p.get("components") or platform.DEFAULT_COMPONENTS),
        p.get("metrics", "none"),
        int(p.get("metrics_days") or 0),
    )


async def _run_database_backup(job: BackupJob) -> tuple[str, dict[str, Any]]:
    p = dict(job.params)
    enc_cfg = p.get("encryption") or {}
    scope = p.get("scope") or {}
    if job.policy_id:
        async with session_scope() as db:
            pol = await db.get(BackupPolicy, job.policy_id)
        if pol is None:
            raise BackupError("the policy no longer exists")
        enc_cfg, scope = pol.encryption or {}, pol.scope or {}
    async with session_scope() as db:
        servers = await scope_servers(db, scope)
        assets: list[dict[str, Any]] = []
        for s in servers:
            dbs = list(scope.get("databases") or []) or ([s.default_database] if s.default_database else [])
            if not dbs:
                assets.append(
                    {
                        "key": f"{s.id}",
                        "server_id": str(s.id),
                        "name": s.name,
                        "database": None,
                        "status": "FAILED",
                        "error": "no database selected and the server has no default database",
                    }
                )
            for d in dbs:
                assets.append(
                    {
                        "key": f"{s.id}:{d}",
                        "server_id": str(s.id),
                        "name": f"{s.name} / {d}",
                        "database": d,
                        "status": "QUEUED",
                    }
                )
    if not assets:
        raise BackupError("the scope matches no registered (non-simulated) server")
    progress = {"step": "running", "total": len(assets), "done": 0, "assets": assets}
    await _progress(job.id, progress)
    options = {k: scope[k] for k in ("server_directory", "directory", "no_owner") if k in scope}
    run_secrets = _run_secrets(job)
    ids = []
    for a in assets:
        if a["status"] == "FAILED":
            progress["done"] += 1
            continue
        if await _cancel_requested(job.id):
            for rest in assets:
                if rest["status"] == "QUEUED":
                    rest["status"] = "CANCELLED"
            await _progress(job.id, progress)
            raise JobCancelled("cancelled by user")
        a["status"] = "RUNNING"
        await _progress(job.id, progress)
        try:
            bid = await create_database_backup(
                server_id=uuid.UUID(a["server_id"]),
                database=a["database"],
                target_id=job.target_id,  # type: ignore[arg-type]
                enc_cfg=enc_cfg,
                run_secrets=run_secrets,
                created_by=job.requested_by,
                options=options,
                job_id=job.id,
                policy_id=job.policy_id,
            )
            async with session_scope() as db:
                b = await db.get(Backup, bid)
                a["status"], a["backup_id"], a["error"] = b.status, str(bid), b.error  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - per-asset result
            a["status"], a["error"] = "FAILED", _err(exc)
            if isinstance(exc, storage.StorageError):
                a["error_class"] = "storage"
        ids.append(a.get("backup_id"))
        progress["done"] += 1
        await _progress(job.id, progress)
        await audit.record(
            "backup.create",
            result="success" if a["status"] == "SUCCESS" else "failure",
            failure_reason=a.get("error"),
            target_type="backup",
            target_id=a.get("backup_id"),
            object_name=a["name"],
            details={
                "job_id": str(job.id),
                "type": "database",
                "server_id": a["server_id"],
                "database": a["database"],
                "status": a["status"],
            },
            **_audit_kw(job),
        )
    ok = sum(1 for a in assets if a["status"] == "SUCCESS")
    status = "SUCCESS" if ok == len(assets) else ("PARTIAL_SUCCESS" if ok else "FAILED")
    if job.policy_id and ok:
        await _apply_retention_safely(job.policy_id, job)
    return status, {
        "backup_ids": [i for i in ids if i],
        "succeeded": ok,
        "total": len(assets),
        "storage_errors": sum(1 for a in assets if a.get("error_class") == "storage"),
    }


async def _load_backup(backup_id: str | uuid.UUID) -> Backup:
    async with session_scope() as db:
        b = await db.get(Backup, uuid.UUID(str(backup_id)))
        if b is None or b.status == "DELETED":
            raise BackupError("the backup does not exist (or was deleted)")
        db.expunge(b)
        return b


async def verify_backup(backup_id: uuid.UUID, job: BackupJob | None = None) -> dict[str, Any]:
    b = await _load_backup(backup_id)
    keys = await _decrypt_material(job, b)
    workdir = _workdir()
    try:
        if b.kind == "platform":
            prov = storage.provider_for(await _target(b.target_id))
            result = await _verify_platform(prov, b, workdir, keys)
        else:
            prov = storage.provider_for(await _target(b.target_id)) if b.location_kind == "target" else None
            ctx = await _admin_context(b.server_id, b.database_name or "", workdir, {}, job.id if job else None)  # type: ignore[arg-type]
            result = await _verify_database(prov, b, ctx, keys)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    values: dict[str, Any] = {"verification": result, "verified_at": _now()}
    if result["ok"] and b.status in ("CORRUPTED", "VERIFYING"):
        values["status"] = "SUCCESS"
    if not result["ok"] and b.status == "SUCCESS":
        values["status"] = "CORRUPTED"
        BACKUP_FAILURES.labels(b.kind).inc()
    await _save_backup(b.id, **values)
    return result


async def _run_verify(job: BackupJob) -> tuple[str, dict[str, Any]]:
    bid = job.params["backup_id"]
    await _update_job(job.id, status="VERIFYING")
    result = await verify_backup(uuid.UUID(bid), job)
    await audit.record(
        "backup.verify",
        result="success" if result["ok"] else "failure",
        failure_reason=None if result["ok"] else "; ".join(result.get("problems") or [])[:500],
        target_type="backup",
        target_id=bid,
        details={"job_id": str(job.id), "level": result.get("level")},
        **_audit_kw(job),
    )
    return ("SUCCESS" if result["ok"] else "FAILED"), result


# ---- restore -------------------------------------------------------------------------------------
async def open_platform_backup(b: Backup, workdir: Path, keys: dict[str, str | None]) -> tuple[Path, dict[str, Any]]:
    """Download, check, decrypt and extract a platform backup. Returns (extract dir, inner manifest)."""
    prov = storage.provider_for(await _target(b.target_id))
    _, actual, plain = await _fetch_plain(prov, b, workdir, keys, need_plain=True)
    if actual != b.sha256:
        await _save_backup(b.id, status="CORRUPTED")
        raise _Corrupted("integrity check failed: the stored archive's SHA-256 does not match the catalog")
    dest = workdir / "x"
    dest.mkdir(exist_ok=True)
    assert plain is not None
    inner = await asyncio.to_thread(platform.extract, plain, dest)
    problems = await asyncio.to_thread(platform.verify_contents, dest, inner)
    if problems:
        await _save_backup(b.id, status="CORRUPTED")
        raise _Corrupted("integrity check failed: " + "; ".join(problems))
    return dest, inner


def key_availability(inner: dict[str, Any]) -> dict[str, Any]:
    ring = get_keyring()
    need = list(inner.get("key_ids") or [])
    missing = []
    for k in need:
        try:
            ring.key(k)
        except CryptoError:
            missing.append(k)
    return {"required": need, "missing": missing}


async def preview_platform_restore(
    backup_id: uuid.UUID, components: list[str] | None, keys: dict[str, str | None]
) -> dict[str, Any]:
    """Restore wizard steps 2–6: manifest, integrity, compatibility, components, impact."""
    b = await _load_backup(backup_id)
    if b.kind != "platform":
        raise BackupError("not a platform backup")
    if b.status not in ("SUCCESS", "VERIFYING"):
        raise BackupError(f"backup status is {b.status}: only verified backups can be restored")
    workdir = _workdir()
    try:
        dest, inner = await open_platform_backup(b, workdir, keys)
        compat = platform.compatibility(inner)
        available = list(inner.get("components") or [])
        chosen = platform.check_components(components or available)
        not_in = sorted(set(chosen) - set(available))
        if not_in:
            raise BackupError(f"components not in this backup: {', '.join(not_in)}")
        result: dict[str, Any] = {
            "backup_id": str(b.id),
            "manifest": {
                k: inner.get(k)
                for k in (
                    "format",
                    "created_at",
                    "created_by",
                    "dbm_version",
                    "schema_version",
                    "components",
                    "metrics",
                )
            },
            "integrity": {"ok": True, "sha256": b.sha256, "tables": len(inner.get("tables") or {})},
            "compatibility": compat,
            "keys": key_availability(inner),
            "components": [
                {
                    "key": c,
                    "label": platform.COMPONENT_LABELS[c],
                    "merge_only": c in platform.MERGE_ONLY,
                    "selected": c in chosen,
                }
                for c in available
            ],
            "impact": None,
        }
        if compat["status"] != "unsupported":
            async with session_scope() as db:
                result["impact"] = await platform.impact(db, dest, chosen)
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _run_platform_restore(job: BackupJob) -> tuple[str, dict[str, Any]]:
    p = job.params
    b = await _load_backup(p["backup_id"])
    keys = await _decrypt_material(job, b)
    steps: list[dict[str, Any]] = []

    async def step(name: str, state: str, detail: str | None = None) -> None:
        steps.append({"step": name, "status": state, "detail": detail})
        await _progress(job.id, {"step": name, "steps": steps})

    await audit.record(
        "restore.start",
        target_type="backup",
        target_id=str(b.id),
        details={"job_id": str(job.id), "type": "platform", "components": p.get("components")},
        **_audit_kw(job),
    )
    workdir = _workdir()
    pre_id: uuid.UUID | None = None
    try:
        await step("validate_manifest", "running")
        dest, inner = await open_platform_backup(b, workdir, keys)
        await step("verify_integrity", "ok", f"{len(inner.get('tables') or {})} tables match their checksums")
        compat = platform.compatibility(inner)
        if compat["status"] == "unsupported":
            raise BackupError("incompatible backup: " + "; ".join(compat["notes"]))
        if compat["status"] == "migration_required" and not p.get("accept_migration"):
            raise BackupError("the backup has an older schema: confirm the column mapping to continue")
        await step("check_compatibility", "ok", compat["status"])
        keyinfo = key_availability(inner)
        if keyinfo["missing"] and not p.get("accept_missing_keys"):
            raise BackupError(
                "master key(s) " + ", ".join(keyinfo["missing"]) + " are not configured (DBM_PREVIOUS_MASTER_KEYS): "
                "encrypted credentials in the backup could not be decrypted"
            )
        components = platform.check_components(p.get("components") or inner.get("components") or [])
        if p.get("pre_restore_backup", True):
            await step("pre_restore_backup", "running")
            enc = b.encryption or {}
            pre_enc = {"mode": enc.get("mode", "none"), "recipient": enc.get("recipient")}
            pre_id = await create_platform_backup(
                target_id=uuid.UUID(str(p.get("pre_restore_target_id") or b.target_id)),
                components=list(platform.COMPONENTS),
                enc_cfg=pre_enc,
                run_secrets={"passphrase": keys.get("passphrase") or "", "identity": keys.get("identity") or ""},
                created_by=job.requested_by,
                job_id=job.id,
                purpose="pre_restore",
            )
            pre = await _load_backup(pre_id)
            if pre.status != "SUCCESS":
                raise BackupError(f"the pre-restore safety backup ended {pre.status}: restore not started")
            await step("pre_restore_backup", "ok", str(pre_id))
        await step("restore", "running")
        async with session_scope() as db:
            report = await platform.apply(db, dest, components)
        await step("restore", "ok")
        # validation: counts now equal the backup for replaced tables
        async with session_scope() as db:
            mismatches = []
            for name, r in report.items():
                if platform.TABLE_COMPONENT[name] in platform.MERGE_ONLY:
                    continue
                n = (await db.execute(text(f"SELECT count(*) FROM {name}"))).scalar_one()  # noqa: S608 - table names from metadata
                if n != r["in_backup"]:
                    mismatches.append(f"{name}: {n} rows, backup had {r['in_backup']}")
        if mismatches:
            raise BackupError("post-restore validation failed: " + "; ".join(mismatches))
        await step("validation", "ok")
        result = {
            "tables": report,
            "steps": steps,
            "components": components,
            "pre_restore_backup": str(pre_id) if pre_id else None,
        }
        await audit.record(
            "restore.complete",
            target_type="backup",
            target_id=str(b.id),
            details={"job_id": str(job.id), "components": components},
            **_audit_kw(job),
        )
        return "SUCCESS", result
    except Exception as exc:
        await audit.record(
            "restore.complete",
            result="failure",
            failure_reason=_err(exc),
            target_type="backup",
            target_id=str(b.id),
            details={"job_id": str(job.id)},
            **_audit_kw(job),
        )
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _run_database_restore(job: BackupJob) -> tuple[str, dict[str, Any]]:
    from app.services import connections

    p = job.params
    b = await _load_backup(p["backup_id"])
    if b.kind != "database" or b.status != "SUCCESS":
        raise BackupError("only successful database backups can be restored")
    target_server = uuid.UUID(str(p.get("server_id") or b.server_id))
    target_db = str(p["target_database"])
    engines.check_identifier(target_db)
    async with session_scope() as db:
        server = await db.get(Server, target_server)
        if server is None:
            raise BackupError("the target server no longer exists")
        connections.require_administration(server)
        if server.engine != b.engine:
            raise BackupError(f"a {b.engine} backup cannot be restored to a {server.engine} server")
    prov_ = engines.provider(b.engine or "")
    if not prov_.supports_restore:
        raise BackupError(
            "EXTERNAL_TOOL_REQUIRED: " + (prov_.restore_command(b, target_db) or "restore with the engine's tools")
        )
    await audit.record(
        "restore.start",
        target_type="backup",
        target_id=str(b.id),
        object_name=target_db,
        details={"job_id": str(job.id), "type": "database", "server_id": str(target_server)},
        **_audit_kw(job),
    )
    workdir = _workdir()
    try:
        ctx = await _admin_context(
            target_server, target_db, workdir, {"tag": job.id.hex[:12], **(p.get("options") or {})}, job.id
        )
        local = None
        await _progress(job.id, {"step": "verify_integrity"})
        if b.location_kind == "target":
            prov = storage.provider_for(await _target(b.target_id))
            _, actual, local = await _fetch_plain(prov, b, workdir, await _decrypt_material(job, b), need_plain=True)
            if actual != b.sha256:
                await _save_backup(b.id, status="CORRUPTED")
                raise _Corrupted("integrity check failed: the stored dump's SHA-256 does not match")
            await prov_.verify(ctx, b, local)
        await _progress(job.id, {"step": "restore"})
        detail = await prov_.restore(ctx, b, local, target_db)
        await audit.record(
            "restore.complete",
            target_type="backup",
            target_id=str(b.id),
            object_name=target_db,
            details={"job_id": str(job.id), **detail},
            **_audit_kw(job),
        )
        return "SUCCESS", detail
    except Exception as exc:
        await audit.record(
            "restore.complete",
            result="failure",
            failure_reason=_err(exc),
            target_type="backup",
            target_id=str(b.id),
            object_name=target_db,
            details={"job_id": str(job.id)},
            **_audit_kw(job),
        )
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---- retention -----------------------------------------------------------------------------------
def retention_keep(backups: list[Backup], rules: dict[str, Any], now: datetime, tz: ZoneInfo) -> set[uuid.UUID]:
    """Ids kept by the rules (newest first input not required). Union of every rule; the newest
    successful backup is always kept. Pinned backups are always kept."""
    ok = sorted((b for b in backups if b.status == "SUCCESS"), key=lambda b: b.created_at, reverse=True)
    keep: set[uuid.UUID] = {b.id for b in backups if b.pinned}
    if ok:
        keep.add(ok[0].id)
    if n := int(rules.get("keep_last") or 0):
        keep |= {b.id for b in ok[:n]}
    if d := int(rules.get("keep_days") or 0):
        keep |= {b.id for b in ok if b.created_at >= now - timedelta(days=d)}

    def generations(n: int, key: Any) -> set[uuid.UUID]:
        seen: dict[Any, uuid.UUID] = {}
        for b in ok:  # newest first: the newest backup of each period is its generation
            k = key(b.created_at.astimezone(tz).date())
            if k not in seen:
                seen[k] = b.id
            if len(seen) >= n:
                break
        return set(seen.values())

    if n := int(rules.get("keep_daily") or 0):
        keep |= generations(n, lambda d: d)
    if n := int(rules.get("keep_weekly") or 0):
        keep |= generations(n, lambda d: d.isocalendar()[:2])
    if n := int(rules.get("keep_monthly") or 0):
        keep |= generations(n, lambda d: (d.year, d.month))
    return keep


def _has_rules(rules: dict[str, Any]) -> bool:
    return any(int(rules.get(k) or 0) for k in ("keep_last", "keep_days", "keep_daily", "keep_weekly", "keep_monthly"))


async def delete_backup(backup_id: uuid.UUID, actor: str | None) -> None:
    """Remove the stored objects of a backup and mark it DELETED (the record stays for the audit
    trail). Server-side files (SQL Server .bak, Data Pump dumps, Db2 images) are not touched."""
    b = await _load_backup(backup_id)
    if b.location_kind == "target" and b.location:
        prov = storage.provider_for(await _target(b.target_id))
        prefix = str(Path(b.location).parent)
        for key in await prov.list(prefix + "/"):
            # only DBManager-managed objects of this backup (its own folder)
            if key.startswith(prefix + "/"):
                await prov.delete(key)
    await _save_backup(b.id, status="DELETED", deleted_at=_now(), deleted_by=actor)


async def apply_retention(
    db: AsyncSession, policy: BackupPolicy, now: datetime | None = None, actor: str = "retention"
) -> dict[str, Any]:
    """Delete backups of this policy that its rules no longer keep. Only backups the policy
    created (``policy_id``) are ever considered."""
    now = now or _now()
    rules = policy.retention or {}
    if not _has_rules(rules):
        return {"deleted": 0, "kept": None}
    tz = _zone((policy.schedule or {}).get("time_zone"))
    rows = list(
        (
            await db.execute(
                select(Backup).where(
                    Backup.policy_id == policy.id, Backup.status.in_(("SUCCESS", "FAILED", "CORRUPTED"))
                )
            )
        ).scalars()
    )
    keep = retention_keep(rows, rules, now, tz)
    grace = now - timedelta(days=max(int(rules.get("keep_days") or 0), 7))
    doomed = [b for b in rows if b.id not in keep and (b.status == "SUCCESS" or b.created_at < grace)]
    deleted, errors = 0, []
    for b in doomed:
        try:
            await delete_backup(b.id, actor)
            deleted += 1
        except Exception as exc:  # noqa: BLE001 - reported, retried on the next run
            errors.append(f"{b.id}: {_err(exc)}")
    if deleted:
        await audit.record(
            "backup.retention",
            username=actor,
            auth_source="system",
            category="backup",
            result="success" if not errors else "failure",
            object_name=policy.name,
            failure_reason="; ".join(errors)[:1000] if errors else None,
            details={"policy_id": str(policy.id), "deleted": deleted, "kept": len(keep)},
        )
    return {"deleted": deleted, "kept": len(keep), "errors": errors}


async def _apply_retention_safely(policy_id: uuid.UUID, job: BackupJob) -> None:
    async with session_scope() as db:
        pol = await db.get(BackupPolicy, policy_id)
        if pol is None:
            return
        res = await apply_retention(db, pol)
        if res.get("errors"):
            await notify_policy(db, pol, "Retention cleanup failed", "; ".join(res["errors"])[:1000], "medium")


async def _run_retention(job: BackupJob) -> tuple[str, dict[str, Any]]:
    async with session_scope() as db:
        pol = await db.get(BackupPolicy, job.policy_id) if job.policy_id else None
        if pol is None:
            raise BackupError("the policy no longer exists")
        res = await apply_retention(db, pol, actor=job.requested_by or "retention")
    return ("FAILED" if res.get("errors") else "SUCCESS"), res


# ---- schedules -----------------------------------------------------------------------------------
def _zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def next_run(schedule: dict[str, Any], after: datetime) -> datetime | None:
    """Next occurrence strictly after ``after`` (wall-clock time in the schedule's time zone,
    so daily runs follow daylight-saving changes)."""
    freq = schedule.get("frequency") or "manual"
    if freq == "manual":
        return None
    tz = _zone(schedule.get("time_zone"))
    hh, _, mm = str(schedule.get("time") or "02:00").partition(":")
    hour, minute = int(hh), int(mm or 0)
    local = after.astimezone(tz)
    day = local.date()
    for _ in range(0, 400):
        ok = True
        if freq == "weekly":
            ok = day.isoweekday() == int(schedule.get("weekday") or 7)
        elif freq == "monthly":
            ok = day.day == min(int(schedule.get("day_of_month") or 1), 28)
        if ok:
            cand = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
            if cand > local:
                return cand.astimezone(UTC)
        day = day + timedelta(days=1)
    return None


def max_age(policy: BackupPolicy) -> timedelta | None:
    if policy.max_age_hours:
        return timedelta(hours=policy.max_age_hours)
    freq = (policy.schedule or {}).get("frequency")
    return {"daily": timedelta(hours=26), "weekly": timedelta(days=8), "monthly": timedelta(days=32)}.get(str(freq))


#: how recent a backup must be for a manual policy without ``max_age_hours`` to count as protecting
#: its servers (such a policy raises no overdue notification; it only stops counting as protection)
UNSCHEDULED_FRESHNESS = timedelta(days=8)


def freshness_limit(policy: BackupPolicy) -> timedelta:
    return max_age(policy) or UNSCHEDULED_FRESHNESS


async def schedule(db: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """Global job ``backup_scheduler``."""
    now = now or _now()
    out = {"queued": 0, "stale": 0, "overdue": 0}
    stale = now - timedelta(minutes=get_settings().backup_stale_minutes)
    res = await db.execute(
        update(BackupJob)
        .where(BackupJob.status.in_(("RUNNING", "VERIFYING")), BackupJob.heartbeat_at < stale)
        .values(status="FAILED", error="worker lost (no heartbeat)", finished_at=now)
        .returning(BackupJob.id)
    )
    out["stale"] = len(res.all())
    policies = list((await db.execute(select(BackupPolicy).where(BackupPolicy.enabled.is_(True)))).scalars())
    for pol in policies:
        sched = pol.schedule or {}
        if pol.next_run_at is None:
            pol.next_run_at = next_run(sched, now)
        if pol.next_run_at and pol.next_run_at <= now:
            active = (
                await db.execute(
                    select(BackupJob.id).where(BackupJob.policy_id == pol.id, BackupJob.status.in_(JOB_ACTIVE)).limit(1)
                )
            ).first()
            if not active:
                await create_job(
                    db,
                    "platform_backup" if pol.kind == "platform" else "database_backup",
                    params={},
                    requested_by="scheduler",
                    trigger="schedule",
                    policy_id=pol.id,
                    target_id=pol.target_id,
                )
                out["queued"] += 1
            pol.last_run_at = now
            pol.next_run_at = next_run(sched, now)
        limit = max_age(pol)
        if limit:
            last_ok = (
                await db.execute(
                    select(func.max(Backup.created_at)).where(Backup.policy_id == pol.id, Backup.status == "SUCCESS")
                )
            ).scalar_one_or_none()
            BACKUP_LAST_SUCCESS.labels(str(pol.id)).set(last_ok.timestamp() if last_ok else 0)
            reference = last_ok or pol.created_at
            overdue = reference < now - limit
            if overdue and (pol.overdue_notified_at is None or pol.overdue_notified_at < now - limit):
                when = last_ok.isoformat() if last_ok else "never"
                await notify_policy(
                    db,
                    pol,
                    "No successful backup within policy",
                    f"Policy {pol.name}: last successful backup {when}; expected at least every {limit}.",
                    "high",
                )
                pol.overdue_notified_at = now
                out["overdue"] += 1
            elif not overdue:
                pol.overdue_notified_at = None
    await db.flush()
    return out


# ---- notifications -------------------------------------------------------------------------------
def _event(policy: BackupPolicy | None, title: str, message: str, severity: str) -> dict[str, Any]:
    base = get_settings().public_base_url.rstrip("/")
    return {
        "id": str(policy.id) if policy else "backup",
        "rule": f"backup policy {policy.name}" if policy else "backup (no policy)",
        "status": "firing",
        "severity": severity,
        "title": title,
        "message": message,
        "server_name": "DBManager backup",
        "engine": policy.kind if policy else "backup",
        "environment": None,
        "url": f"{base}/platform/backups",
        "renotify": False,
    }


async def notify_policy(db: AsyncSession, policy: BackupPolicy | None, title: str, message: str, severity: str) -> int:
    """Send a backup event to the policy's notification channels; events of backups that no
    policy owns go to the channels named in the platform setting ``backup.notification_channels``
    (no channels: nothing sent)."""
    from app.services import alerting, app_settings

    if policy is None:
        names = [n.strip() for n in str(await app_settings.get(db, "backup.notification_channels")).split(",")]
        names = [n for n in names if n]
        if not names:
            return 0
        channels = list(
            (
                await db.execute(
                    select(NotificationChannel).where(
                        NotificationChannel.name.in_(names), NotificationChannel.enabled.is_(True)
                    )
                )
            ).scalars()
        )
        data = _event(None, title, message, severity)
        for ch in channels:
            await alerting.deliver(db, ch, "firing", None, data)
        return len(channels)
    if not policy.channel_ids:
        return 0
    data = _event(policy, title, message, severity)
    ids = [uuid.UUID(str(c)) for c in policy.channel_ids]
    channels = list(
        (
            await db.execute(
                select(NotificationChannel).where(
                    NotificationChannel.id.in_(ids), NotificationChannel.enabled.is_(True)
                )
            )
        ).scalars()
    )
    for ch in channels:
        await alerting.deliver(db, ch, "firing", None, data)
    return len(channels)


async def _notify_job(job: BackupJob, status: str, error: str | None, result: dict[str, Any] | None) -> None:
    titles = {
        "platform_backup": "Backup failed",
        "database_backup": "Backup failed" if status == "FAILED" else "Backup partially failed",
        "verify": "Backup verification failed",
        "platform_restore": "Restore failed",
        "database_restore": "Restore failed",
        "retention": "Retention cleanup failed",
    }
    msg = error or ""
    if result and result.get("total"):
        msg = f"{result.get('succeeded', 0)} of {result['total']} assets succeeded. {msg}"
    try:
        async with session_scope() as db:
            pol = await db.get(BackupPolicy, job.policy_id) if job.policy_id else None
            if pol is None and job.params.get("backup_id"):
                b = await db.get(Backup, uuid.UUID(str(job.params["backup_id"])))
                pol = await db.get(BackupPolicy, b.policy_id) if b and b.policy_id else None
            title = titles.get(job.kind, "Backup job failed")
            if status == "CANCELLED":
                title = "Backup job cancelled"
            elif (result or {}).get("storage_errors"):
                # classified from the exception type, not from the message text
                title = "Backup storage unavailable"
            await notify_policy(db, pol, title, msg[:1500] or status, "medium" if status == "CANCELLED" else "high")
    except Exception:  # noqa: BLE001 - notification problems never fail the job record
        log.exception("backup notification failed")


# ---- asset protection status -------------------------------------------------------------------
async def protection(db: AsyncSession, servers: list[Server]) -> dict[str, dict[str, Any]]:
    """Per server: covering database policies, last successful backup and whether it is
    *Protected* (an enabled policy covers it and its newest backup is within the policy's age)."""
    now = _now()
    policies = list(
        (
            await db.execute(
                select(BackupPolicy).where(BackupPolicy.kind == "database", BackupPolicy.enabled.is_(True))
            )
        ).scalars()
    )
    covering: dict[uuid.UUID, list[BackupPolicy]] = {s.id: [] for s in servers}
    for pol in policies:
        for s in await scope_servers(db, pol.scope or {}):
            if s.id in covering:
                covering[s.id].append(pol)
    last = {
        sid: (ts, size)
        for sid, ts, size in (
            await db.execute(
                select(Backup.server_id, func.max(Backup.created_at), func.max(Backup.size_bytes))
                .where(Backup.server_id.in_([s.id for s in servers]), Backup.status == "SUCCESS")
                .group_by(Backup.server_id)
            )
        ).all()
    }
    out = {}
    for s in servers:
        pols = covering.get(s.id, [])
        ts = last.get(s.id, (None, None))[0]
        fresh = bool(ts) and any(ts >= now - freshness_limit(p) for p in pols)  # type: ignore[operator]
        out[str(s.id)] = {
            "status": "protected" if pols and fresh else ("at_risk" if pols else "not_protected"),
            "last_backup_at": ts,
            "freshness_limit_hours": max((freshness_limit(p).total_seconds() / 3600 for p in pols), default=None),
            "policies": [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "retention": p.retention,
                    "frequency": (p.schedule or {}).get("frequency"),
                }
                for p in pols
            ],
        }
    return out


async def platform_protection(db: AsyncSession) -> dict[str, Any]:
    """Registration metadata ("server configuration protection") is covered by platform backups
    including the inventory component."""
    row = (
        await db.execute(
            select(Backup.created_at, Backup.manifest)
            .where(Backup.kind == "platform", Backup.status == "SUCCESS")
            .order_by(Backup.created_at.desc())
            .limit(20)
        )
    ).all()
    for ts, manifest in row:
        if "inventory" in ((manifest or {}).get("components") or []):
            return {"protected": True, "last_backup_at": ts}
    return {"protected": False, "last_backup_at": None}


# ---- disaster recovery: register backups found in a storage target -----------------------------
async def scan_target(db: AsyncSession, target: BackupTarget, actor: str | None) -> dict[str, int]:
    """Register backups whose catalog entry (``manifest.json``) is in the target but not in this
    installation's catalog — e.g. after reinstalling DBManager from scratch."""
    prov = storage.provider_for(target)
    keys = [k for k in await prov.list("") if k.endswith("/manifest.json")]
    known = {str(x) for x in (await db.execute(select(Backup.id))).scalars()}
    added, skipped = 0, 0
    workdir = _workdir()
    try:
        for key in keys:
            local = workdir / "m.json"
            await prov.get(key, local)
            try:
                m = json.loads(local.read_text())
                bid = uuid.UUID(str(m["backup_id"]))
                archive = m["archive"]
            except (ValueError, KeyError, TypeError):
                skipped += 1
                continue
            if str(bid) in known or await prov.size(archive["object"]) is None:
                skipped += 1
                continue
            enc = m.get("encryption") or {"mode": "none"}
            server = m.get("server") or {}
            db.add(
                Backup(
                    id=bid,
                    kind=m.get("type", "platform"),
                    status="VERIFYING",
                    target_id=target.id,
                    server_id=None,
                    server_name=server.get("name"),
                    engine=server.get("engine"),
                    database_name=m.get("database"),
                    method=m.get("method") or "platform",
                    tool=m.get("tool"),
                    location=archive["object"],
                    location_kind="target",
                    size_bytes=archive.get("size"),
                    sha256=archive.get("sha256"),
                    encrypted=enc.get("mode", "none") != "none",
                    encryption=enc,
                    app_version=str(m.get("dbm_version") or "?"),
                    schema_version=m.get("schema_version"),
                    format_version=str(m.get("format")),
                    manifest={k: v for k, v in m.items() if k not in ("server",)},
                    created_by=m.get("created_by"),
                    created_at=datetime.fromisoformat(m["created_at"]) if m.get("created_at") else _now(),
                    verification={
                        "level": "imported",
                        "ok": None,
                        "note": "registered from the storage target; verify before restoring",
                    },
                )
            )
            added += 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    await db.flush()
    if added:
        await audit.record(
            "backup.import", username=actor, category="backup", object_name=target.name, details={"registered": added}
        )
    return {"registered": added, "skipped": skipped}
