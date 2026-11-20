"""Persist observed deployment outcomes for approved release runs."""

import sqlalchemy as sa
from alembic import op

revision = "0006_deployments"
down_revision = "0005_approvals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deployments",
        sa.Column("run_id", sa.String(128), sa.ForeignKey("approvals.run_id"), primary_key=True),
        sa.Column("rollout_uid", sa.String(36), primary_key=True),
        sa.Column("generation", sa.BigInteger(), primary_key=True),
        sa.Column("outcome", sa.String(8), primary_key=True),
        sa.Column("namespace", sa.String(63), nullable=False),
        sa.Column("rollout", sa.String(253), nullable=False),
        sa.Column("image_digest", sa.String(71), nullable=False),
        sa.Column("stable_image_digest", sa.String(71), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deployments_run_recorded", "deployments", ["run_id", "recorded_at"])


def downgrade():
    op.drop_table("deployments")
