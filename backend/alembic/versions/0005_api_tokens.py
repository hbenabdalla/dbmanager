"""api tokens: personal, hashed, expiring, scoped API tokens

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("scopes", postgresql.JSONB()),
        sa.Column("allowed_cidrs", postgresql.JSONB()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_used_at", TS),
        sa.Column("last_used_ip", sa.String(64)),
        sa.Column("revoked_at", TS),
        sa.Column("created_by", sa.String(128)),
        sa.UniqueConstraint("prefix", name="uq_api_tokens_prefix"),
    )
    op.create_index("ix_api_tokens_user", "api_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_api_tokens_user", table_name="api_tokens")
    op.drop_table("api_tokens")
