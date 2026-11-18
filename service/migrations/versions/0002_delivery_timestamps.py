"""Track delivery and completion times and index project history."""

import sqlalchemy as sa
from alembic import op

revision = "0002_delivery_timestamps"
down_revision = "0001_webhook_deliveries"
branch_labels = None
depends_on = None

TABLE = "webhook_deliveries"
HISTORY_INDEX = "ix_webhook_deliveries_project_history"


def upgrade():
    op.add_column(TABLE, sa.Column("created_at", sa.DateTime(timezone=True)))
    op.add_column(TABLE, sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.create_index(HISTORY_INDEX, TABLE, ["repository_id", "installation_id", "id"])


def downgrade():
    op.drop_index(HISTORY_INDEX, table_name=TABLE)
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_column("completed_at")
        batch.drop_column("created_at")
