"""Serve one local support application version for the endpoint evaluation demo."""

import argparse
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from service.evaluate_builds import Answer, BuildTargets

from .release_trace_demo import Config, evaluate

DIRECTORY = Path(__file__).resolve().parent


class Question(BaseModel):
    case_id: str
    question: str


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("variant", choices=("baseline", "candidate"))
    variant = parser.parse_args().variant
    targets = BuildTargets.model_validate_json((DIRECTORY / "build_targets.json").read_bytes())
    target = getattr(targets, variant)
    fixture = json.loads((DIRECTORY / "fixture.json").read_text(encoding="utf-8"))
    suite = json.loads((DIRECTORY / "gate_suite.json").read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in [fixture, *suite["cases"]]}
    config = Config(**fixture[variant])
    app = FastAPI()

    @app.post("/evaluate")
    def answer(question: Question) -> Answer:
        case = cases.get(question.case_id)
        if case is None:
            raise HTTPException(404, "Unknown demo case")
        result = evaluate({**case, "question": question.question}, config)
        return Answer(build=target.build, citation=result["citation"])

    uvicorn.run(app, host="127.0.0.1", port=target.url.port)


if __name__ == "__main__":
    main()
