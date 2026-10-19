"""Backup & restore API (milestone 0.12): storage targets, policies, backups, jobs, restore."""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select

from app.api.deps import DB, CurrentPrincipal, load_server, rate_limit
from app.backup import encryption, engines, platform, service, storage
from app.core.errors import ConfirmationRequired, Conflict, NotFound, PermissionDenied, ValidationFailed
from app.models import Backup, BackupJob, BackupPolicy, BackupTarget, Server
from app.models.backup import JOB_ACTIVE
from app.schemas.common import Message
from app.services import audit
from app.services.authz import server_ref

router = APIRouter(tags=["backup"])
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
PLATFORM_CONFIRM = "RESTORE PLATFORM"


# ---- schemas ----------------------------------------------------------------------------------------
class EncryptionIn(BaseModel):
    mode: Literal["none", "recipient", "passphrase"] = "recipient"
    #: recipient mode: an age X25519 public key (age1…)
    recipient: str | None = Field(None, max_length=200)
    #: passphrase mode in policies: where the passphrase comes from (env:NAME or file:/path)
    passphrase_ref: str | None = Field(None, max_length=512)

    @model_validator(mode="after")
    def _check(self) -> EncryptionIn:
        if self.mode == "recipient":
            if not self.recipient:
                raise ValueError("recipient mode needs an age recipient (age1…)")
            try:
                encryption.parse_recipient(self.recipient)
            except encryption.EncryptionError as exc:
                raise ValueError(str(exc)) from exc
        if self.passphrase_ref and not self.passphrase_ref.startswith(("env:", "file:")):
            raise ValueError("passphrase_ref is env:NAME or file:/path")
        return self


class TargetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    kind: Literal["local", "mounted", "nfs", "s3"]
    enabled: bool = True
    #: filesystem: path; s3: endpoint_url, bucket, prefix, region, access_key_id, addressing_style,
    #: verify_tls, ca_pem
    config: dict[str, Any] = Field(default_factory=dict)
    #: S3 secret access key (write-only; omit to keep)
    secret_access_key: str | None = Field(None, max_length=512)

    @model_validator(mode="after")
    def _check(self) -> TargetIn:
        bad = [k for k in self.config if any(s in k.lower() for s in ("password", "secret", "token"))]
        if bad:
            raise ValueError(f"put secrets in secret_access_key, not in config ({', '.join(bad)})")
        if self.kind in storage.FILESYSTEM_KINDS:
            try:
                storage.validate_directory(str(self.config.get("path") or ""))
            except storage.StorageError as exc:
                raise ValueError(str(exc)) from exc
        else:
            if not self.config.get("bucket"):
                raise ValueError("an S3 target needs a bucket")
            url = str(self.config.get("endpoint_url") or "")
            if url and not url.startswith(("https://", "http://")):
                raise ValueError("endpoint_url must be an http(s) URL")
            if self.config.get("addressing_style", "auto") not in ("auto", "path", "virtual"):
                raise ValueError("addressing_style is auto, path or virtual")
        return self


class ScheduleIn(BaseModel):
    frequency: Literal["manual", "daily", "weekly", "monthly"] = "daily"
    time: str = "02:00"
    time_zone: str = "UTC"
    weekday: int = Field(7, ge=1, le=7)
    day_of_month: int = Field(1, ge=1, le=28)

    @field_validator("time")
    @classmethod
    def _time(cls, v: str) -> str:
        if not _TIME.match(v):
            raise ValueError("time is HH:MM (24 h)")
        return v

    @field_validator("time_zone")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {v!r}") from exc
        return v


class RetentionIn(BaseModel):
    keep_last: int = Field(7, ge=0, le=1000)
    keep_days: int = Field(0, ge=0, le=3650)
    keep_daily: int = Field(0, ge=0, le=366)
    keep_weekly: int = Field(0, ge=0, le=520)
    keep_monthly: int = Field(0, ge=0, le=240)


class DatabaseScopeIn(BaseModel):
    server_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    environment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    site_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=50)
    #: empty = each server's default database
    databases: list[str] = Field(default_factory=list, max_length=200)
    #: SQL Server / Db2: directory on the database server (default: the instance default)
    server_directory: str | None = Field(None, max_length=400)
    #: Oracle: Data Pump directory object (default DATA_PUMP_DIR)
    directory: str | None = Field(None, max_length=128)

    @model_validator(mode="after")
    def _check(self) -> DatabaseScopeIn:
        if not (self.server_ids or self.environment_ids or self.site_ids or self.tags):
            raise ValueError("select servers, environments, sites or tags")
        for d in self.databases:
            try:
                engines.check_identifier(d)
            except engines.BackupProviderError as exc:
                raise ValueError(str(exc)) from exc
        return self

    def stored(self) -> dict[str, Any]:
        out = self.model_dump(mode="json", exclude_none=True)
        return out


class PlatformScopeIn(BaseModel):
    components: list[str] = Field(default_factory=lambda: list(platform.DEFAULT_COMPONENTS))
    #: none | export (VictoriaMetrics native export into the archive)
    metrics: Literal["none", "export"] = "none"
    #: export only the last N days (0 = all retained telemetry)
    metrics_days: int = Field(0, ge=0, le=3650)

    @field_validator("components")
    @classmethod
    def _components(cls, v: list[str]) -> list[str]:
        try:
            return platform.check_components(v)
        except platform.PlatformBackupError as exc:
            raise ValueError(str(exc)) from exc


class PolicyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=2000)
    kind: Literal["platform", "database"]
    enabled: bool = True
    target_id: uuid.UUID
    platform_scope: PlatformScopeIn | None = None
    database_scope: DatabaseScopeIn | None = None
    schedule: ScheduleIn = Field(default_factory=ScheduleIn)
    encryption: EncryptionIn
    retention: RetentionIn = Field(default_factory=RetentionIn)
    channel_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    max_age_hours: int = Field(0, ge=0, le=24 * 400)

    @model_validator(mode="after")
    def _check(self) -> PolicyIn:
        if self.kind == "database" and self.database_scope is None:
            raise ValueError("database policies need a database_scope")
        if self.kind == "platform" and self.platform_scope is None:
            self.platform_scope = PlatformScopeIn()
        if (
            self.encryption.mode == "passphrase"
            and not self.encryption.passphrase_ref
            and self.schedule.frequency != "manual"
        ):
            raise ValueError("scheduled passphrase backups need a passphrase_ref (env:NAME or file:/path)")
        return self

    def scope(self) -> dict[str, Any]:
        if self.kind == "platform":
            return (self.platform_scope or PlatformScopeIn()).model_dump(mode="json")
        return self.database_scope.stored()  # type: ignore[union-attr]


class RunSecrets(BaseModel):
    #: passphrase for this run (passphrase mode); never stored beyond the job
    passphrase: str | None = Field(None, max_length=1024)
    #: age identity (AGE-SECRET-KEY-1…) to read a recipient-encrypted backup; never stored beyond the job
    identity: str | None = Field(None, max_length=4096)


class PlatformBackupIn(RunSecrets):
    target_id: uuid.UUID
    scope: PlatformScopeIn = Field(default_factory=PlatformScopeIn)
    encryption: EncryptionIn


class DatabaseBackupIn(RunSecrets):
    target_id: uuid.UUID
    scope: DatabaseScopeIn
    encryption: EncryptionIn


class RestoreIn(RunSecrets):
    #: platform: the components to replace (default: all in the backup)
    components: list[str] | None = None
    #: database: target server (default: the backup's server) and new database / schema name
    server_id: uuid.UUID | None = None
    target_database: str | None = Field(None, max_length=128)
    #: typed confirmation: "RESTORE PLATFORM" or the target database name
    confirm: str | None = None
    pre_restore_backup: bool = True
    accept_migration: bool = False
    accept_missing_keys: bool = False


# ---- presentation -----------------------------------------------------------------------------------
def _target_out(t: BackupTarget, used: dict[uuid.UUID, tuple[int, int]] | None = None) -> dict[str, Any]:
    n, size = (used or {}).get(t.id, (0, 0))
    return {
        "id": str(t.id),
        "name": t.name,
        "kind": t.kind,
        "enabled": t.enabled,
        "config": {k: v for k, v in t.config.items() if k != "ca_pem"}
        | ({"ca_pem": "(set)"} if t.config.get("ca_pem") else {}),
        "has_secret": bool(t.secret),
        "last_check_at": t.last_check_at,
        "last_check_ok": t.last_check_ok,
        "last_check_error": t.last_check_error,
        "backups": n,
        "stored_bytes": size,
        "created_by": t.created_by,
        "created_at": t.created_at,
    }


def _policy_out(pol: BackupPolicy, last_ok: datetime | None = None) -> dict[str, Any]:
    return {
        "id": str(pol.id),
        "name": pol.name,
        "description": pol.description,
        "kind": pol.kind,
        "enabled": pol.enabled,
        "target_id": str(pol.target_id),
        "scope": pol.scope,
        "schedule": pol.schedule,
        "encryption": pol.encryption,
        "retention": pol.retention,
        "channel_ids": pol.channel_ids,
        "max_age_hours": pol.max_age_hours,
        "effective_max_age_hours": (service.max_age(pol).total_seconds() / 3600) if service.max_age(pol) else None,
        "last_run_at": pol.last_run_at,
        "next_run_at": pol.next_run_at,
        "last_status": pol.last_status,
        "last_success_at": last_ok,
        "created_by": pol.created_by,
        "created_at": pol.created_at,
    }


def _backup_out(b: Backup, names: dict[uuid.UUID, str] | None = None) -> dict[str, Any]:
    return {
        "id": str(b.id),
        "kind": b.kind,
        "status": b.status,
        "method": b.method,
        "tool": b.tool,
        "server_id": str(b.server_id) if b.server_id else None,
        "server_name": b.server_name,
        "engine": b.engine,
        "database": b.database_name,
        "policy_id": str(b.policy_id) if b.policy_id else None,
        "policy_name": (names or {}).get(b.policy_id) if b.policy_id else None,  # type: ignore[arg-type]
        "target_id": str(b.target_id) if b.target_id else None,
        "target_name": (names or {}).get(b.target_id) if b.target_id else None,  # type: ignore[arg-type]
        "job_id": str(b.job_id) if b.job_id else None,
        "location": b.location,
        "location_kind": b.location_kind,
        "size_bytes": b.size_bytes,
        "sha256": b.sha256,
        "encrypted": b.encrypted,
        "encryption": {
            **(b.encryption or {}),
            "recipient": encryption.recipient_fingerprint((b.encryption or {}).get("recipient")),
        },
        "app_version": b.app_version,
        "schema_version": b.schema_version,
        "format_version": b.format_version,
        "verification": b.verification,
        "verified_at": b.verified_at,
        "error": b.error,
        "pinned": b.pinned,
        "created_by": b.created_by,
        "created_at": b.created_at,
        "finished_at": b.finished_at,
        "deleted_at": b.deleted_at,
    }


def _job_out(j: BackupJob) -> dict[str, Any]:
    return {
        "id": str(j.id),
        "kind": j.kind,
        "status": j.status,
        "trigger": j.trigger,
        "policy_id": str(j.policy_id) if j.policy_id else None,
        "target_id": str(j.target_id) if j.target_id else None,
        "params": {k: v for k, v in (j.params or {}).items() if k != "sealed"},
        "progress": j.progress,
        "result": j.result,
        "error": j.error,
        "cancel_requested": j.cancel_requested,
        "requested_by": j.requested_by,
        "correlation_id": j.correlation_id,
        "created_at": j.created_at,
        "started_at": j.started_at,
        "finished_at": j.finished_at,
    }


async def _names(db: DB) -> dict[uuid.UUID, str]:
    out = dict((await db.execute(select(BackupTarget.id, BackupTarget.name))).all())
    out.update(dict((await db.execute(select(BackupPolicy.id, BackupPolicy.name))).all()))
    return out  # type: ignore[return-value]


def _run_secrets(body: RunSecrets) -> dict[str, str | None]:
    return {"passphrase": body.passphrase, "identity": body.identity}


async def _get_target(db: DB, target_id: uuid.UUID) -> BackupTarget:
    t = await db.get(BackupTarget, target_id)
    if t is None:
        raise NotFound("Storage target not found")
    return t


async def _get_backup(db: DB, p: CurrentPrincipal, backup_id: uuid.UUID, permission: str = "backup.view") -> Backup:
    """Platform backups need the permission at platform scope; database backups on their server."""
    b = await db.get(Backup, backup_id)
    if b is None:
        raise NotFound("Backup not found")
    if b.kind == "database" and b.server_id:
        s = await db.get(Server, b.server_id)
        if s is not None:
            if not p.authz.can_see("server.view", server_ref(s)):
                raise NotFound("Backup not found")
            p.authz.require(permission, server_ref(s))
            return b
    p.authz.require(permission)
    return b


def _check_secrets_shape(body: RunSecrets) -> None:
    if body.identity:
        try:
            encryption.parse_identity(body.identity)
        except encryption.EncryptionError as exc:
            raise ValidationFailed(str(exc)) from exc


# ---- storage targets ---------------------------------------------------------------------------------
@router.get("/backup/targets", summary="Backup storage targets with their usage")
async def list_targets(db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    p.authz.require_anywhere("backup.view")
    used = {
        tid: (int(n), int(size or 0))
        for tid, n, size in (
            await db.execute(
                select(Backup.target_id, func.count(), func.sum(Backup.size_bytes))
                .where(Backup.status != "DELETED", Backup.location_kind == "target")
                .group_by(Backup.target_id)
            )
        ).all()
    }
    rows = list((await db.execute(select(BackupTarget).order_by(BackupTarget.name))).scalars())
    return [_target_out(t, used) for t in rows]


def _stored_target_config(body: TargetIn) -> dict[str, Any]:
    if body.kind in storage.FILESYSTEM_KINDS:
        return {"path": str(storage.validate_directory(str(body.config["path"])))}
    keys = ("endpoint_url", "bucket", "prefix", "region", "access_key_id", "addressing_style", "verify_tls", "ca_pem")
    out = {k: body.config[k] for k in keys if k in body.config}
    if out.get("ca_pem") in ("", "(set)"):
        out.pop("ca_pem")
    return out


@router.post("/backup/targets", status_code=201, summary="Add a backup storage target")
async def create_target(body: TargetIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("backup.target.manage")
    if (await db.execute(select(BackupTarget.id).where(BackupTarget.name == body.name))).first():
        raise Conflict("a storage target with this name already exists")
    tid = uuid.uuid4()
    t = BackupTarget(
        id=tid,
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        config=_stored_target_config(body),
        secret=storage.seal_secret(tid, body.secret_access_key) if body.kind == "s3" else None,
        created_by=p.username,
    )
    db.add(t)
    await db.flush()
    await audit.record(
        "backup_target.create",
        actor=p,
        category="backup",
        object_name=t.name,
        details={"kind": t.kind, "config": {k: v for k, v in t.config.items() if k != "ca_pem"}},
    )
    return _target_out(t)


@router.put("/backup/targets/{target_id}", summary="Change a backup storage target")
async def update_target(target_id: uuid.UUID, body: TargetIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("backup.target.manage")
    t = await _get_target(db, target_id)
    if body.kind != t.kind:
        raise ValidationFailed("the target kind cannot be changed; add a new target")
    # the API never returns the CA: omitted or "(set)" keeps the stored one, "" removes it
    keep_ca = body.config.get("ca_pem", "(set)") == "(set)" and t.config.get("ca_pem")
    t.name, t.enabled, t.config = body.name, body.enabled, _stored_target_config(body)
    if keep_ca:  # the API only ever shows "(set)": keep the stored certificate
        t.config = {**t.config, "ca_pem": keep_ca}
    if body.kind == "s3" and body.secret_access_key is not None:
        t.secret = storage.seal_secret(t.id, body.secret_access_key) if body.secret_access_key else None
    await audit.record(
        "backup_target.update",
        actor=p,
        category="backup",
        object_name=t.name,
        details={"enabled": t.enabled, "secret_changed": body.secret_access_key is not None},
    )
    return _target_out(t)


@router.delete("/backup/targets/{target_id}", summary="Remove a storage target (stored backups are not deleted)")
async def delete_target(target_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("backup.target.manage")
    t = await _get_target(db, target_id)
    if (await db.execute(select(BackupPolicy.id).where(BackupPolicy.target_id == t.id))).first():
        raise Conflict("policies still use this target: change or delete them first")
    n = (
        await db.execute(
            select(func.count()).select_from(Backup).where(Backup.target_id == t.id, Backup.status != "DELETED")
        )
    ).scalar_one()
    if n:
        raise Conflict(f"{n} backups in the catalog are stored on this target: delete them or keep the target")
    name = t.name
    await db.delete(t)
    await audit.record("backup_target.delete", actor=p, category="backup", object_name=name)
    return Message(message=f"Storage target {name} removed")


@router.post(
    "/backup/targets/{target_id}/check", summary="Write/read/delete probe", dependencies=[rate_limit("expensive")]
)
async def check_target(target_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("backup.target.manage")  # writes and deletes a probe object
    t = await _get_target(db, target_id)
    try:
        info = await storage.provider_for(t).check()
        ok, error = True, None
    except storage.StorageError as exc:
        info, ok, error = {}, False, str(exc)
    t.last_check_at, t.last_check_ok, t.last_check_error = datetime.now(UTC), ok, error
    return {"ok": ok, "error": error, "info": info}


@router.post(
    "/backup/targets/{target_id}/scan",
    summary="Register backups found in the target that this catalog does not know (disaster recovery)",
    dependencies=[rate_limit("expensive")],
)
async def scan_target(target_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, int]:
    p.authz.require("backup.target.manage")
    t = await _get_target(db, target_id)
    try:
        return await service.scan_target(db, t, p.username)
    except storage.StorageError as exc:
        raise ValidationFailed(str(exc)) from exc


# ---- policies ---------------------------------------------------------------------------------------
@router.get("/backup/policies", summary="Backup policies")
async def list_policies(db: DB, p: CurrentPrincipal) -> list[dict[str, Any]]:
    p.authz.require_anywhere("backup.view")
    last = dict(
        (
            await db.execute(
                select(Backup.policy_id, func.max(Backup.created_at))
                .where(Backup.status == "SUCCESS")
                .group_by(Backup.policy_id)
            )
        ).all()
    )
    rows = list((await db.execute(select(BackupPolicy).order_by(BackupPolicy.name))).scalars())
    return [_policy_out(x, last.get(x.id)) for x in rows]


async def _apply_policy(db: DB, pol: BackupPolicy, body: PolicyIn) -> None:
    await _get_target(db, body.target_id)
    pol.name, pol.description, pol.kind, pol.enabled = body.name, body.description, body.kind, body.enabled
    pol.target_id, pol.scope = body.target_id, body.scope()
    pol.schedule = body.schedule.model_dump()
    pol.encryption = body.encryption.model_dump(exclude_none=True)
    pol.retention = body.retention.model_dump()
    pol.channel_ids = [str(c) for c in body.channel_ids]
    pol.max_age_hours = body.max_age_hours
    pol.next_run_at = service.next_run(pol.schedule, datetime.now(UTC)) if body.enabled else None


@router.post("/backup/policies", status_code=201, summary="Create a backup policy")
async def create_policy(body: PolicyIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("backup.policy.manage")
    if (await db.execute(select(BackupPolicy.id).where(BackupPolicy.name == body.name))).first():
        raise Conflict("a policy with this name already exists")
    pol = BackupPolicy(created_by=p.username)
    await _apply_policy(db, pol, body)
    db.add(pol)
    await db.flush()
    await audit.record(
        "backup_policy.create", actor=p, category="backup", object_name=pol.name, details=_policy_audit(pol)
    )
    return _policy_out(pol)


def _policy_audit(pol: BackupPolicy) -> dict[str, Any]:
    return {
        "kind": pol.kind,
        "schedule": pol.schedule,
        "retention": pol.retention,
        "encryption_mode": (pol.encryption or {}).get("mode"),
        "target_id": str(pol.target_id),
    }


@router.put("/backup/policies/{policy_id}", summary="Change a backup policy")
async def update_policy(policy_id: uuid.UUID, body: PolicyIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("backup.policy.manage")
    pol = await db.get(BackupPolicy, policy_id)
    if pol is None:
        raise NotFound("Policy not found")
    await _apply_policy(db, pol, body)
    await audit.record(
        "backup_policy.update", actor=p, category="backup", object_name=pol.name, details=_policy_audit(pol)
    )
    return _policy_out(pol)


@router.delete("/backup/policies/{policy_id}", summary="Delete a policy (its backups are kept)")
async def delete_policy(policy_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("backup.policy.manage")
    pol = await db.get(BackupPolicy, policy_id)
    if pol is None:
        raise NotFound("Policy not found")
    name = pol.name
    await db.delete(pol)
    await audit.record("backup_policy.delete", actor=p, category="backup", object_name=name)
    return Message(message=f"Policy {name} deleted; its backups are kept (no longer subject to retention)")


@router.post("/backup/policies/{policy_id}/run", status_code=202, summary="Run a policy now")
async def run_policy(
    policy_id: uuid.UUID, db: DB, p: CurrentPrincipal, body: RunSecrets | None = None
) -> dict[str, Any]:
    pol = await db.get(BackupPolicy, policy_id)
    if pol is None:
        raise NotFound("Policy not found")
    if pol.kind == "platform":
        p.authz.require("backup.create")
    else:
        await _require_on_scope(db, p, pol.scope or {}, "backup.create")
    if (
        await db.execute(select(BackupJob.id).where(BackupJob.policy_id == pol.id, BackupJob.status.in_(JOB_ACTIVE)))
    ).first():
        raise Conflict("this policy is already running")
    enc = pol.encryption or {}
    if enc.get("mode") == "passphrase" and not enc.get("passphrase_ref") and not (body and body.passphrase):
        raise ValidationFailed("this policy encrypts with a passphrase: supply it for this run")
    job = await service.create_job(
        db,
        "platform_backup" if pol.kind == "platform" else "database_backup",
        params={},
        requested_by=p.username,
        policy_id=pol.id,
        target_id=pol.target_id,
        run_secrets=_run_secrets(body) if body else None,
    )
    await audit.record(
        "backup.request",
        actor=p,
        category="backup",
        object_name=pol.name,
        details={"job_id": str(job.id), "policy_id": str(pol.id)},
    )
    return _job_out(job)


@router.get("/backup/policies/{policy_id}/retention", summary="Which backups the retention rules keep and remove")
async def retention_preview(policy_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require_anywhere("backup.view")
    pol = await db.get(BackupPolicy, policy_id)
    if pol is None:
        raise NotFound("Policy not found")
    rows = list(
        (
            await db.execute(
                select(Backup).where(Backup.policy_id == pol.id, Backup.status.in_(("SUCCESS", "FAILED", "CORRUPTED")))
            )
        ).scalars()
    )
    now = datetime.now(UTC)
    keep = service.retention_keep(rows, pol.retention or {}, now, service._zone((pol.schedule or {}).get("time_zone")))
    has = service._has_rules(pol.retention or {})
    grace = now - timedelta(days=max(int((pol.retention or {}).get("keep_days") or 0), 7))
    remove = [b for b in rows if has and b.id not in keep and (b.status == "SUCCESS" or b.created_at < grace)]
    return {
        "keep": sorted(str(i) for i in keep),
        "remove": [str(b.id) for b in remove],
        "rules": pol.retention,
        "note": "Only backups created by this policy are ever removed; pinned backups and the newest successful backup are always kept.",
    }


async def _require_on_scope(db: DB, p: CurrentPrincipal, scope: dict[str, Any], permission: str) -> list[Server]:
    servers = await service.scope_servers(db, scope)
    if not servers:
        raise ValidationFailed("the scope matches no registered (non-simulated) server")
    for s in servers:
        if not p.authz.has(permission, server_ref(s)):
            raise PermissionDenied(
                f"Missing permission '{permission}' for server {s.name}", details={"permission": permission}
            )
    return servers


# ---- backups ----------------------------------------------------------------------------------------
@router.get("/backups", summary="Backup history")
async def list_backups(
    db: DB,
    p: CurrentPrincipal,
    kind: Literal["platform", "database"] | None = None,
    status: str | None = None,
    server_id: uuid.UUID | None = None,
    policy_id: uuid.UUID | None = None,
    include_deleted: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    p.authz.require_anywhere("backup.view")
    q = select(Backup)
    if kind:
        q = q.where(Backup.kind == kind)
    if status:
        q = q.where(Backup.status == status.upper())
    elif not include_deleted:
        q = q.where(Backup.status != "DELETED")
    if server_id:
        q = q.where(Backup.server_id == server_id)
    if policy_id:
        q = q.where(Backup.policy_id == policy_id)
    if not p.authz.has("backup.view"):  # scoped grants: database backups of visible servers only
        flt = p.authz.server_filter("backup.view")
        visible = select(Server.id).where(flt) if flt is not None else select(Server.id)
        q = q.where(Backup.kind == "database", Backup.server_id.in_(visible))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = list(
        (
            await db.execute(q.order_by(Backup.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
        ).scalars()
    )
    names = await _names(db)
    return {"items": [_backup_out(b, names) for b in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/backups/{backup_id}", summary="Backup details with its manifest")
async def get_backup(backup_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    b = await _get_backup(db, p, backup_id)
    out = _backup_out(b, await _names(db))
    out["manifest"] = b.manifest
    if b.kind == "database":
        prov = engines.provider(b.engine or "")
        out["restore_supported"] = prov.supports_restore
        out["restore_command"] = None if prov.supports_restore else prov.restore_command(b, "<new name>")
    return out


@router.post("/backups/platform", status_code=202, summary="Back up DB Manager itself (asynchronous)")
async def create_platform_backup(body: PlatformBackupIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require("backup.create")
    await _get_target(db, body.target_id)
    if body.encryption.mode == "passphrase" and not (body.passphrase or body.encryption.passphrase_ref):
        raise ValidationFailed("passphrase encryption needs a passphrase")
    if body.passphrase is not None and len(body.passphrase) < 12:
        raise ValidationFailed("the passphrase must have at least 12 characters")
    job = await service.create_job(
        db,
        "platform_backup",
        params={
            "components": body.scope.components,
            "metrics": body.scope.metrics,
            "metrics_days": body.scope.metrics_days,
            "encryption": body.encryption.model_dump(exclude_none=True),
        },
        requested_by=p.username,
        target_id=body.target_id,
        run_secrets=_run_secrets(body),
    )
    await audit.record(
        "backup.request",
        actor=p,
        category="backup",
        target_type="platform",
        details={"job_id": str(job.id), "components": body.scope.components},
    )
    return _job_out(job)


@router.post(
    "/backups/database", status_code=202, summary="Back up managed databases (asynchronous, per-asset progress)"
)
async def create_database_backup(body: DatabaseBackupIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    await _get_target(db, body.target_id)
    servers = await _require_on_scope(db, p, body.scope.stored(), "backup.create")
    if body.encryption.mode == "passphrase" and not (body.passphrase or body.encryption.passphrase_ref):
        raise ValidationFailed("passphrase encryption needs a passphrase")
    job = await service.create_job(
        db,
        "database_backup",
        params={"scope": body.scope.stored(), "encryption": body.encryption.model_dump(exclude_none=True)},
        requested_by=p.username,
        target_id=body.target_id,
        run_secrets=_run_secrets(body),
    )
    await audit.record(
        "backup.request",
        actor=p,
        category="backup",
        target_type="database",
        details={"job_id": str(job.id), "servers": [s.name for s in servers][:50]},
    )
    return _job_out(job)


@router.post("/backups/{backup_id}/verify", status_code=202, summary="Verify a stored backup (asynchronous)")
async def verify(backup_id: uuid.UUID, db: DB, p: CurrentPrincipal, body: RunSecrets | None = None) -> dict[str, Any]:
    b = await _get_backup(db, p, backup_id, "backup.verify")
    if body:
        _check_secrets_shape(body)
    job = await service.create_job(
        db,
        "verify",
        params={"backup_id": str(b.id)},
        requested_by=p.username,
        target_id=b.target_id,
        run_secrets=_run_secrets(body) if body else None,
    )
    return _job_out(job)


@router.post("/backups/{backup_id}/pin", summary="Keep a backup regardless of retention (or release it)")
async def pin(backup_id: uuid.UUID, db: DB, p: CurrentPrincipal, pinned: bool = True) -> dict[str, Any]:
    p.authz.require("backup.policy.manage")
    b = await _get_backup(db, p, backup_id)
    b.pinned = pinned
    await audit.record(
        "backup.pin" if pinned else "backup.unpin",
        actor=p,
        category="backup",
        target_type="backup",
        target_id=str(b.id),
    )
    return _backup_out(b, await _names(db))


@router.delete("/backups/{backup_id}", summary="Delete a stored backup (typed confirmation)")
async def delete_backup(backup_id: uuid.UUID, db: DB, p: CurrentPrincipal, confirm: str | None = None) -> Message:
    p.authz.require("backup.delete")
    b = await _get_backup(db, p, backup_id)
    if confirm != str(b.id)[:8]:
        raise ConfirmationRequired(
            "Deleting a backup removes it from storage. Confirm with the first 8 characters of its id.",
            details={"expected": str(b.id)[:8]},
        )
    if b.status in ("CREATING", "VERIFYING") and b.job_id:
        job = await db.get(BackupJob, b.job_id)
        if job is not None and job.status in JOB_ACTIVE:
            raise Conflict("the backup is still being written")
    try:
        await service.delete_backup(b.id, p.username)
    except (storage.StorageError, service.BackupError) as exc:
        await audit.record(
            "backup.delete",
            actor=p,
            category="backup",
            result="failure",
            failure_reason=str(exc),
            target_type="backup",
            target_id=str(b.id),
        )
        raise ValidationFailed(f"could not delete the stored files: {exc}") from exc
    await audit.record(
        "backup.delete",
        actor=p,
        category="backup",
        target_type="backup",
        target_id=str(b.id),
        details={"kind": b.kind, "server": b.server_name, "location_kind": b.location_kind},
    )
    note = " Files on the database server itself are not removed." if b.location_kind == "server" else ""
    return Message(message=f"Backup {str(b.id)[:8]} deleted.{note}")


@router.get("/backups/{backup_id}/download", summary="Download the stored (encrypted) backup file")
async def download(backup_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> StreamingResponse:
    raw = await db.get(Backup, backup_id)
    # a downloaded archive can be restored elsewhere: same permission as restoring it
    b = await _get_backup(
        db, p, backup_id, "restore.platform" if raw and raw.kind == "platform" else "restore.database"
    )
    if b.location_kind != "target" or not b.location or b.status != "SUCCESS":
        raise ValidationFailed("only successful backups stored in a storage target can be downloaded")
    t = await _get_target(db, b.target_id)  # type: ignore[arg-type]
    prov = storage.provider_for(t)
    fd, tmp = tempfile.mkstemp(prefix="dbm-dl-")
    os.close(fd)
    try:
        await prov.get(b.location, Path(tmp))
    except storage.StorageError as exc:
        os.unlink(tmp)
        raise ValidationFailed(str(exc)) from exc
    await audit.record(
        "backup.download",
        actor=p,
        category="backup",
        target_type="backup",
        target_id=str(b.id),
        details={"encrypted": b.encrypted},
    )

    async def _stream() -> AsyncIterator[bytes]:
        try:
            with open(tmp, "rb") as f:  # noqa: ASYNC230 - local temporary copy
                while chunk := f.read(1 << 20):
                    yield chunk
        finally:
            os.unlink(tmp)

    name = Path(b.location).name
    return StreamingResponse(
        _stream(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="dbm-{str(b.id)[:8]}-{name}"'},
    )


# ---- restore ----------------------------------------------------------------------------------------
async def _restore_permissions(db: DB, p: CurrentPrincipal, b: Backup, body: RestoreIn) -> Server | None:
    if b.kind == "platform":
        p.authz.require("restore.execute")
        p.authz.require("restore.platform")
        return None
    sid = body.server_id or b.server_id
    if sid is None:
        raise ValidationFailed("choose the server to restore to")
    s = await load_server(db, p, sid, "restore.execute")
    p.authz.require("restore.database", server_ref(s))
    return s


@router.post(
    "/backups/{backup_id}/restore/preview",
    summary="Validate, verify and analyse a restore (nothing is changed)",
    dependencies=[rate_limit("expensive")],
)
async def restore_preview(backup_id: uuid.UUID, body: RestoreIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    b = await _get_backup(db, p, backup_id)
    s = await _restore_permissions(db, p, b, body)
    _check_secrets_shape(body)
    if b.kind == "platform":
        keys = {"passphrase": body.passphrase, "identity": body.identity or service._identity_from_settings()}
        if (b.encryption or {}).get("mode") == "passphrase" and not keys["passphrase"] and b.policy_id:
            keys = await service._decrypt_material(None, b) | {k: v for k, v in keys.items() if v}
        try:
            result = await service.preview_platform_restore(b.id, body.components, keys)
        except (
            service.BackupError,
            platform.PlatformBackupError,
            encryption.EncryptionError,
            storage.StorageError,
        ) as exc:
            raise ValidationFailed(str(exc), code="restore_blocked") from exc
        result["warnings"] = _platform_warnings(result, p.username)
        result["confirm_phrase"] = PLATFORM_CONFIRM
        return result
    assert s is not None
    prov = engines.provider(b.engine or "")
    caps = await engines.capabilities(s)
    warnings = []
    if b.status != "SUCCESS":
        warnings.append(f"the backup status is {b.status}")
    if s.engine != b.engine:
        warnings.append(f"a {b.engine} backup cannot be restored to a {s.engine} server")
    from app.services import connections

    try:
        connections.require_administration(s)
    except connections.MonitoringOnly as exc:
        warnings.append(exc.message)
    return {
        "backup_id": str(b.id),
        "server": {"id": str(s.id), "name": s.name, "engine": s.engine},
        "capabilities": caps,
        "restore_supported": prov.supports_restore,
        "restore_command": None
        if prov.supports_restore
        else prov.restore_command(b, body.target_database or "<new name>"),
        "integrity": b.verification,
        "impact": {
            "creates": body.target_database,
            "overwrites": None,
            "note": "The backup is restored into a new database (or schema); restores never overwrite an existing one.",
        },
        "warnings": warnings,
        "confirm_phrase": body.target_database,
    }


def _platform_warnings(result: dict[str, Any], me: str) -> list[str]:
    w = []
    compat = result.get("compatibility") or {}
    if compat.get("status") == "migration_required":
        w.append("Older schema: columns added since then take their defaults (confirm with accept_migration).")
    if compat.get("status") == "unsupported":
        w.append("This backup cannot be restored by this version.")
    if (result.get("keys") or {}).get("missing"):
        w.append(
            "Master keys used by encrypted values in the backup are not configured: "
            + ", ".join(result["keys"]["missing"])
        )
    impact = result.get("impact") or {}
    if impact.get("error"):
        w.append("The trial restore failed: " + impact["error"])
    users = (impact.get("tables") or {}).get("users")
    if users and users.get("deleted"):
        w.append(
            f"{users['deleted']} user account(s) that exist now are not in the backup and will be removed (check that '{me}' is in the backup)."
        )
    for t, n in (impact.get("side_effects") or {}).items():
        if n < 0:
            w.append(f"{-n} row(s) of {t} (not restored) will be removed by cascading deletes.")
    return w


@router.post("/backups/{backup_id}/restore", status_code=202, summary="Start a restore (typed confirmation)")
async def restore(backup_id: uuid.UUID, body: RestoreIn, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    b = await _get_backup(db, p, backup_id)
    s = await _restore_permissions(db, p, b, body)
    _check_secrets_shape(body)
    if b.status not in ("SUCCESS",) and not (b.kind == "platform" and b.status == "VERIFYING"):
        raise ValidationFailed(f"the backup status is {b.status}: verify it first")
    if (
        await db.execute(
            select(BackupJob.id).where(BackupJob.kind.in_(service.RESTORE_KINDS), BackupJob.status.in_(JOB_ACTIVE))
        )
    ).first():
        raise Conflict("another restore is running")
    if b.kind == "platform":
        if body.confirm != PLATFORM_CONFIRM:
            raise ConfirmationRequired(
                f"Restoring replaces DB Manager's configuration. Type {PLATFORM_CONFIRM!r} to confirm.",
                details={"expected": PLATFORM_CONFIRM},
            )
        components = body.components or list((b.manifest or {}).get("components") or [])
        try:
            platform.check_components(components)
        except platform.PlatformBackupError as exc:
            raise ValidationFailed(str(exc)) from exc
        params: dict[str, Any] = {
            "backup_id": str(b.id),
            "components": components,
            "pre_restore_backup": body.pre_restore_backup,
            "accept_migration": body.accept_migration,
            "accept_missing_keys": body.accept_missing_keys,
        }
        kind = "platform_restore"
    else:
        assert s is not None
        if not body.target_database:
            raise ValidationFailed("choose the name of the new database (or schema) to restore into")
        try:
            engines.check_identifier(body.target_database)
        except engines.BackupProviderError as exc:
            raise ValidationFailed(str(exc)) from exc
        if body.confirm != body.target_database:
            raise ConfirmationRequired(
                "Type the new database name to confirm the restore.", details={"expected": body.target_database}
            )
        from app.services import connections

        connections.require_administration(s)
        params = {"backup_id": str(b.id), "server_id": str(s.id), "target_database": body.target_database}
        kind = "database_restore"
    job = await service.create_job(
        db, kind, params=params, requested_by=p.username, target_id=b.target_id, run_secrets=_run_secrets(body)
    )
    await audit.record(
        "restore.request",
        actor=p,
        category="backup",
        risk_class="DESTRUCTIVE" if kind == "platform_restore" else "PRIVILEGED_CHANGE",
        target_type="backup",
        target_id=str(b.id),
        server=s,
        object_name=body.target_database,
        details={"job_id": str(job.id), **{k: v for k, v in params.items() if k != "backup_id"}},
    )
    return _job_out(job)


# ---- jobs -------------------------------------------------------------------------------------------
@router.get("/backup/jobs", summary="Recent backup, verification and restore jobs")
async def list_jobs(
    db: DB, p: CurrentPrincipal, active: bool = False, limit: int = Query(50, ge=1, le=500)
) -> list[dict[str, Any]]:
    p.authz.require_anywhere("backup.view")
    q = select(BackupJob)
    if active:
        q = q.where(BackupJob.status.in_(JOB_ACTIVE))
    rows = list((await db.execute(q.order_by(BackupJob.created_at.desc()).limit(limit))).scalars())
    return [_job_out(j) for j in rows]


@router.get("/backup/jobs/{job_id}", summary="One job with per-asset progress")
async def get_job(job_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require_anywhere("backup.view")
    j = await db.get(BackupJob, job_id)
    if j is None:
        raise NotFound("Job not found")
    return _job_out(j)


@router.post("/backup/jobs/{job_id}/cancel", summary="Ask a queued or running job to stop")
async def cancel_job(job_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require_anywhere("backup.create")
    j = await db.get(BackupJob, job_id)
    if j is None:
        raise NotFound("Job not found")
    if j.kind == "platform_restore" and j.status == "RUNNING":
        raise Conflict("a running platform restore cannot be cancelled (it commits in one transaction)")
    if j.status == "QUEUED":
        j.status, j.finished_at, j.error = "CANCELLED", datetime.now(UTC), "cancelled before it started"
    elif j.status in ("RUNNING", "VERIFYING"):
        j.cancel_requested = True
    else:
        raise Conflict(f"the job is already {j.status}")
    await audit.record("backup.cancel", actor=p, category="backup", details={"job_id": str(j.id), "kind": j.kind})
    return _job_out(j)


# ---- overview / capabilities / protection ------------------------------------------------------------
@router.get("/backup/overview", summary="Backup dashboard: health, recent activity, coverage")
async def overview(db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    p.authz.require_anywhere("backup.view")
    now = datetime.now(UTC)
    since = now - timedelta(days=30)
    by_status = dict(
        (
            await db.execute(
                select(Backup.status, func.count()).where(Backup.created_at >= since).group_by(Backup.status)
            )
        ).all()
    )
    last_platform = (
        await db.execute(
            select(func.max(Backup.created_at)).where(Backup.kind == "platform", Backup.status == "SUCCESS")
        )
    ).scalar_one_or_none()
    failures = list(
        (
            await db.execute(
                select(Backup)
                .where(Backup.status.in_(("FAILED", "CORRUPTED")), Backup.created_at >= now - timedelta(days=7))
                .order_by(Backup.created_at.desc())
                .limit(10)
            )
        ).scalars()
    )
    stored = (
        await db.execute(
            select(func.coalesce(func.sum(Backup.size_bytes), 0)).where(
                Backup.status == "SUCCESS", Backup.location_kind == "target"
            )
        )
    ).scalar_one()
    policies = list((await db.execute(select(BackupPolicy).order_by(BackupPolicy.next_run_at))).scalars())
    flt = p.authz.server_filter("server.view")
    sq = select(Server).where(Server.is_demo.is_(False))
    if flt is not None:
        sq = sq.where(flt)
    servers = list((await db.execute(sq)).scalars())
    prot = await service.protection(db, servers)
    coverage = {"protected": 0, "at_risk": 0, "not_protected": 0}
    for v in prot.values():
        coverage[v["status"]] += 1
    names = await _names(db)
    return {
        "last_30_days": by_status,
        "last_platform_backup_at": last_platform,
        "platform_protection": await service.platform_protection(db),
        "recent_failures": [_backup_out(b, names) for b in failures],
        "stored_bytes": int(stored),
        "running_jobs": [
            _job_out(j) for j in (await db.execute(select(BackupJob).where(BackupJob.status.in_(JOB_ACTIVE)))).scalars()
        ],
        "next_runs": [
            {"policy_id": str(x.id), "name": x.name, "next_run_at": x.next_run_at, "last_status": x.last_status}
            for x in policies
            if x.enabled and x.next_run_at
        ][:10],
        "coverage": coverage,
        "servers": len(servers),
    }


@router.get("/backup/capabilities", summary="What DB Manager can back up and restore per engine")
async def capabilities(db: DB, p: CurrentPrincipal, server_id: uuid.UUID | None = None) -> dict[str, Any]:
    p.authz.require_anywhere("backup.view")
    found = await engines.tools()
    if server_id:
        s = await load_server(db, p, server_id)
        return {"server_id": str(s.id), "engine": s.engine, **(await engines.capabilities(s, found))}
    from types import SimpleNamespace

    engines_out = {}
    for name in engines.PROVIDERS:
        fake = SimpleNamespace(engine=name, deployment="self-managed", host="", is_demo=False, capabilities={})
        engines_out[name] = await engines.capabilities(fake, found)
    return {
        "engines": engines_out,
        "tools": found,
        "platform": {"components": platform.COMPONENT_LABELS, "excluded": platform.EXCLUDED},
    }


@router.get("/servers/{server_id}/backup-protection", summary="Asset protection status of one server")
async def server_protection(server_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, Any]:
    s = await load_server(db, p, server_id)
    p.authz.require("backup.view", server_ref(s))
    prot = (await service.protection(db, [s]))[str(s.id)]
    recent = list(
        (
            await db.execute(
                select(Backup)
                .where(Backup.server_id == s.id, Backup.status != "DELETED")
                .order_by(Backup.created_at.desc())
                .limit(10)
            )
        ).scalars()
    )
    return {
        **prot,
        "capabilities": await engines.capabilities(s),
        "registration": await service.platform_protection(db),
        "recent": [_backup_out(b, await _names(db)) for b in recent],
    }


@router.post(
    "/backup/keys/generate",
    summary="Generate an age key pair (shown once, never stored)",
    dependencies=[rate_limit("expensive")],
)
async def generate_key(p: CurrentPrincipal) -> dict[str, str]:
    p.authz.require_anywhere("backup.policy.manage")
    identity, recipient = encryption.generate_identity()
    await audit.record("backup.key_generate", actor=p, category="backup", details={"recipient": recipient})
    return {
        "recipient": recipient,
        "identity": identity,
        "note": "Store the identity (AGE-SECRET-KEY-1…) in your password manager or vault NOW: DB Manager does not keep it, "
        "and backups encrypted to this recipient cannot be restored without it.",
    }
