"""data_source classification and simulation runs

Introduces ``data_source`` (real | simulated | system) on inventory tables, replaces the
boolean ``servers.is_demo`` with it, and adds ``simulation_runs``.

Existing data is classified, never deleted:

* servers: ``is_demo = true`` -> ``simulated`` (the flag was only ever set by the demo seeder)
* sites / clusters / applications: ``simulated`` only when at least one demo server uses
  them and no other server does (the legacy seeder created them for its fleet). Anything a
  real server references stays ``real``.
* the five default environments created by bootstrap -> ``system``
* when demo servers exist, a ``legacy`` simulation run is recorded so the administration
  page can show where they came from.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ("environments", "sites", "clusters", "applications", "servers")
DEFAULT_ENVIRONMENTS = ("Production", "DR", "UAT", "Development", "Test")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table, sa.Column("data_source", sa.String(length=16), server_default="real", nullable=False)
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_{table}_data_source"), table, "data_source IN ('real','simulated','system')"
        )
    op.create_index(op.f("ix_servers_data_source"), "servers", ["data_source"])

    op.execute("UPDATE servers SET data_source = 'simulated' WHERE is_demo IS TRUE")
    for table, column in (("sites", "site_id"), ("clusters", "cluster_id"), ("applications", "application_id")):
        op.execute(
            f"""
            UPDATE {table} t SET data_source = 'simulated'
            WHERE EXISTS (SELECT 1 FROM servers s WHERE s.{column} = t.id AND s.is_demo IS TRUE)
              AND NOT EXISTS (SELECT 1 FROM servers s WHERE s.{column} = t.id AND s.is_demo IS NOT TRUE)
            """
        )
    names = ", ".join(f"'{n}'" for n in DEFAULT_ENVIRONMENTS)
    op.execute(f"UPDATE environments SET data_source = 'system' WHERE name IN ({names})")

    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("profile", sa.String(length=32), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=128), nullable=True),
        sa.Column("requested_by_id", sa.UUID(), nullable=True),
        sa.Column("requested_by", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_simulation_runs_requested_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulation_runs")),
    )
    op.create_index(op.f("ix_simulation_runs_created"), "simulation_runs", ["created_at"])
    op.execute(
        """
        INSERT INTO simulation_runs (id, operation, profile, status, progress, phase, requested_by, stats,
                                     created_at, started_at, finished_at)
        SELECT gen_random_uuid(), 'legacy', 'legacy', 'completed', 100, 'Created by DBM_DEMO_MODE at startup',
               'DBM_DEMO_MODE', jsonb_build_object('servers_created', count(*)),
               min(created_at), min(created_at), max(created_at)
        FROM servers WHERE is_demo IS TRUE
        HAVING count(*) > 0
        """
    )
    op.drop_column("servers", "is_demo")


def downgrade() -> None:
    op.add_column("servers", sa.Column("is_demo", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.execute("UPDATE servers SET is_demo = (data_source = 'simulated')")
    op.drop_index("ix_simulation_runs_created", table_name="simulation_runs")
    op.drop_table("simulation_runs")
    op.drop_index("ix_servers_data_source", table_name="servers")
    for table in TABLES:
        op.drop_constraint(op.f(f"ck_{table}_{table}_data_source"), table, type_="check")
        op.drop_column(table, "data_source")
