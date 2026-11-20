"""Store the current viewer request for each account and repository."""
import sqlalchemy as sa
from alembic import op

revision = "0010_access_requests"
down_revision = "0009_browser_sessions"
branch_labels = depends_on = None


def upgrade():
    op.create_table("access_requests",
        sa.Column("handle", sa.String(64), sa.ForeignKey("accounts.handle"), primary_key=True),
        sa.Column("repository_id", sa.BigInteger(), primary_key=True),
        sa.Column("id", sa.String(36), unique=True, nullable=False),
        sa.Column("installation_id", sa.BigInteger()), sa.Column("organization", sa.String(63)),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_access_requests_queue", "access_requests", ["status", "updated_at"])


def downgrade():
    op.drop_table("access_requests")
