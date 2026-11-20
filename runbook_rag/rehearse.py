"""Verify pinned Runbook RAG targets through the ReleaseTrace HTTP evaluator."""

import json
import os
from pathlib import Path
import sys

import httpx

from service.evaluate_builds import BuildTargets, evaluate_builds
from service.evaluation_config import read_evaluation

from .retrieval import DATA


def rehearse(path):
    raw = json.loads(path.read_bytes())
    token = os.environ["RUNBOOK_ACCESS_TOKEN"]
    headers = {"Authorization": f"Bearer {token}"}
    evidence = {}
    targets_by_run = {
        role: BuildTargets.model_validate({"baseline": raw["stable"], "candidate": raw[role]})
        for role in ("stale-candidate", "corrected-candidate")
    }
    identities = [target["build"] for target in raw.values()]
    for field in ("commit_sha", "image_digest"):
        if len({identity[field] for identity in identities}) != 3:
            raise ValueError(f"Expected three distinct values for {field}")

    with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
        for role, expected in (("stale-candidate", "block"), ("corrected-candidate", "pass")):
            targets = targets_by_run[role]
            report = evaluate_builds(
                client, targets, None, None, {"baseline": token, "candidate": token},
                project_evaluation=read_evaluation(DATA / "release-suite.json"),
            )
            if report["baseline"]["verdict"] != "pass" or report["verdict"] != expected:
                raise AssertionError(f"{role} did not return the expected release verdict")
            for variant, target in (("baseline", targets.baseline), ("candidate", targets.candidate)):
                body = {"case_id": "no-evidence", "question": "What are the checkout cafeteria lunch prices?"}
                denied = client.post(str(target.url), json=body)
                response = client.post(str(target.url), json=body, headers=headers)
                response.raise_for_status()
                if denied.status_code != 401 or response.json() != {"citation": None, "build": target.build.model_dump()}:
                    raise AssertionError(f"{role} {variant} failed credential or abstention checks")
            evidence[role] = report
    return evidence


if __name__ == "__main__":
    print(json.dumps(rehearse(Path(sys.argv[1])), indent=2))
