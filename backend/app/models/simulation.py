"""Simulation lifecycle records (generate / regenerate / remove)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPk, utcnow


class SimulationRun(UUIDPk, Base):
    """One simulation operation requested by an administrator.

    Generation runs on the background job queue, so the row also carries progress. The
    newest completed ``generate``/``regenerate`` run describes the simulated fleet that is
    currently present (profile, seed, who generated it and when).
    """

    __tablename__ = "simulation_runs"
    __table_args__ = (Index("ix_simulation_runs_created", "created_at"),)

    operation: Mapped[str] = mapped_column(String(16), nullable=False)  # generate|regenerate|remove|legacy
    profile: Mapped[str | None] = mapped_column(String(32))
    seed: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", nullable=False
    )  # pending|running|completed|failed
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0..100
    phase: Mapped[str | None] = mapped_column(String(128))
    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    requested_by: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(64))
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
