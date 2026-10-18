"""rate limits: shared sliding-window counters for API, login and token-failure limits

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_windows",
        sa.Column("bucket", sa.String(200), primary_key=True),
        sa.Column("window_start", sa.BigInteger(), primary_key=True),
        sa.Column("hits", sa.Integer(), nullable=False),
    )
    op.create_index("ix_rate_limit_windows_start", "rate_limit_windows", ["window_start"])


def downgrade() -> None:
    op.drop_index("ix_rate_limit_windows_start", table_name="rate_limit_windows")
    op.drop_table("rate_limit_windows")
