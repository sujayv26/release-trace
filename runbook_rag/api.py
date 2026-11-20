from contextlib import ExitStack, asynccontextmanager
from hashlib import sha256
from hmac import compare_digest
import json
import logging
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .grounding import GroundedSearch, openai_backend
from .ranking import DEFAULT_VARIANT, VARIANTS, RunbookSearch
from .retrieval import Citation, DATA, build_index, read_corpus

trace = logging.getLogger("uvicorn.error")
REVISION = Path(__file__).with_name("release_revision.txt").read_text(encoding="utf-8").strip()


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=500)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Enter a question.")
        return value.strip()


class Answer(BaseModel):
    status: Literal["answered", "insufficient_evidence"]
    answer: str | None
    citations: list[Citation]


class EvaluationQuestion(Question):
    case_id: str = Field(min_length=1, max_length=128)


@asynccontextmanager
async def lifespan(app: FastAPI):
    commit = os.getenv("RUNBOOK_BUILD_COMMIT", "")
    digest = os.getenv("RUNBOOK_IMAGE_DIGEST", "")
    token = os.getenv("RUNBOOK_ACCESS_TOKEN", "")
    build = None
    if commit or digest or token or os.getenv("RUNBOOK_REQUIRE_RELEASE") == "1":
        if (not re.fullmatch(r"[a-f0-9]{40}", commit)
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest) or len(token) < 32
                or not token.isascii()):
            raise RuntimeError("Runbook release requires valid build identity and access configuration")
        build = {"commit_sha": commit, "image_digest": digest}
    variant = os.getenv("RUNBOOK_RETRIEVAL_VARIANT", DEFAULT_VARIANT)
    if variant not in VARIANTS:
        raise RuntimeError("Unknown runbook retrieval variant")
    configured_corpus = os.getenv("RUNBOOK_CORPUS_FILE")
    corpus = Path(configured_corpus) if configured_corpus else DATA / "runbooks.json"
    if configured_corpus and REVISION == "stale-candidate":
        raise RuntimeError("The intentional stale fixture requires the bundled corpus")
    snapshot = read_corpus(corpus)
    fingerprint = sha256(snapshot).hexdigest()
    expected = os.getenv("RUNBOOK_CORPUS_SHA256")
    if (expected and not compare_digest(expected.encode(), fingerprint.encode())
            or configured_corpus and build and not expected):
        raise RuntimeError("Runbook release corpus digest does not match its configuration")

    with ExitStack() as stack:
        directory = stack.enter_context(TemporaryDirectory(prefix="runbook-rag-"))
        database = Path(directory) / "runbooks.db"
        build_index(database, stale_checkout=REVISION == "stale-candidate", snapshot=snapshot)
        lexical = RunbookSearch(database)
        backend = openai_backend(stack, variant)
        app.state.retrieval = GroundedSearch(lexical, backend) if backend else lexical
        app.state.build = build
        app.state.token = token
        app.state.variant = variant
        app.state.models = ({"embedding_model": backend.embedding_model,
                             "answer_model": backend.answer_model} if backend else {})
        app.state.corpus_digest = fingerprint
        yield


app = FastAPI(title="Runbook RAG", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/ask", response_model=Answer)
def ask(payload: Question, request: Request):
    authorize(request)
    return retrieve(payload, request)


def authorize(request):
    token = request.app.state.token
    supplied = request.headers.get("Authorization", "")
    if token and not compare_digest(supplied.encode(), f"Bearer {token}".encode()):
        raise HTTPException(status_code=401, detail="Invalid runbook credential")


def retrieve(payload, request):
    state = request.app.state
    _, response, timings = state.retrieval.run(payload.question, state.variant)
    citation = next(iter(response["citations"]), None)
    trace.info("%s", json.dumps({
        "event": "runbook_request", "build": state.build, "revision": REVISION,
        "corpus_digest": state.corpus_digest, "variant": state.variant,
        "document_version": citation.id if citation else None,
        **state.models,
        "failure_reason": None if citation else "insufficient_evidence",
        **timings,
    }))
    return response


@app.post("/evaluate")
def evaluate(payload: EvaluationQuestion, request: Request):
    authorize(request)
    if request.app.state.build is None:
        raise HTTPException(status_code=503, detail="Build identity unavailable")
    result = retrieve(payload, request)
    return {
        "build": request.app.state.build,
        "citation": next((citation.id for citation in result["citations"]), None),
    }
