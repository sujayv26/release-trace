"""Store investigations for completed evaluation cases."""

import sqlalchemy as sa
from alembic import op

revision = "0004_investigations"
down_revision = "0003_github_check_delivery"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "investigations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(128), sa.ForeignKey("webhook_deliveries.id"), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("report", sa.JSON()),
    )
    op.create_index("ix_investigations_state", "investigations", ["state"])


def downgrade():
    op.drop_table("investigations")
