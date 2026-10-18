"""alerting completion: tag scope, range aggregation, re-notification, alert comments, recurring
maintenance windows with time zones, channel routing/presets/SMTP security, delivery retries

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("alert_rules", sa.Column("aggregation", sa.String(8), nullable=False, server_default="latest"))
    op.add_column("alert_rules", sa.Column("window_seconds", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("alert_rules", sa.Column("renotify_minutes", sa.Integer(), nullable=False, server_default="0"))

    op.add_column("alerts", sa.Column("notified_at", TS))
    op.add_column("alerts", sa.Column("notify_count", sa.Integer(), nullable=False, server_default="0"))
    # existing firing/acknowledged alerts were notified when they fired (unless suppressed)
    op.execute(
        "UPDATE alerts SET notified_at = fired_at, notify_count = 1 "
        "WHERE fired_at IS NOT NULL AND suppressed = false"
    )

    op.create_table(
        "alert_comments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("alert_id", sa.UUID(), sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author", sa.String(128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_alert_comments_alert", "alert_comments", ["alert_id", "created_at"])

    op.add_column("maintenance_windows", sa.Column("recurrence", sa.String(8), nullable=False, server_default="none"))
    op.add_column(
        "maintenance_windows",
        sa.Column("weekdays", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("maintenance_windows", sa.Column("time_zone", sa.String(64), nullable=False, server_default="UTC"))
    op.add_column("maintenance_windows", sa.Column("repeat_until", TS))

    op.add_column("notification_channels", sa.Column("preset", sa.String(16), nullable=False, server_default="generic"))
    op.add_column(
        "notification_channels",
        sa.Column("routes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )

    op.add_column("notification_deliveries", sa.Column("next_attempt_at", TS))
    op.add_column("notification_deliveries", sa.Column("payload", postgresql.JSONB()))
    op.create_index(
        "ix_notification_deliveries_retry",
        "notification_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text("status = 'retrying'"),
    )


def downgrade() -> None:
    op.drop_index("ix_notification_deliveries_retry", table_name="notification_deliveries")
    op.drop_column("notification_deliveries", "payload")
    op.drop_column("notification_deliveries", "next_attempt_at")
    op.drop_column("notification_channels", "routes")
    op.drop_column("notification_channels", "preset")
    for c in ("repeat_until", "time_zone", "weekdays", "recurrence"):
        op.drop_column("maintenance_windows", c)
    op.drop_index("ix_alert_comments_alert", table_name="alert_comments")
    op.drop_table("alert_comments")
    op.drop_column("alerts", "notify_count")
    op.drop_column("alerts", "notified_at")
    for c in ("renotify_minutes", "window_seconds", "aggregation"):
        op.drop_column("alert_rules", c)
