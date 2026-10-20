"""servers: explicit management mode for servers registered before the setting existed

Servers created before 0.10 have no ``options.management_mode`` and were treated as
*Administration enabled*. The application now treats a missing value as *Monitoring only*
(the documented default), so this migration writes ``administration`` into the rows that had
no value: existing installations keep exactly the behaviour they had (simulated servers
included: their changes are simulated, never executed).

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE servers
           SET options = COALESCE(options, '{}'::jsonb)
               || jsonb_build_object('management_mode', 'administration')
         WHERE options IS NULL OR NOT (options ? 'management_mode')
        """
    )


def downgrade() -> None:
    # the values written are the effective behaviour before this revision: nothing to undo
    pass
