"""reporting: scheduled report delivery

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
JSON = postgresql.JSONB()


def upgrade() -> None:
    op.create_table(
        "report_schedules",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("report_key", sa.String(64), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("params", JSON, nullable=False),
        sa.Column("schedule", JSON, nullable=False),
        sa.Column("channel_ids", JSON, nullable=False),
        sa.Column("owner_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", TS),
        sa.Column("last_run_at", TS),
        sa.Column("last_status", sa.String(16)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_by", sa.String(128)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "report_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("schedule_id", sa.UUID(), sa.ForeignKey("report_schedules.id", ondelete="CASCADE")),
        sa.Column("report_key", sa.String(64), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("triggered_by", sa.String(128)),
        sa.Column("rows", sa.Integer()),
        sa.Column("size_bytes", sa.Integer()),
        sa.Column("deliveries", JSON, nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS),
    )
    op.create_index("ix_report_runs_schedule_started", "report_runs", ["schedule_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_report_runs_schedule_started", table_name="report_runs")
    op.drop_table("report_runs")
    op.drop_table("report_schedules")
