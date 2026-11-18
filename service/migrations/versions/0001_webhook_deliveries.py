"""Create the delivery queue or adopt the original local schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_webhook_deliveries"
down_revision = None
branch_labels = None
depends_on = None

TABLE = "webhook_deliveries"


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(TABLE):
        columns = {column["name"] for column in inspector.get_columns(TABLE)}
        expected = {"id", "repository_id", "installation_id", "commit_sha", "state", "inputs", "report"}
        if columns != expected or inspector.get_pk_constraint(TABLE)["constrained_columns"] != ["id"]:
            raise RuntimeError("Existing webhook_deliveries table does not match the original ReleaseTrace schema")
        if not any(index["name"] == "ix_webhook_deliveries_state" for index in inspector.get_indexes(TABLE)):
            op.create_index("ix_webhook_deliveries_state", TABLE, ["state"])
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON()),
    )
    op.create_index("ix_webhook_deliveries_state", TABLE, ["state"])


def downgrade():
    op.drop_table(TABLE)
