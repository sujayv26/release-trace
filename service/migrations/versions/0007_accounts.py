"""Persist reviewer accounts and scoped project memberships."""

import sqlalchemy as sa
from alembic import op

revision = "0007_accounts"
down_revision = "0006_deployments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("handle", sa.String(64), primary_key=True),
        sa.Column("key_hash", sa.String(64), unique=True),
    )
    op.create_table(
        "project_owners",
        sa.Column("repository_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("organization", sa.String(63), nullable=False),
    )
    op.create_table(
        "project_memberships",
        sa.Column("handle", sa.String(64), sa.ForeignKey("accounts.handle"), primary_key=True),
        sa.Column("repository_id", sa.BigInteger(), primary_key=True),
        sa.Column("installation_id", sa.BigInteger(), primary_key=True),
        sa.Column("organization", sa.String(63), primary_key=True),
        sa.Column("role", sa.String(10), nullable=False),
    )


def downgrade():
    op.drop_table("project_memberships")
    op.drop_table("project_owners")
    op.drop_table("accounts")
