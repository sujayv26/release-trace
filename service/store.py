"""Persist GitHub deliveries and their fixture evaluation results."""

import os

from sqlalchemy import JSON, BigInteger, Column, MetaData, String, Table, create_engine, select, update
from sqlalchemy.exc import IntegrityError

from demo.release_gate import run_suite

metadata = MetaData()
deliveries = Table(
    "webhook_deliveries",
    metadata,
    Column("id", String(128), primary_key=True),
    Column("repository_id", BigInteger, nullable=False),
    Column("installation_id", BigInteger, nullable=False),
    Column("commit_sha", String(40), nullable=False),
    Column("state", String(16), nullable=False, index=True),
    Column("inputs", JSON, nullable=False),
    Column("report", JSON),
)


def database():
    return create_engine(os.environ["RELEASETRACE_DATABASE_URL"])


def enqueue(engine, delivery_id, repository, installation, sha, inputs):
    try:
        with engine.begin() as connection:
            connection.execute(deliveries.insert().values(
                id=delivery_id, repository_id=repository, installation_id=installation,
                commit_sha=sha, state="queued", inputs=inputs,
            ))
    except IntegrityError:
        with engine.connect() as connection:
            existing = connection.execute(select(
                deliveries.c.repository_id, deliveries.c.installation_id, deliveries.c.commit_sha,
            ).where(
                deliveries.c.id == delivery_id,
            )).one_or_none()
        if existing is None:
            raise
        return "duplicate" if existing == (repository, installation, sha) else "conflict"
    return "queued"


def process_one(engine):
    # Keep the claim and fixture result in one transaction. A worker crash rolls
    # back the claim, so the next worker can process the same delivery.
    with engine.begin() as connection:
        job = connection.execute(select(deliveries).where(
            deliveries.c.state == "queued",
        ).order_by(deliveries.c.id).limit(1).with_for_update(skip_locked=True)).one_or_none()
        if job is None:
            return None
        claimed = connection.execute(update(deliveries).where(
            deliveries.c.id == job.id, deliveries.c.state == "queued",
        ).values(state="running"))
        if claimed.rowcount != 1:
            return None
        report = run_suite(job.inputs["fixture"], job.inputs["suite"])
        connection.execute(update(deliveries).where(deliveries.c.id == job.id).values(
            state="completed", report=report,
        ))
        return job.id


if __name__ == "__main__":
    metadata.create_all(database())
