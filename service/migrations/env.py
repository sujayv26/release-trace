"""Run migrations against the configured ReleaseTrace database."""

from alembic import context

from service.store import database, metadata


def migrate(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    migrate(connection)
else:
    engine = database()
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
