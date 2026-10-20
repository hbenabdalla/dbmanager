"""platform_instances: running API / worker processes and the scheduler leader (0.15.4)

Every process heartbeats its row, so any API replica can show the whole deployment.

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_instances",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("hostname", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("git_commit", sa.String(40)),
        sa.Column("runs_worker", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_leader", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("platform_instances")
