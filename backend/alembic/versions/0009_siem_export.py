"""siem export: audit event forwarding destinations (syslog RFC 5424 / webhook) with a cursor

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "siem_destinations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("secret", postgresql.JSONB()),
        sa.Column("categories", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("cursor_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("exported_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_success_at", TS),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_error_at", TS),
        sa.Column("created_by", sa.String(128)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("siem_destinations")
