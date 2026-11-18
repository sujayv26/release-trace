"""Run the recovery drill against a local Temporal test server."""

import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from .evaluate_builds import FIXTURES
from .schema import upgrade_database
from .store import deliveries, enqueue
from .temporal_worker import (
    ACTIVITY_NAME, EvaluateRelease, EvaluationActivities, dispatch_pending, evaluation_workflow_id,
)


class TemporalRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_worker_replacement_after_commit_preserves_result(self):
        with tempfile.TemporaryDirectory() as directory, ThreadPoolExecutor() as executor:
            db_path = Path(directory, "release.db").as_posix()
            engine = create_engine(f"sqlite:///{db_path}")
            try:
                upgrade_database(engine)
                inputs = {key: json.loads((FIXTURES / name).read_bytes()) for key, name in (
                    ("fixture", "fixture.json"), ("suite", "gate_suite.json"),
                )}
                enqueue(engine, "recover-1", 1, 2, "a" * 40, inputs)
                before_ack = asyncio.Event()
                retry_attempts = []
                loop = asyncio.get_running_loop()
                evaluate = EvaluationActivities(engine).evaluate

                @activity.defn(name=ACTIVITY_NAME)
                def lose_ack(delivery_id: str) -> None:
                    evaluate(delivery_id)
                    loop.call_soon_threadsafe(before_ack.set)
                    raise ApplicationError("Simulated lost acknowledgement")

                @activity.defn(name=ACTIVITY_NAME)
                def retry(delivery_id: str) -> None:
                    retry_attempts.append(activity.info().attempt)
                    evaluate(delivery_id)

                queue = f"recovery-test-{uuid4()}"
                async with await WorkflowEnvironment.start_time_skipping() as environment:
                    client = environment.client
                    worker_options = {
                        "task_queue": queue, "workflows": [EvaluateRelease], "activity_executor": executor,
                    }
                    async with Worker(client, activities=[lose_ack], **worker_options):
                        await dispatch_pending(client, engine, queue)
                        await dispatch_pending(client, engine, queue)
                        await asyncio.wait_for(before_ack.wait(), timeout=20)
                    with engine.connect() as connection:
                        original = connection.execute(select(deliveries)).one()
                    self.assertEqual(original.state, "completed")
                    self.assertEqual(original.report["candidate"]["verdict"], "block")
                    async with Worker(client, activities=[retry], **worker_options):
                        handle = client.get_workflow_handle(evaluation_workflow_id(queue, "recover-1"))
                        await asyncio.wait_for(handle.result(), timeout=20)
                    self.assertEqual(len(retry_attempts), 1)
                    self.assertGreater(retry_attempts[0], 1)
                    with engine.connect() as connection:
                        self.assertEqual(connection.execute(select(deliveries)).one(), original)
            finally:
                engine.dispose()
