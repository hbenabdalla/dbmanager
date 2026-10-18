"""alerting: rules, alerts, maintenance windows, notification channels and deliveries

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def _stamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", TS, server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("metric", sa.String(128)),
        sa.Column("operator", sa.String(4)),
        sa.Column("threshold", sa.Float()),
        sa.Column("health_states", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("for_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False, server_default="all"),
        sa.Column("scope_id", sa.String(64)),
        sa.Column("channel_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("notify_on_resolve", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(128)),
        *_stamps(),
    )
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("scope_type", sa.String(16), nullable=False, server_default="all"),
        sa.Column("scope_id", sa.String(64)),
        sa.Column("starts_at", TS, nullable=False),
        sa.Column("ends_at", TS, nullable=False),
        sa.Column("created_by", sa.String(128)),
        *_stamps(),
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("rule_id", sa.UUID(), sa.ForeignKey("alert_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("server_id", sa.UUID(), sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("metric", sa.String(128)),
        sa.Column("value", sa.Float()),
        sa.Column("threshold", sa.Float()),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("suppressed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "suppressed_by", sa.UUID(), sa.ForeignKey("maintenance_windows.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("pending_since", TS, nullable=False),
        sa.Column("fired_at", TS),
        sa.Column("acknowledged_at", TS),
        sa.Column("acknowledged_by", sa.String(128)),
        sa.Column("ack_comment", sa.Text()),
        sa.Column("resolved_at", TS),
        sa.Column("resolved_by", sa.String(128)),
        sa.Column("last_evaluated_at", TS, nullable=False),
    )
    op.create_index(
        "uq_alerts_active_rule_server",
        "alerts",
        ["rule_id", "server_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','firing','acknowledged')"),
    )
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("ix_alerts_server", "alerts", ["server_id"])
    op.create_table(
        "notification_channels",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("secret", postgresql.JSONB()),
        sa.Column("min_severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("send_for_simulated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(128)),
        *_stamps(),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "channel_id", sa.UUID(), sa.ForeignKey("notification_channels.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("alert_id", sa.UUID(), sa.ForeignKey("alerts.id", ondelete="SET NULL")),
        sa.Column("event", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_code", sa.Integer()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_notification_deliveries_channel_created", "notification_deliveries", ["channel_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("notification_deliveries")
    op.drop_table("notification_channels")
    op.drop_table("alerts")
    op.drop_table("maintenance_windows")
    op.drop_table("alert_rules")
