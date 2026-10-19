"""Alerting: rules, alert instances, maintenance windows, notification channels, deliveries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPk, utcnow

#: alert states that hold the (rule, server) de-duplication slot
ACTIVE_STATES = ("pending", "firing", "acknowledged")


class AlertRule(UUIDPk, Timestamps, Base):
    __tablename__ = "alert_rules"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: metric (threshold on a catalog metric) | health (server health state) | collector (failing collection)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    metric: Mapped[str | None] = mapped_column(String(128))
    operator: Mapped[str | None] = mapped_column(String(4))  # > >= < <= == !=
    threshold: Mapped[float | None] = mapped_column(Float)
    #: health rules: the server health states that fire (e.g. ["unavailable"])
    health_states: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    #: the condition must hold this long before the alert fires (0 = immediately)
    for_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # critical|high|medium|low|info
    #: all | environment (scope_id = environment id) | server (server id) | tag (``key`` or ``key=value``)
    scope_type: Mapped[str] = mapped_column(String(16), default="all", nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(64))
    #: metric rules: ``latest`` collector value, avg|min|max over ``window_seconds`` of history, or
    #: ``zscore``: standard deviations of the current value (latest, or avg over ``window_seconds``)
    #: from the server's own baseline over ``baseline_days``
    aggregation: Mapped[str] = mapped_column(String(8), default="latest", server_default="latest", nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    #: zscore rules: days of history the baseline (mean, stddev) is computed from
    baseline_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7", nullable=False)
    channel_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    notify_on_resolve: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: repeat the "firing" notification every N minutes until acknowledged or resolved (0 = never)
    renotify_minutes: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128))


class Alert(UUIDPk, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        # one active alert per (rule, server): repeated evaluations update it instead of duplicating
        Index(
            "uq_alerts_active_rule_server",
            "rule_id",
            "server_id",
            unique=True,
            postgresql_where=text("status IN ('pending','firing','acknowledged')"),
        ),
        Index("ix_alerts_status", "status"),
        Index("ix_alerts_server", "server_id"),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False)
    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pending|firing|acknowledged|resolved
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str | None] = mapped_column(String(128))
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suppressed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("maintenance_windows.id", ondelete="SET NULL"))
    pending_since: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(128))
    ack_comment: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(128))  # "auto" or a username
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    #: last "firing" notification; None while suppressed and not yet notified
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notify_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)


class AlertComment(UUIDPk, Base):
    __tablename__ = "alert_comments"
    __table_args__ = (Index("ix_alert_comments_alert", "alert_id", "created_at"),)

    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False)
    author: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class MaintenanceWindow(UUIDPk, Timestamps, Base):
    __tablename__ = "maintenance_windows"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(String(16), default="all", nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(64))
    #: first (or only) occurrence
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: none | daily | weekly — recurring windows repeat the first occurrence's local time of day
    #: (in ``time_zone``, so they follow daylight saving time) until ``repeat_until``
    recurrence: Mapped[str] = mapped_column(String(8), default="none", server_default="none", nullable=False)
    #: weekly: ISO weekdays 1 (Monday) … 7 (Sunday)
    weekdays: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC", nullable=False)
    repeat_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(128))


class NotificationChannel(UUIDPk, Timestamps, Base):
    __tablename__ = "notification_channels"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # webhook|email
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: non-secret settings (webhook URL, recipients, SMTP host override …)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    #: envelope-encrypted secret settings (webhook signing secret, SMTP password); never returned
    secret: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    min_severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    #: alerts on simulated servers are only sent when explicitly enabled
    send_for_simulated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: webhook payload format: generic (DBManager JSON) | slack | teams
    preset: Mapped[str] = mapped_column(String(16), default="generic", server_default="generic", nullable=False)
    #: routing: {"subscribe_all": bool, "environment_ids": [...], "tags": ["k" | "k=v"], "engines": [...]}
    #: — filters narrow what the channel receives; subscribe_all also delivers alerts of rules that
    #: do not list the channel
    routes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(128))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("ix_notification_deliveries_channel_created", "channel_id", "created_at"),
        Index("ix_notification_deliveries_retry", "next_attempt_at", postgresql_where=text("status = 'retrying'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False
    )
    alert_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("alerts.id", ondelete="SET NULL"))
    event: Mapped[str] = mapped_column(String(16), nullable=False)  # firing|resolved|test
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # sent|failed|skipped|retrying
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    #: retries with backoff: when the next attempt is due, and what to send (alert snapshot)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
