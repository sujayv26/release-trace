"""Exercise the demo stack through the running API and background worker."""

import os
from time import monotonic, sleep

import httpx

from .seed_demo import queue_demo


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_for_report(client, path):
    deadline = monotonic() + 30
    while monotonic() < deadline:
        run = client.get(path).raise_for_status().json()
        if run["state"] == "completed":
            return run
        sleep(0.5)
    raise RuntimeError(f"Worker did not complete {path} in 30 seconds")


def main():
    delivery = queue_demo()
    require(delivery["status"] in {"queued", "duplicate"}, "Signed fixture event was not accepted")
    delivery_id = delivery["delivery_id"]
    require(queue_demo() == {"status": "duplicate", "delivery_id": delivery_id}, "Duplicate fixture delivery was not deduplicated")

    viewer = {"Authorization": f"Bearer {os.environ['RELEASETRACE_READ_TOKEN']}"}
    maintainer = {"Authorization": f"Bearer {os.environ['RELEASETRACE_MAINTAINER_TOKEN']}"}
    repository = int(os.environ["RELEASETRACE_REPOSITORY_ID"])
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=viewer, timeout=5, trust_env=False) as client:
        path = f"/v1/runs/{delivery_id}"
        evaluation = wait_for_report(client, path)
        require(evaluation["commit_sha"] == "1" * 40, "Evaluation SHA did not match the signed fixture push")
        require(evaluation["evaluation_mode"] == "synthetic_fixture", "Expected a synthetic fixture run")
        gate = evaluation["report"]["candidate"]
        require(gate["verdict"] == "block" and gate["critical_failures"] == ["account-closure"],
                "Expected the critical account-closure failure to block the candidate")
        history = client.get(f"/v1/projects/{repository}/runs").raise_for_status().json()
        require(sum(run["id"] == delivery_id for run in history["items"]) == 1, "Expected one saved fixture run")

        request = {"case_id": "account-closure"}
        investigations = f"{path}/investigations"
        require(client.post(investigations, json=request).status_code == 403, "Viewer was able to queue an investigation")
        accepted = client.post(investigations, json=request, headers=maintainer)
        require(accepted.status_code == 202, "Maintainer investigation was not accepted")
        investigation_id = accepted.json()["investigation_id"]
        report = wait_for_report(client, f"{investigations}/{investigation_id}")["report"]
        require(report["outcome"] == "supported", "Retrieval diagnosis was not supported")
        require(report["candidate"]["citation"] == "account-closure-v1"
                and report["experiment"]["citation"] == "account-closure-v2"
                and report["experiment"]["outcome"] == "pass", "Retrieval rerun did not restore the correct citation")
        require(report["experiment"]["config"] == {**report["candidate"]["config"], "retrieval": "newest"},
                "Experiment changed more than retrieval")
        require(client.get(path).raise_for_status().json()["report"]["candidate"]["verdict"] == "block",
                "Investigation changed the release verdict")

    print("Verified signed fixture delivery, deduplication, blocked gate, and successful retrieval rerun.")


if __name__ == "__main__":
    main()
