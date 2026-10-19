"""backup & restore: storage targets, policies, asynchronous jobs and backup records

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
JSON = postgresql.JSONB()


def _obj() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "backup_targets",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("config", JSON, nullable=False, server_default=_obj()),
        sa.Column("secret", JSON),
        sa.Column("last_check_at", TS),
        sa.Column("last_check_ok", sa.Boolean()),
        sa.Column("last_check_error", sa.Text()),
        sa.Column("created_by", sa.String(128)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "backup_policies",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("scope", JSON, nullable=False, server_default=_obj()),
        sa.Column("target_id", sa.UUID(), sa.ForeignKey("backup_targets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("schedule", JSON, nullable=False, server_default=_obj()),
        sa.Column("encryption", JSON, nullable=False, server_default=_obj()),
        sa.Column("retention", JSON, nullable=False, server_default=_obj()),
        sa.Column("channel_ids", JSON, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("max_age_hours", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_run_at", TS),
        sa.Column("next_run_at", TS),
        sa.Column("last_status", sa.String(16)),
        sa.Column("overdue_notified_at", TS),
        sa.Column("created_by", sa.String(128)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "backup_jobs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("trigger", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("policy_id", sa.UUID(), sa.ForeignKey("backup_policies.id", ondelete="SET NULL")),
        sa.Column("target_id", sa.UUID(), sa.ForeignKey("backup_targets.id", ondelete="SET NULL")),
        sa.Column("params", JSON, nullable=False, server_default=_obj()),
        sa.Column("progress", JSON, nullable=False, server_default=_obj()),
        sa.Column("result", JSON),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text()),
        sa.Column("requested_by", sa.String(128)),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("locked_by", sa.String(128)),
        sa.Column("heartbeat_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
    )
    op.create_index("ix_backup_jobs_status", "backup_jobs", ["status", "created_at"])
    op.create_index("ix_backup_jobs_policy", "backup_jobs", ["policy_id", "created_at"])
    op.create_table(
        "backups",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="CREATING"),
        sa.Column("job_id", sa.UUID(), sa.ForeignKey("backup_jobs.id", ondelete="SET NULL")),
        sa.Column("policy_id", sa.UUID(), sa.ForeignKey("backup_policies.id", ondelete="SET NULL")),
        sa.Column("target_id", sa.UUID(), sa.ForeignKey("backup_targets.id", ondelete="SET NULL")),
        sa.Column("server_id", sa.UUID(), sa.ForeignKey("servers.id", ondelete="SET NULL")),
        sa.Column("server_name", sa.String(255)),
        sa.Column("engine", sa.String(32)),
        sa.Column("database_name", sa.String(255)),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("tool", sa.String(128)),
        sa.Column("location", sa.Text()),
        sa.Column("location_kind", sa.String(16), nullable=False, server_default="target"),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("sha256", sa.String(64)),
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("encryption", JSON, nullable=False, server_default=_obj()),
        sa.Column("app_version", sa.String(32), nullable=False),
        sa.Column("schema_version", sa.String(32)),
        sa.Column("format_version", sa.String(32), nullable=False),
        sa.Column("manifest", JSON, nullable=False, server_default=_obj()),
        sa.Column("verification", JSON),
        sa.Column("verified_at", TS),
        sa.Column("error", sa.Text()),
        sa.Column("created_by", sa.String(128)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", TS),
        sa.Column("deleted_at", TS),
        sa.Column("deleted_by", sa.String(128)),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_backups_created", "backups", ["created_at"])
    op.create_index("ix_backups_policy", "backups", ["policy_id", "created_at"])
    op.create_index("ix_backups_server", "backups", ["server_id", "created_at"])


def downgrade() -> None:
    op.drop_table("backups")
    op.drop_table("backup_jobs")
    op.drop_table("backup_policies")
    op.drop_table("backup_targets")
