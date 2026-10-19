"""performance intelligence: baseline (z-score) alert rules, recommendation validation and
saved execution plans

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("alert_rules", sa.Column("baseline_days", sa.Integer(), nullable=False, server_default="7"))
    op.add_column("recommendations", sa.Column("validation", postgresql.JSONB(), nullable=True))
    op.create_table(
        "query_plan_snapshots",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("server_id", sa.UUID(), sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("database", sa.String(256)),
        sa.Column("fingerprint", sa.String(32)),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("statement_sha256", sa.String(64), nullable=False),
        sa.Column("engine", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("total_cost", sa.Float()),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.String(128)),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_query_plan_snapshots_server_created", "query_plan_snapshots", ["server_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_query_plan_snapshots_server_created", table_name="query_plan_snapshots")
    op.drop_table("query_plan_snapshots")
    op.drop_column("recommendations", "validation")
    op.drop_column("alert_rules", "baseline_days")
