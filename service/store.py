"""Persist GitHub deliveries and their evaluation results."""

import os
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Column, DateTime, Index, MetaData, String, Table, create_engine, select, update,
)
from sqlalchemy.exc import IntegrityError

from demo.release_gate import run_suite

from .evaluate_builds import BuildTargets, evaluate_configured_builds

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
    Column("created_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Index("ix_webhook_deliveries_project_history", "repository_id", "installation_id", "id"),
)


def database():
    return create_engine(os.environ["RELEASETRACE_DATABASE_URL"])


def enqueue(engine, delivery_id, repository, installation, sha, inputs):
    try:
        with engine.begin() as connection:
            connection.execute(deliveries.insert().values(
                id=delivery_id, repository_id=repository, installation_id=installation,
                commit_sha=sha, state="queued", inputs=inputs,
                created_at=datetime.now(timezone.utc),
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
    # Keep the claim and result in one transaction. A worker crash rolls
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
        fixture, suite = job.inputs["fixture"], job.inputs["suite"]
        if job.inputs.get("evaluation_mode") == "build_endpoints":
            targets = BuildTargets.model_validate(job.inputs["build_targets"])
            report = evaluate_configured_builds(targets, fixture, suite)
        else:
            report = run_suite(fixture, suite)
        connection.execute(update(deliveries).where(deliveries.c.id == job.id).values(
            state="completed", report=report, completed_at=datetime.now(timezone.utc),
        ))
        return job.id


if __name__ == "__main__":
    from .schema import upgrade_database

    engine = database()
    try:
        upgrade_database(engine)
    finally:
        engine.dispose()
