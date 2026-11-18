"""Track successful GitHub checks and scheduled retries."""

import sqlalchemy as sa
from alembic import op

revision = "0003_github_check_delivery"
down_revision = "0002_delivery_timestamps"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("webhook_deliveries", sa.Column("github_check_id", sa.BigInteger()))
    op.add_column("webhook_deliveries", sa.Column("github_check_retry_at", sa.DateTime(timezone=True)))


def downgrade():
    with op.batch_alter_table("webhook_deliveries") as batch:
        batch.drop_column("github_check_retry_at")
        batch.drop_column("github_check_id")
