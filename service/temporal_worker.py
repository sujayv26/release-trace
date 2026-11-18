"""Dispatch persisted release evaluations to a Temporal worker."""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.service import RPCError
from temporalio.worker import Worker

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import select

    from .store import database, deliveries, process_one

logger = logging.getLogger(__name__)
TASK_QUEUE = "releasetrace-evaluations"
ACTIVITY_NAME = "evaluate-release"


def evaluation_workflow_id(task_queue, delivery_id):
    return f"release-evaluation/{task_queue}/{delivery_id}"


@workflow.defn
class EvaluateRelease:
    @workflow.run
    async def run(self, delivery_id: str) -> None:
        await workflow.execute_activity(
            ACTIVITY_NAME, delivery_id,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), maximum_interval=timedelta(minutes=1)),
        )


class EvaluationActivities:
    def __init__(self, engine):
        self.engine = engine

    @activity.defn(name=ACTIVITY_NAME)
    def evaluate(self, delivery_id: str) -> None:
        try:
            if process_one(self.engine, delivery_id) is not None:
                return
            with self.engine.connect() as connection:
                state = connection.scalar(select(deliveries.c.state).where(deliveries.c.id == delivery_id))
            if state != "completed":
                raise RuntimeError("Evaluation has no committed result")
        except Exception as error:
            logger.warning("Evaluation activity failed for %s (%s)", delivery_id, type(error).__name__)
            raise ApplicationError("Evaluation has no committed result") from None


def queued_page(engine, after):
    query = select(deliveries.c.id).where(deliveries.c.state == "queued").order_by(deliveries.c.id).limit(100)
    if after is not None:
        query = query.where(deliveries.c.id > after)
    with engine.connect() as connection:
        return connection.scalars(query).all()


async def dispatch_pending(client, engine, task_queue):
    after = None
    while delivery_ids := await asyncio.to_thread(queued_page, engine, after):
        for delivery_id in delivery_ids:
            try:
                await client.start_workflow(
                    EvaluateRelease.run, delivery_id, id=evaluation_workflow_id(task_queue, delivery_id),
                    task_queue=task_queue, id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
                    rpc_timeout=timedelta(seconds=5),
                )
            except WorkflowAlreadyStartedError:
                pass
        after = delivery_ids[-1]


async def run():
    address = os.getenv("RELEASETRACE_TEMPORAL_ADDRESS", "127.0.0.1:7233")
    namespace = os.getenv("RELEASETRACE_TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("RELEASETRACE_TEMPORAL_TASK_QUEUE", TASK_QUEUE)
    client = await Client.connect(address, namespace=namespace)
    engine = database()
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            async with Worker(
                client, task_queue=task_queue, workflows=[EvaluateRelease],
                activities=[EvaluationActivities(engine).evaluate], activity_executor=executor,
                max_concurrent_activities=4,
            ):
                while True:
                    try:
                        await dispatch_pending(client, engine, task_queue)
                    except RPCError as error:
                        logger.warning("Temporal dispatch unavailable (%s)", error.status.name)
                    await asyncio.sleep(2)
    finally:
        engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
