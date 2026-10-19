"""Backup & restore (milestone 0.12): storage targets, policies, jobs and backup records."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk, utcnow

#: backup record states (``SUCCESS`` only after the stored copy was read back and verified)
BACKUP_STATES = ("CREATING", "VERIFYING", "SUCCESS", "FAILED", "CORRUPTED", "DELETED")
#: asynchronous job states
JOB_STATES = ("QUEUED", "RUNNING", "VERIFYING", "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "CANCELLED")
JOB_ACTIVE = ("QUEUED", "RUNNING", "VERIFYING")


class BackupTarget(UUIDPk, Timestamps, Base):
    """Where backups are stored: a directory (local disk, mounted volume, NFS mount) under
    ``DBM_BACKUP_ALLOWED_ROOTS`` or an S3-compatible bucket."""

    __tablename__ = "backup_targets"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    #: local | mounted | nfs | s3
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: filesystem: {"path"}; s3: {"endpoint_url", "bucket", "prefix", "region", "access_key_id",
    #: "addressing_style", "verify_tls", "ca_pem"}
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: envelope-encrypted secret settings (S3 secret access key); never returned
    secret: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_check_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128))


class BackupPolicy(UUIDPk, Timestamps, Base):
    __tablename__ = "backup_policies"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    #: platform | database
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: platform: {"components": [...], "metrics": "none|export", "metrics_days": n}
    #: database: {"server_ids": [...], "environment_ids": [...], "site_ids": [...], "tags": [...],
    #: "databases": [...] (empty = default database), "method": "logical|native"}
    scope: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backup_targets.id", ondelete="RESTRICT"), nullable=False)
    #: {"frequency": "manual|daily|weekly|monthly", "time": "HH:MM", "time_zone": "Europe/Paris",
    #: "weekday": 1-7 (weekly), "day_of_month": 1-28 (monthly)}
    schedule: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: {"mode": "none|recipient|passphrase", "recipient": "age1…", "passphrase_ref": "env:NAME|file:/path"}
    encryption: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: {"keep_last": n, "keep_days": n, "keep_daily": n, "keep_weekly": n, "keep_monthly": n}
    retention: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: notification channels told about failures (backup failed, no recent success, storage
    #: unavailable, verification failed, retention cleanup failed)
    channel_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    #: alert when no successful backup within this many hours (0 = derived from the schedule)
    max_age_hours: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    #: last "no successful backup" notification (de-duplication)
    overdue_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(128))


class BackupJob(UUIDPk, Base):
    __tablename__ = "backup_jobs"
    __table_args__ = (
        Index("ix_backup_jobs_status", "status", "created_at"),
        Index("ix_backup_jobs_policy", "policy_id", "created_at"),
    )

    #: platform_backup | database_backup | verify | platform_restore | database_restore | retention
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)  # manual|schedule
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("backup_policies.id", ondelete="SET NULL"))
    target_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("backup_targets.id", ondelete="SET NULL"))
    #: what to do (components, servers, backup id …); secrets supplied for this run only are
    #: sealed under "sealed" with the platform key ring and removed when the job ends
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: {"total": n, "done": n, "assets": [{"key", "name", "status", "backup_id", "error"}], "step": "…"}
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Backup(UUIDPk, Base):
    __tablename__ = "backups"
    __table_args__ = (
        Index("ix_backups_created", "created_at"),
        Index("ix_backups_policy", "policy_id", "created_at"),
        Index("ix_backups_server", "server_id", "created_at"),
    )

    #: platform | database
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="CREATING", nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("backup_jobs.id", ondelete="SET NULL"))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("backup_policies.id", ondelete="SET NULL"))
    target_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("backup_targets.id", ondelete="SET NULL"))
    #: database backups: the asset (kept by name when the server is removed)
    server_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("servers.id", ondelete="SET NULL"))
    server_name: Mapped[str | None] = mapped_column(String(255))
    engine: Mapped[str | None] = mapped_column(String(32))
    database_name: Mapped[str | None] = mapped_column(String(255))
    #: platform | logical | native
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    #: tool or statement that produced it (e.g. "pg_dump 16.4", "BACKUP DATABASE")
    tool: Mapped[str | None] = mapped_column(String(128))
    #: target: object key relative to the target root; server-side: path on the database server
    location: Mapped[str | None] = mapped_column(Text)
    #: target (copied to the storage target) | server (written on the database server itself)
    location_kind: Mapped[str] = mapped_column(String(16), default="target", nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: {"mode", "algorithm", "recipient"}; never a key or passphrase
    encryption: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    app_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str | None] = mapped_column(String(32))
    format_version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: {"level": "checksum|content|engine", "ok": bool, "at": iso, "details": {...}}
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(String(128))
    #: kept by retention regardless of the policy's rules
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), nullable=False)
