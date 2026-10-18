"""mfa: native TOTP for local accounts (encrypted secret, replay guard, hashed recovery codes)

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("totp_secret", postgresql.JSONB()))
    op.add_column("users", sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("totp_confirmed_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger()))
    op.add_column(
        "users",
        sa.Column("mfa_recovery", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    for c in ("mfa_recovery", "totp_last_step", "totp_confirmed_at", "totp_enabled", "totp_secret"):
        op.drop_column("users", c)
