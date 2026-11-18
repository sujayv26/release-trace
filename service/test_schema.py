import unittest

from sqlalchemy import create_engine, inspect, select

from .schema import upgrade_database
from .store import deliveries


class SchemaTest(unittest.TestCase):
    def test_adopts_existing_database_and_preserves_completed_report(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE webhook_deliveries (
                    id VARCHAR(128) PRIMARY KEY,
                    repository_id BIGINT NOT NULL,
                    installation_id BIGINT NOT NULL,
                    commit_sha VARCHAR(40) NOT NULL,
                    state VARCHAR(16) NOT NULL,
                    inputs JSON NOT NULL,
                    report JSON
                )
            """)
            connection.exec_driver_sql("""
                INSERT INTO webhook_deliveries VALUES ('original', 1, 2, ?, 'completed', '{}', ?)
            """, ("a" * 40, '{"candidate":{"verdict":"block"}}'))

        upgrade_database(engine)
        upgrade_database(engine)

        with engine.connect() as connection:
            old_run = connection.execute(select(deliveries)).one()
            self.assertEqual(old_run.id, "original")
            self.assertEqual(old_run.report, {"candidate": {"verdict": "block"}})
            self.assertIsNone(old_run.created_at)
            self.assertIsNone(old_run.completed_at)
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
            self.assertEqual(version, "0002_delivery_timestamps")
            indexes = {index["name"] for index in inspect(connection).get_indexes(deliveries.name)}
            self.assertEqual(indexes, {
                "ix_webhook_deliveries_state", "ix_webhook_deliveries_project_history",
            })
