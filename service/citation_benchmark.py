"""Compare the HTTP gate against fixed answer-quality labels for a synthetic municipal archive."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

import httpx

from .evaluate_builds import BuildTargets, evaluate_builds
from .evaluation_config import EvaluationDefinition

DATA = Path(__file__).resolve().parents[1] / "demo" / "archive_holdout.json"


def benchmark():
    source = DATA.read_bytes()
    data = json.loads(source)
    targets = BuildTargets.model_validate({
        role: {"url": f"https://{role}.invalid/evaluate",
               "build": {"commit_sha": digit * 40, "image_digest": "sha256:" + digit * 64}}
        for role, digit in (("baseline", "1"), ("candidate", "2"))
    })
    policies = [{"id": name, "revision": int(name.rsplit("-v", 1)[1]), "text": text}
                for name, text in data["documents"].items()]
    rows = []
    counts = Counter()
    for case in data["cases"]:
        # Each observation represents one release with one critical case. Combining them
        # would let a caught failure hide an unrelated false pass in the aggregate verdict.
        spec = EvaluationDefinition.model_validate({
            "suite": {"revision": data["revision"], "cases": [{"case_id": case["id"], "question": case["question"],
                      "required_citation": case["expected"], "policies": policies}]},
            "policy": {"revision": "archive-critical-v1", "critical_cases": [case["id"]], "minimum_score": 1.0},
        }).snapshot()

        def endpoint(request):
            assert json.loads(request.content) == {"case_id": case["id"], "question": case["question"]}
            stable = request.url.host == "baseline.invalid"
            return httpx.Response(200, json={
                "build": (targets.baseline if stable else targets.candidate).build.model_dump(),
                "citation": case["expected"] if stable else case["citation"],
                "answer": data["documents"][case["expected"]] if stable else case["answer"],
            })

        with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
            report = evaluate_builds(client, targets, None, None, project_evaluation=spec)
        assert report["baseline"]["verdict"] == "pass"
        category, label = data["labels"][case["id"]]
        verdict = report["verdict"]
        stopped = verdict != "pass"
        if label == "regression":
            result = "caught" if stopped else "missed"
        else:
            result = "false_stop" if stopped else "valid_pass"
        counts[result] += 1
        rows.append({"id": case["id"], "category": category, "label": label, "verdict": verdict, "result": result})
    assert len(rows) == len(data["labels"]) == len({row["id"] for row in rows})
    return {"revision": data["revision"], "dataset_sha256": sha256(source).hexdigest(),
            "cases": rows, "counts": dict(counts)}


if __name__ == "__main__":
    print(json.dumps(benchmark(), indent=2))
