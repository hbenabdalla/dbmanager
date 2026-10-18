"""Performance recommendations and security findings produced by the rule engines."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPk, utcnow


class _FindingColumns:
    fingerprint: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(32), nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    database: Mapped[str | None] = mapped_column(String(256))
    affected_object: Mapped[str | None] = mapped_column(String(512))
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    finding: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    risk: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    guidance: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="open", nullable=False
    )  # open|acknowledged|dismissed|resolved
    status_reason: Mapped[str | None] = mapped_column(Text)
    status_changed_by: Mapped[str | None] = mapped_column(String(128))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Recommendation(UUIDPk, _FindingColumns, Base):
    __tablename__ = "recommendations"
    __table_args__ = (Index("ix_recommendations_server_status", "server_id", "status"),)

    expected_impact: Mapped[str] = mapped_column(Text, nullable=False)


class SecurityFinding(UUIDPk, _FindingColumns, Base):
    __tablename__ = "security_findings"
    __table_args__ = (Index("ix_security_findings_server_status", "server_id", "status"),)

    remediation: Mapped[str | None] = mapped_column(Text)
