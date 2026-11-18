"""Accept signed GitHub pushes for one configured fixture project."""

import hmac
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from .store import database, deliveries, enqueue

MAX_BODY = 1024 * 1024
DELIVERY_ID = re.compile(r"[A-Za-z0-9-]{1,128}")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
logger = logging.getLogger(__name__)


class GitHubIdentity(BaseModel):
    id: StrictInt


class Push(BaseModel):
    after: str
    ref: str
    deleted: bool
    repository: GitHubIdentity
    installation: GitHubIdentity


class Accepted(BaseModel):
    status: Literal["queued", "duplicate", "ignored"]
    delivery_id: str


Verdict = Literal["pass", "block", "inconclusive"]
Outcome = Literal["pass", "fail", "inconclusive"]
PageSize = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(min_length=1, max_length=128)]


class RunSummary(BaseModel):
    id: str
    commit_sha: str
    state: str
    baseline_verdict: Verdict | None
    candidate_verdict: Verdict | None
    created_at: datetime | None
    evaluation_mode: Literal["synthetic_fixture"] = "synthetic_fixture"


class RunPage(BaseModel):
    items: list[RunSummary]
    next_cursor: str | None


class PolicyEvidence(BaseModel):
    id: str
    revision: int
    text: str


class CaseVariant(BaseModel):
    config: dict[str, str]
    config_digest: str
    citation: str | None
    evidence: PolicyEvidence | None
    outcome: Outcome


class CaseComparison(BaseModel):
    case_id: str
    question: str
    required_citation: str
    critical: bool
    baseline: CaseVariant
    candidate: CaseVariant


class CasePage(BaseModel):
    state: str
    items: list[CaseComparison]
    next_cursor: str | None


class ReadError(Exception):
    def __init__(self, status, code, message):
        self.status = status
        self.code = code
        super().__init__(message)


def error(status, code, message, request_id):
    return JSONResponse(status_code=status, content={
        "code": code, "message": message, "request_id": request_id,
    })


def as_utc(value: datetime | None) -> datetime | None:
    # SQLite drops offsets on storage, while PostgreSQL returns aware values.
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def create_app():
    secret = os.environ["RELEASETRACE_WEBHOOK_SECRET"].encode()
    token = os.environ["RELEASETRACE_READ_TOKEN"].encode()
    repository = int(os.environ["RELEASETRACE_REPOSITORY_ID"])
    installation = int(os.environ["RELEASETRACE_INSTALLATION_ID"])
    if not secret or not token or repository <= 0 or installation <= 0:
        raise ValueError("Webhook secret, read token and positive GitHub IDs are required")
    engine = database()
    demo = Path(__file__).resolve().parents[1] / "demo"
    inputs = {
        "fixture": json.loads((demo / "fixture.json").read_text(encoding="utf-8")),
        "suite": json.loads((demo / "gate_suite.json").read_text(encoding="utf-8")),
    }
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(lifespan=lifespan)
    scope = (
        deliveries.c.repository_id == repository,
        deliveries.c.installation_id == installation,
    )

    def require_read(request: Request):
        provided = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(provided, b"Bearer " + token):
            raise ReadError(401, "unauthorized", "A project read token is required")

    def load_run(delivery_id, *columns):
        with engine.connect() as connection:
            result = connection.execute(select(*columns).where(
                *scope, deliveries.c.id == delivery_id,
            )).mappings().one_or_none()
        if result is None:
            raise ReadError(404, "run_missing", "Run was not found")
        return result

    @app.exception_handler(ReadError)
    async def read_error(request, exc):
        return error(exc.status, exc.code, str(exc), str(uuid4()))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return error(422, "invalid_request", "Invalid request parameters", str(uuid4()))

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(request, exc):
        request_id = str(uuid4())
        logger.error("Storage failure %s (%s)", request_id, type(exc).__name__)
        return error(503, "storage_unavailable", "Storage is unavailable", request_id)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/readyz")
    def readiness():
        with engine.connect() as connection:
            connection.execute(select(deliveries.c.created_at, deliveries.c.completed_at).limit(1))
        return {"status": "ready"}

    @app.post("/webhooks/github", response_model=Accepted, status_code=202)
    async def github(request: Request):
        request_id = str(uuid4())
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY:
                return error(413, "payload_too_large", "Webhook exceeds the size limit", request_id)
            body.extend(chunk)
        signature = b"sha256=" + hmac.digest(secret, body, "sha256").hex().encode()
        if not hmac.compare_digest(signature, request.headers.get("x-hub-signature-256", "").encode()):
            return error(401, "invalid_signature", "Invalid webhook signature", request_id)
        delivery_id = request.headers.get("x-github-delivery", "")
        if not DELIVERY_ID.fullmatch(delivery_id):
            return error(400, "invalid_delivery", "Invalid delivery ID", request_id)
        if request.headers.get("x-github-event") != "push":
            return Accepted(status="ignored", delivery_id=delivery_id)
        try:
            push = Push.model_validate_json(body)
        except ValidationError:
            return error(400, "invalid_push", "Invalid GitHub push payload", request_id)
        if (push.repository.id, push.installation.id) != (repository, installation):
            return error(403, "project_mismatch", "Webhook is outside the configured project", request_id)
        if push.deleted or not push.ref.startswith("refs/heads/"):
            return Accepted(status="ignored", delivery_id=delivery_id)
        if not COMMIT_SHA.fullmatch(push.after) or push.after == "0" * 40:
            return error(400, "invalid_commit", "Invalid GitHub commit SHA", request_id)
        status = await run_in_threadpool(
            enqueue, engine, delivery_id, repository, installation, push.after, inputs,
        )
        if status == "conflict":
            return error(409, "delivery_conflict", "Delivery ID already exists", request_id)
        return Accepted(status=status, delivery_id=delivery_id)

    @app.get("/v1/projects/{project_id}/runs", dependencies=[Depends(require_read)])
    def run_history(project_id: int, limit: PageSize = 25, after: Cursor = None) -> RunPage:
        if project_id != repository:
            raise ReadError(404, "project_missing", "Project was not found")
        query = select(
            deliveries.c.id, deliveries.c.commit_sha, deliveries.c.state, deliveries.c.created_at,
            deliveries.c.report["baseline"]["verdict"].as_string().label("baseline_verdict"),
            deliveries.c.report["candidate"]["verdict"].as_string().label("candidate_verdict"),
        ).where(*scope)
        if after is not None:
            if not DELIVERY_ID.fullmatch(after):
                raise ReadError(400, "invalid_cursor", "Invalid run cursor")
            query = query.where(deliveries.c.id > after)
        with engine.connect() as connection:
            rows = connection.execute(query.order_by(deliveries.c.id).limit(limit + 1)).mappings().all()
        items = [RunSummary(**{**row, "created_at": as_utc(row["created_at"])}) for row in rows[:limit]]
        return RunPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)

    @app.get("/v1/runs/{delivery_id}", dependencies=[Depends(require_read)])
    def run_detail(delivery_id: str):
        result = load_run(
            delivery_id, deliveries.c.id, deliveries.c.commit_sha, deliveries.c.state, deliveries.c.report,
            deliveries.c.created_at, deliveries.c.completed_at,
        )
        return {
            **result,
            "created_at": as_utc(result["created_at"]),
            "completed_at": as_utc(result["completed_at"]),
            "evaluation_mode": "synthetic_fixture",
        }

    @app.get("/v1/runs/{delivery_id}/cases", dependencies=[Depends(require_read)])
    def run_cases(
        delivery_id: str, limit: PageSize = 25, after: Cursor = None,
        outcome: Annotated[Outcome | None, Query()] = None,
    ) -> CasePage:
        run = load_run(delivery_id, deliveries.c.state, deliveries.c.inputs, deliveries.c.report)
        report = run["report"]
        if report is None:
            return CasePage(state=run["state"], items=[], next_cursor=None)
        cases = [run["inputs"]["fixture"], *run["inputs"]["suite"]["cases"]]
        baseline = report["baseline"]["results"]
        candidate = report["candidate"]["results"]
        items = []
        for case in sorted(cases, key=lambda item: item["case_id"]):
            case_id = case["case_id"]
            if after is not None and case_id <= after:
                continue
            if outcome is not None and candidate[case_id]["outcome"] != outcome:
                continue
            items.append(CaseComparison(
                case_id=case_id, question=case["question"],
                required_citation=case["required_citation"], critical=case["critical"],
                baseline=baseline[case_id], candidate=candidate[case_id],
            ))
            if len(items) > limit:
                return CasePage(state=run["state"], items=items[:limit], next_cursor=items[-2].case_id)
        return CasePage(state=run["state"], items=items, next_cursor=None)

    return app
