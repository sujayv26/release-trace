"""Persist GitHub deliveries and their evaluation results."""

import os
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, BigInteger, Column, DateTime, ForeignKey, Index, Integer, MetaData, String, Table, create_engine, select, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url

from demo.release_gate import run_suite

from .evaluate_builds import BuildTargets, evaluate_configured_builds
from .evaluation_config import EvaluationSnapshot

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
    Column("github_check_id", BigInteger),
    Column("github_check_retry_at", DateTime(timezone=True)),
    Index("ix_webhook_deliveries_project_history", "repository_id", "installation_id", "id"),
)
investigations = Table(
    "investigations",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("run_id", ForeignKey("webhook_deliveries.id"), nullable=False),
    Column("case_id", String(128), nullable=False),
    Column("state", String(16), nullable=False, index=True),
    Column("report", JSON),
)
approvals = Table(
    "approvals",
    metadata,
    Column("run_id", ForeignKey("webhook_deliveries.id"), primary_key=True),
    Column("image_digest", String(71), nullable=False),
    Column("decision", String(8), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
deployments = Table(
    "deployments",
    metadata,
    Column("run_id", ForeignKey("approvals.run_id"), primary_key=True),
    Column("rollout_uid", String(36), primary_key=True),
    Column("generation", BigInteger, primary_key=True),
    Column("outcome", String(8), primary_key=True),
    Column("namespace", String(63), nullable=False),
    Column("rollout", String(253), nullable=False),
    Column("image_digest", String(71), nullable=False),
    Column("stable_image_digest", String(71), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Index("ix_deployments_run_recorded", "run_id", "recorded_at"),
)
accounts = Table(
    "accounts",
    metadata,
    Column("handle", String(64), primary_key=True),
    Column("key_hash", String(64), unique=True),
)
browser_sessions = Table(
    "browser_sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("credential_hash", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
)
project_owners = Table(
    "project_owners",
    metadata,
    Column("repository_id", BigInteger, primary_key=True, autoincrement=False),
    Column("organization", String(63), nullable=False),
)
project_memberships = Table(
    "project_memberships",
    metadata,
    Column("handle", ForeignKey("accounts.handle"), primary_key=True),
    Column("repository_id", BigInteger, primary_key=True),
    Column("installation_id", BigInteger, primary_key=True),
    Column("organization", String(63), primary_key=True),
    Column("role", String(10), nullable=False),
)
account_access_events = Table(
    "account_access_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("action", String(8), nullable=False),
    Column("handle", String(64), nullable=False),
    Column("operator", String(64)),
    Column("repository_id", BigInteger),
    Column("installation_id", BigInteger),
    Column("organization", String(63)),
    Column("role", String(10)),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Index("ix_account_access_events_handle", "handle", "id"),
    Index("ix_account_access_events_repository", "repository_id", "id"),
)


def database():
    url = make_url(os.environ["RELEASETRACE_DATABASE_URL"])
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg")
    return create_engine(url)


def as_utc(value: datetime | None) -> datetime | None:
    # SQLite drops offsets on storage, while PostgreSQL returns aware values.
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


def process_one(engine, delivery_id=None):
    # Keep the claim and result in one transaction. A worker crash rolls
    # back the claim, so the next worker can process the same delivery.
    with engine.begin() as connection:
        query = select(deliveries).where(deliveries.c.state == "queued")
        if delivery_id is not None:
            query = query.where(deliveries.c.id == delivery_id)
        job = connection.execute(
            query.order_by(deliveries.c.id).limit(1).with_for_update(skip_locked=delivery_id is None)
        ).one_or_none()
        if job is None:
            return None
        claimed = connection.execute(update(deliveries).where(
            deliveries.c.id == job.id, deliveries.c.state == "queued",
        ).values(state="running"))
        if claimed.rowcount != 1:
            return None
        inputs = job.inputs
        if inputs.get("evaluation_mode") == "build_endpoints":
            targets = BuildTargets.model_validate(inputs["build_targets"])
            options = {"repository_id": job.repository_id} if inputs.get("project_scoped_tokens") else {}
            if "project_evaluation" in inputs:
                options["project_evaluation"] = EvaluationSnapshot.model_validate(inputs["project_evaluation"])
            report = evaluate_configured_builds(targets, inputs.get("fixture"), inputs.get("suite"), **options)
        else:
            report = run_suite(inputs["fixture"], inputs["suite"])
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
