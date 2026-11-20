"""Store expiring browser sessions separately from reviewer API keys."""

import sqlalchemy as sa
from alembic import op

revision = "0009_browser_sessions"
down_revision = "0008_account_access_events"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "browser_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("credential_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_browser_sessions_expires_at", "browser_sessions", ["expires_at"])


def downgrade():
    op.drop_table("browser_sessions")
