"""Time-series metrics, query statistics, collector jobs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPk, utcnow


class MetricSample(Base):
    """LEGACY: metric history before the time-series database was introduced.

    New telemetry is written to the time-series database (``app.timeseries``). This table is
    only read by ``python -m app.cli metrics migrate-to-timeseries`` and written when
    ``DBM_METRICS_PG_DUAL_WRITE=true`` (transition period). It is pruned by the retention job
    and will be dropped by a later migration (see docs/monitoring/timeseries-storage.md, phase F).
    """

    __tablename__ = "metric_samples"
    __table_args__ = (
        Index("ix_metric_samples_server_metric_ts", "server_id", "metric", "ts"),
        Index("ix_metric_samples_metric_ts", "metric", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    metric: Mapped[str] = mapped_column(String(96), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    labels: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class QueryStatSnapshot(Base):
    """LEGACY: query statistic snapshots (see :class:`MetricSample`). Query telemetry is now
    stored as fingerprint-keyed series in the time-series database plus
    :class:`QueryFingerprint` metadata."""

    __tablename__ = "query_stat_snapshots"
    __table_args__ = (
        Index("ix_query_stats_server_ts", "server_id", "ts"),
        Index("ix_query_stats_server_query", "server_id", "query_id", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    query_id: Mapped[str] = mapped_column(String(64), nullable=False)
    database: Mapped[str | None] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(256))
    query_text: Mapped[str] = mapped_column(Text, nullable=False)  # normalized / masked text
    calls: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_ms: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    mean_ms: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    max_ms: Mapped[float | None] = mapped_column(Float)
    rows: Mapped[int | None] = mapped_column(BigInteger)
    cpu_ms: Mapped[float | None] = mapped_column(Float)
    logical_reads: Mapped[int | None] = mapped_column(BigInteger)
    physical_reads: Mapped[int | None] = mapped_column(BigInteger)
    temp_blocks: Mapped[int | None] = mapped_column(BigInteger)
    lock_wait_ms: Mapped[float | None] = mapped_column(Float)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class QueryFingerprint(Base):
    """Metadata of one normalized statement on one server.

    The time-series database only knows the 16-hex-digit ``fingerprint`` label; the (masked,
    literal-free) statement text, the database and the engine's own query id live here.
    """

    __tablename__ = "query_fingerprints"
    __table_args__ = (
        UniqueConstraint("server_id", "fingerprint", "database", name="uq_query_fingerprints_server_fp_db"),
        Index("ix_query_fingerprints_server_last_seen", "server_id", "last_seen"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    database: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str | None] = mapped_column(String(256))
    normalized_query: Mapped[str] = mapped_column(Text, nullable=False)
    engine_query_id: Mapped[str | None] = mapped_column(String(64))
    data_source: Mapped[str] = mapped_column(String(16), nullable=False, default="real")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class CollectorJob(UUIDPk, Base):
    """Background job queue + history. Claimed with SELECT ... FOR UPDATE SKIP LOCKED."""

    __tablename__ = "collector_jobs"
    __table_args__ = (
        Index("ix_collector_jobs_claim", "status", "run_after"),
        Index("ix_collector_jobs_server_kind", "server_id", "kind", "created_at"),
        Index(
            "uq_collector_jobs_active",
            "server_id",
            "kind",
            unique=True,
            postgresql_where=text("status IN ('pending','running')"),
            postgresql_nulls_not_distinct=True,  # global jobs have server_id NULL (PostgreSQL 15+)
        ),
    )

    server_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), default="schedule", nullable=False)  # schedule|manual
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_by: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
