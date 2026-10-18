"""Append-only audit trail, SQL workspace history and platform settings."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPk, utcnow


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_ts", "ts"),
        Index("ix_audit_user_ts", "username", "ts"),
        Index("ix_audit_server_ts", "server_id", "ts"),
        Index("ix_audit_action_ts", "action", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    username: Mapped[str | None] = mapped_column(String(128))
    auth_source: Mapped[str | None] = mapped_column(String(16))
    source_ip: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="admin")
    risk_class: Mapped[str | None] = mapped_column(String(24))
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(128))
    server_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    server_name: Mapped[str | None] = mapped_column(String(128))
    database: Mapped[str | None] = mapped_column(String(256))
    object_name: Mapped[str | None] = mapped_column(String(512))
    result: Mapped[str] = mapped_column(String(16), nullable=False)  # success|failure|denied
    failure_reason: Mapped[str | None] = mapped_column(Text)
    change_reason: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class QueryHistory(UUIDPk, Base):
    __tablename__ = "query_history"
    __table_args__ = (Index("ix_query_history_user_ts", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    database: Mapped[str | None] = mapped_column(String(256))
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    statement_type: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_class: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    row_count: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(128))
