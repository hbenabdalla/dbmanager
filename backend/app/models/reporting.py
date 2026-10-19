"""Scheduled report delivery (milestone 0.14)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk, utcnow


class ReportSchedule(UUIDPk, Timestamps, Base):
    """A report generated on a schedule and e-mailed to notification channels.

    The report runs with the **owner's** permissions at run time (not the creator's at creation
    time): a schedule never shows more than its owner may currently see, and it stops when the
    owner is disabled or loses access to the report."""

    __tablename__ = "report_schedules"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    report_key: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)  # csv|pdf
    #: app.services.reports.ReportParams
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: {"frequency": daily|weekly|monthly, "time": "HH:MM", "time_zone": ..., "weekday": 1-7, "day_of_month": 1-28}
    schedule: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: e-mail notification channels the file is sent to
    channel_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128))


class ReportRun(UUIDPk, Base):
    """One generation + delivery of a scheduled report (also "send now")."""

    __tablename__ = "report_runs"
    __table_args__ = (Index("ix_report_runs_schedule_started", "schedule_id", "started_at"),)

    schedule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("report_schedules.id", ondelete="CASCADE"))
    report_key: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    #: success | partial (some channels failed) | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # schedule|manual
    triggered_by: Mapped[str | None] = mapped_column(String(128))
    rows: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    #: per channel: {"channel_id", "name", "status": sent|failed, "recipients": n, "error"}
    deliveries: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
