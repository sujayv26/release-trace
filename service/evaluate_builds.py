"""Evaluate two pinned application endpoints with the ReleaseTrace gate."""

import argparse
import json
import os
from pathlib import Path
from time import monotonic
from typing import Annotated

import httpx
from pydantic import BaseModel, HttpUrl, StringConstraints, ValidationError, field_validator

from demo.release_gate import evaluate_suite

FIXTURES = Path(__file__).resolve().parents[1] / "demo"
MAX_RESPONSE_BYTES = 16_384
RESPONSE_TIMEOUT = 5
LOOPBACK = {"localhost", "127.0.0.1", "[::1]"}
CommitSHA = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
ImageDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]


class BuildIdentity(BaseModel):
    commit_sha: CommitSHA
    image_digest: ImageDigest


class EvaluationTarget(BaseModel):
    url: HttpUrl
    build: BuildIdentity

    @field_validator("url")
    @classmethod
    def validate_url(cls, url):
        if url.username or url.password or url.query is not None or url.fragment is not None:
            raise ValueError("Use an endpoint URL without credentials, query, or fragment")
        if url.scheme != "https" and url.host not in LOOPBACK:
            raise ValueError("Use HTTPS for non-local endpoints")
        return url


class BuildTargets(BaseModel):
    baseline: EvaluationTarget
    candidate: EvaluationTarget


class Answer(BaseModel):
    build: BuildIdentity
    citation: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None


def score_case(client, target, case, token=None):
    headers = {"Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = {"case_id": case["case_id"], "question": case["question"]}

    def missing(reason):
        return {"outcome": "inconclusive", "citation": None, "evidence": None, "reason": reason}

    try:
        deadline = monotonic() + RESPONSE_TIMEOUT
        with client.stream("POST", str(target.url), json=request, headers=headers) as response:
            response.raise_for_status()
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                return missing("unsupported_encoding")
            body = bytearray()
            chunks = (response.content,) if response.is_stream_consumed else response.iter_raw()
            for chunk in chunks:
                if monotonic() > deadline:
                    return missing("endpoint_timeout")
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    return missing("response_too_large")
                body.extend(chunk)
        answer = Answer.model_validate_json(body)
    except httpx.TimeoutException:
        return missing("endpoint_timeout")
    except httpx.HTTPError:
        return missing("endpoint_error")
    except ValidationError:
        return missing("invalid_response")

    if answer.build != target.build:
        return missing("build_mismatch")
    policy = next((item for item in case["policies"] if item["id"] == answer.citation), None)
    if policy is None:
        return missing("citation_unverified")
    return {
        "outcome": "pass" if answer.citation == case["required_citation"] else "fail",
        "citation": answer.citation,
        "evidence": policy,
    }


def evaluate_builds(client, targets, fixture, suite, tokens=None):
    by_variant = {"baseline": targets.baseline, "candidate": targets.candidate}
    tokens = tokens or {}
    report = evaluate_suite(fixture, suite, lambda variant, case: score_case(
        client, by_variant[variant], case, tokens.get(variant),
    ))
    ready = report["baseline"]["verdict"] == "pass"
    candidate = report["candidate"]["verdict"]
    return {
        "evaluation_mode": "build_endpoints",
        "evaluator_revision": "citation-v1",
        "verdict": candidate if ready else "inconclusive",
        "reason": None if ready else "baseline_not_verified",
        "builds": {variant: target.build.model_dump() for variant, target in by_variant.items()},
        **report,
    }


def evaluate_configured_builds(targets, fixture, suite):
    tokens = {variant: os.getenv(f"RELEASETRACE_{variant.upper()}_EVALUATION_TOKEN")
              for variant in ("baseline", "candidate")}
    with httpx.Client(timeout=RESPONSE_TIMEOUT, follow_redirects=False, trust_env=False) as client:
        return evaluate_builds(client, targets, fixture, suite, tokens)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("targets", type=Path, help="JSON file listing baseline and candidate endpoints and build identities")
    args = parser.parse_args()
    try:
        targets = BuildTargets.model_validate_json(args.targets.read_bytes())
    except (OSError, ValidationError):
        parser.error("Could not load a valid build target file")

    fixture = json.loads((FIXTURES / "fixture.json").read_text(encoding="utf-8"))
    suite = json.loads((FIXTURES / "gate_suite.json").read_text(encoding="utf-8"))
    report = evaluate_configured_builds(targets, fixture, suite)
    print(json.dumps(report, indent=2))
    return {"pass": 0, "block": 1, "inconclusive": 2}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
