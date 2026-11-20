"""Persist protected decisions for evaluated builds."""

import sqlalchemy as sa
from alembic import op

revision = "0005_approvals"
down_revision = "0004_investigations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "approvals",
        sa.Column("run_id", sa.String(128), sa.ForeignKey("webhook_deliveries.id"), primary_key=True),
        sa.Column("image_digest", sa.String(71), nullable=False),
        sa.Column("decision", sa.String(8), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("approvals")
