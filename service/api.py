"""Accept signed GitHub pushes for one configured project."""

import hmac
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictInt, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from .evaluate_builds import BuildIdentity, BuildTargets, ImageDigest
from .investigator import enqueue_investigation, investigation_key
from .store import approvals, database, deliveries, deployments, enqueue, investigations

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
EvaluationMode = Literal["synthetic_fixture", "build_endpoints"]
PageSize = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(min_length=1, max_length=128)]


class RunSummary(BaseModel):
    id: str
    commit_sha: str
    state: str
    baseline_verdict: Verdict | None
    candidate_verdict: Verdict | None
    verdict: Verdict | None
    created_at: datetime | None
    evaluation_mode: EvaluationMode


class RunPage(BaseModel):
    items: list[RunSummary]
    next_cursor: str | None


class PolicyEvidence(BaseModel):
    id: str
    revision: int
    text: str


class CaseVariant(BaseModel):
    config: dict[str, str] | None = None
    config_digest: str | None = None
    citation: str | None
    evidence: PolicyEvidence | None
    outcome: Outcome
    reason: str | None = None


class CaseComparison(BaseModel):
    case_id: str
    question: str
    required_citation: str
    critical: bool
    baseline: CaseVariant
    candidate: CaseVariant
    investigation_id: str | None = None


class CasePage(BaseModel):
    state: str
    items: list[CaseComparison]
    next_cursor: str | None


class InvestigationRequest(BaseModel):
    case_id: Annotated[str, Field(min_length=1, max_length=128)]


class ApprovalRequest(BaseModel):
    image_digest: ImageDigest
    decision: Literal["approve", "reject"]


class ApprovalDetail(ApprovalRequest):
    run_id: str
    actor: str
    created_at: datetime


class PromotionDetail(BaseModel):
    run_id: str
    baseline: BuildIdentity
    candidate: BuildIdentity


class DeploymentObservation(BaseModel):
    rollout_uid: UUID
    generation: Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
    namespace: Annotated[str, Field(max_length=63, pattern=r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")]
    rollout: Annotated[str, Field(max_length=253, pattern=r"^[a-z0-9](?:[-.a-z0-9]*[a-z0-9])?$")]
    outcome: Literal["promoted", "aborted"]
    image_digest: ImageDigest
    stable_image_digest: ImageDigest


class DeploymentDetail(DeploymentObservation):
    run_id: str
    recorded_at: datetime


class QueuedInvestigation(BaseModel):
    investigation_id: str


class ControlledExperiment(CaseVariant):
    experiment_id: str
    intervention: Literal["restore_retrieval", "restore_prompt", "replay_tool_response"]


class InvestigationReport(BaseModel):
    outcome: Literal["supported", "inconclusive"]
    reason: str
    required_citation: str
    baseline: CaseVariant
    candidate: CaseVariant
    experiment: ControlledExperiment | None
    changed_parameter: dict[str, str] | None = None
    dataset_digest: str
    policy_digest: str


class InvestigationDetail(QueuedInvestigation):
    case_id: str
    state: str
    report: InvestigationReport | None


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
    maintainer_token = os.getenv("RELEASETRACE_MAINTAINER_TOKEN", "").encode()
    approver_token = os.getenv("RELEASETRACE_APPROVER_TOKEN", "").encode()
    approver_actor = os.getenv("RELEASETRACE_APPROVER_ACTOR", "").strip()
    repository = int(os.environ["RELEASETRACE_REPOSITORY_ID"])
    installation = int(os.environ["RELEASETRACE_INSTALLATION_ID"])
    if not secret or not token or repository <= 0 or installation <= 0:
        raise ValueError("Webhook secret, read token and positive GitHub IDs are required")
    if maintainer_token and hmac.compare_digest(token, maintainer_token):
        raise ValueError("Use distinct viewer and maintainer tokens")
    if approver_token and (
        not 1 <= len(approver_actor) <= 128 or
        any(hmac.compare_digest(approver_token, role_token) for role_token in (token, maintainer_token))
    ):
        raise ValueError("An approver requires an actor name and a distinct token")
    engine = database()
    demo = Path(__file__).resolve().parents[1] / "demo"
    inputs = {
        "fixture": json.loads((demo / "fixture.json").read_text(encoding="utf-8")),
        "suite": json.loads((demo / "gate_suite.json").read_text(encoding="utf-8")),
    }
    targets_path = os.getenv("RELEASETRACE_BUILD_TARGETS_FILE")
    if not targets_path and os.getenv("RELEASETRACE_EXTENDED_FIXTURES") == "1":
        extra = json.loads((demo / "investigation_cases.json").read_text(encoding="utf-8"))
        inputs["suite"]["cases"].extend(extra)
    targets = None
    if targets_path:
        try:
            targets = BuildTargets.model_validate_json(Path(targets_path).read_bytes())
        except (OSError, ValidationError):
            raise ValueError("Invalid configured build target file") from None
        inputs.update(evaluation_mode="build_endpoints", build_targets=targets.model_dump(mode="json"))

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
    mode = func.coalesce(deliveries.c.inputs["evaluation_mode"].as_string(), "synthetic_fixture").label("evaluation_mode")

    def is_maintainer(request: Request):
        if not maintainer_token:
            return False
        provided = request.headers.get("authorization", "").encode()
        return hmac.compare_digest(provided, b"Bearer " + maintainer_token)

    def require_read(request: Request):
        if is_maintainer(request) or is_approver(request):
            return
        provided = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(provided, b"Bearer " + token):
            raise ReadError(401, "unauthorized", "A project read token is required")

    def require_maintainer(request: Request):
        require_read(request)
        if not is_maintainer(request):
            raise ReadError(403, "maintainer_required", "A project maintainer token is required")

    def is_approver(request: Request):
        provided = request.headers.get("authorization", "").encode()
        return bool(approver_token) and hmac.compare_digest(provided, b"Bearer " + approver_token)

    def require_approver(request: Request):
        require_read(request)
        if not is_approver(request):
            raise ReadError(403, "approver_required", "A project approver token is required")

    def is_current_candidate(run):
        report = run["report"] or {}
        return targets is not None and (
            run["commit_sha"] == targets.candidate.build.commit_sha
            and report.get("builds", {}).get("candidate") == targets.candidate.build.model_dump()
        )

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
            connection.execute(select(deliveries.c.created_at, deliveries.c.github_check_id).limit(1))
            connection.execute(select(investigations.c.id).limit(1))
            connection.execute(select(approvals.c.run_id).limit(1))
            connection.execute(select(deployments.c.run_id).limit(1))
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
        if targets is not None and push.after != targets.candidate.build.commit_sha:
            return Accepted(status="ignored", delivery_id=delivery_id)
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
        candidate_verdict = deliveries.c.report["candidate"]["verdict"].as_string()
        query = select(
            deliveries.c.id, deliveries.c.commit_sha, deliveries.c.state, deliveries.c.created_at,
            deliveries.c.report["baseline"]["verdict"].as_string().label("baseline_verdict"),
            candidate_verdict.label("candidate_verdict"),
            func.coalesce(deliveries.c.report["verdict"].as_string(), candidate_verdict).label("verdict"),
            mode,
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
    def run_detail(delivery_id: str, request: Request):
        result = load_run(
            delivery_id, deliveries.c.id, deliveries.c.commit_sha, deliveries.c.state, deliveries.c.report,
            deliveries.c.created_at, deliveries.c.completed_at, deliveries.c.github_check_id,
            deliveries.c.github_check_retry_at, mode,
        )
        approval = None
        deployment = None
        if result["evaluation_mode"] == "build_endpoints":
            with engine.connect() as connection:
                approval = connection.execute(select(approvals).where(
                    approvals.c.run_id == delivery_id,
                )).mappings().one_or_none()
                if approval is not None:
                    deployment = connection.execute(select(deployments).where(
                        deployments.c.run_id == delivery_id,
                    ).order_by(
                        deployments.c.recorded_at.desc(), deployments.c.rollout_uid,
                        deployments.c.generation.desc(), deployments.c.outcome,
                    ).limit(1)).mappings().one_or_none()
        return {
            **result,
            "created_at": as_utc(result["created_at"]),
            "completed_at": as_utc(result["completed_at"]),
            "github_check_retry_at": as_utc(result["github_check_retry_at"]),
            "can_investigate": is_maintainer(request) and result["evaluation_mode"] == "synthetic_fixture"
            and result["state"] == "completed",
            "approval": ApprovalDetail(**{**approval, "created_at": as_utc(approval["created_at"])}) if approval else None,
            "deployment": DeploymentDetail(
                **{**deployment, "recorded_at": as_utc(deployment["recorded_at"])}
            ) if deployment else None,
            "can_record_decision": is_approver(request) and approval is None
            and result["evaluation_mode"] == "build_endpoints" and result["state"] == "completed"
            and is_current_candidate(result),
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
                break
        next_cursor = items[limit - 1].case_id if len(items) > limit else None
        items = items[:limit]
        ids = {investigation_key(delivery_id, item.case_id): item for item in items if item.candidate.outcome == "fail"}
        if ids and report.get("evaluation_mode") != "build_endpoints":
            with engine.connect() as connection:
                saved = connection.scalars(select(investigations.c.id).where(
                    investigations.c.run_id == delivery_id, investigations.c.id.in_(ids),
                ))
                for investigation_id in saved:
                    ids[investigation_id].investigation_id = investigation_id
        return CasePage(state=run["state"], items=items, next_cursor=next_cursor)

    @app.post("/v1/runs/{delivery_id}/approval", dependencies=[Depends(require_approver)])
    def record_approval(delivery_id: str, body: ApprovalRequest) -> ApprovalDetail:
        run = load_run(delivery_id, deliveries.c.state, deliveries.c.commit_sha, deliveries.c.report, mode)
        report = run["report"] or {}
        if run["evaluation_mode"] != "build_endpoints" or run["state"] != "completed":
            raise ReadError(409, "run_not_eligible", "A completed build evaluation is required")
        if not is_current_candidate(run) or body.image_digest != targets.candidate.build.image_digest:
            raise ReadError(409, "stale_candidate", "The run and requested digest must match the configured candidate")
        if body.decision == "approve" and report.get("verdict") != "pass":
            raise ReadError(409, "gate_not_passed", "The release gate must pass before approval")

        record = ApprovalDetail(
            **body.model_dump(), run_id=delivery_id, actor=approver_actor, created_at=datetime.now(timezone.utc),
        )
        try:
            with engine.begin() as connection:
                connection.execute(approvals.insert().values(**record.model_dump()))
        except IntegrityError:
            with engine.connect() as connection:
                existing = connection.execute(select(approvals).where(
                    approvals.c.run_id == delivery_id,
                )).mappings().one_or_none()
            if existing is None:
                raise
            if any(existing[key] != getattr(record, key) for key in ("image_digest", "decision", "actor")):
                raise ReadError(409, "decision_exists", "A different decision already exists for this run")
            return ApprovalDetail(**{**existing, "created_at": as_utc(existing["created_at"])})
        return record

    @app.get("/v1/runs/{delivery_id}/promotion", dependencies=[Depends(require_approver)])
    def read_promotion(delivery_id: str, response: Response) -> PromotionDetail:
        run = load_run(delivery_id, deliveries.c.state, deliveries.c.commit_sha, deliveries.c.report, mode)
        report = run["report"] or {}
        if delivery_id.startswith("demo-") or run["evaluation_mode"] != "build_endpoints" or run["state"] != "completed":
            raise ReadError(409, "run_not_eligible", "A completed build evaluation from a GitHub delivery is required")
        if not is_current_candidate(run) or report["builds"].get("baseline") != targets.baseline.build.model_dump():
            raise ReadError(409, "stale_candidate", "The evaluated build pair must match the configured build pair")
        if report.get("verdict") != "pass":
            raise ReadError(409, "gate_not_passed", "The release gate must pass before rollout")
        with engine.connect() as connection:
            approval = connection.execute(select(approvals.c.decision, approvals.c.image_digest).where(
                approvals.c.run_id == delivery_id,
            )).one_or_none()
        if approval != ("approve", targets.candidate.build.image_digest):
            raise ReadError(409, "approval_required", "A saved approval for the candidate digest is required")
        response.headers["Cache-Control"] = "no-store"
        return PromotionDetail(run_id=delivery_id, baseline=targets.baseline.build, candidate=targets.candidate.build)

    @app.post("/v1/runs/{delivery_id}/deployments", dependencies=[Depends(require_approver)])
    def record_deployment(delivery_id: str, body: DeploymentObservation) -> DeploymentDetail:
        run = load_run(delivery_id, deliveries.c.state, deliveries.c.report, mode)
        report = run["report"] or {}
        if (delivery_id.startswith("demo-") or run["evaluation_mode"] != "build_endpoints"
                or run["state"] != "completed" or report.get("verdict") != "pass"):
            raise ReadError(409, "run_not_eligible", "A passing build evaluation from a GitHub delivery is required")
        builds = report.get("builds", {})
        candidate = builds.get("candidate", {}).get("image_digest")
        stable_variant = "candidate" if body.outcome == "promoted" else "baseline"
        stable = builds.get(stable_variant, {}).get("image_digest")
        if (body.image_digest, body.stable_image_digest) != (candidate, stable):
            raise ReadError(409, "deployment_digest_mismatch", "Observed digests do not match the evaluated builds")
        values = {
            **body.model_dump(mode="json"), "run_id": delivery_id, "recorded_at": datetime.now(timezone.utc),
        }
        try:
            with engine.begin() as connection:
                approval = connection.execute(select(approvals.c.decision, approvals.c.image_digest).where(
                    approvals.c.run_id == delivery_id,
                )).one_or_none()
                if approval != ("approve", body.image_digest):
                    raise ReadError(409, "approval_required", "A saved approval for the candidate digest is required")
                connection.execute(deployments.insert().values(**values))
        except IntegrityError:
            with engine.connect() as connection:
                existing = connection.execute(select(deployments).where(
                    deployments.c.run_id == delivery_id, deployments.c.rollout_uid == str(body.rollout_uid),
                    deployments.c.generation == body.generation, deployments.c.outcome == body.outcome,
                )).mappings().one_or_none()
            if existing is None:
                raise
            if any(existing[key] != value for key, value in body.model_dump(mode="json").items()):
                raise ReadError(409, "deployment_conflict", "A different observation already exists for this outcome")
            values = {**existing, "recorded_at": as_utc(existing["recorded_at"])}
        return DeploymentDetail(**values)

    @app.post("/v1/runs/{delivery_id}/investigations", status_code=202, dependencies=[Depends(require_maintainer)])
    def start_investigation(delivery_id: str, body: InvestigationRequest) -> QueuedInvestigation:
        run = load_run(delivery_id, deliveries.c.state, deliveries.c.report, mode)
        if run["evaluation_mode"] != "synthetic_fixture" or run["state"] != "completed" or run["report"] is None:
            raise ReadError(409, "run_not_investigable", "A completed synthetic evaluation is required")
        case = run["report"]["candidate"]["results"].get(body.case_id)
        if case is None:
            raise ReadError(404, "case_missing", "Case was not found")
        if case["outcome"] != "fail":
            raise ReadError(409, "case_not_failed", "A failed candidate case is required")
        return QueuedInvestigation(investigation_id=enqueue_investigation(engine, delivery_id, body.case_id))

    @app.get("/v1/runs/{delivery_id}/investigations/{investigation_id}", dependencies=[Depends(require_read)])
    def read_investigation(delivery_id: str, investigation_id: str) -> InvestigationDetail:
        with engine.connect() as connection:
            result = connection.execute(select(
                investigations.c.id.label("investigation_id"), investigations.c.case_id,
                investigations.c.state, investigations.c.report,
            ).join(deliveries, deliveries.c.id == investigations.c.run_id).where(
                *scope, investigations.c.run_id == delivery_id, investigations.c.id == investigation_id,
            )).mappings().one_or_none()
        if result is None:
            raise ReadError(404, "investigation_missing", "Investigation was not found")
        return InvestigationDetail(**result)

    return app
