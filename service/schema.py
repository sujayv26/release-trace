"""Apply ReleaseTrace database migrations using an existing engine."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade_database(engine):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
