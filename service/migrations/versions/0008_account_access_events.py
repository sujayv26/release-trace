"""Record managed account and project access commands."""

import sqlalchemy as sa
from alembic import op

revision = "0008_account_access_events"
down_revision = "0007_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "account_access_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("handle", sa.String(64), nullable=False),
        sa.Column("operator", sa.String(64)),
        sa.Column("repository_id", sa.BigInteger()),
        sa.Column("installation_id", sa.BigInteger()),
        sa.Column("organization", sa.String(63)),
        sa.Column("role", sa.String(10)),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_account_access_events_handle", "account_access_events", ["handle", "id"])
    op.create_index("ix_account_access_events_repository", "account_access_events", ["repository_id", "id"])


def downgrade():
    op.drop_table("account_access_events")
