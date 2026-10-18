"""time-series storage: query fingerprint metadata

Monitoring telemetry moves to the time-series database. PostgreSQL keeps only the metadata
of normalized statements; the legacy ``metric_samples`` and ``query_stat_snapshots`` tables
are intentionally kept (read by the migration utility, pruned by retention) and will be
dropped by a later migration once the migration has been verified (phase F).

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "query_fingerprints",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("server_id", sa.UUID(), nullable=False),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("database", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("engine", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=256), nullable=True),
        sa.Column("normalized_query", sa.Text(), nullable=False),
        sa.Column("engine_query_id", sa.String(length=64), nullable=True),
        sa.Column("data_source", sa.String(length=16), nullable=False, server_default="real"),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_query_fingerprints_server_id_servers"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_query_fingerprints")),
        sa.UniqueConstraint("server_id", "fingerprint", "database", name="uq_query_fingerprints_server_fp_db"),
    )
    op.create_index("ix_query_fingerprints_server_last_seen", "query_fingerprints", ["server_id", "last_seen"])


def downgrade() -> None:
    op.drop_index("ix_query_fingerprints_server_last_seen", table_name="query_fingerprints")
    op.drop_table("query_fingerprints")
