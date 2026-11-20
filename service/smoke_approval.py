"""Exercise the passing-build and protected approval demo through the API."""

import os

import httpx

from .evaluate_builds import FIXTURES, BuildTargets
from .seed_demo import queue_demo
from .smoke_demo import require, wait_for_report


def main():
    targets_path = FIXTURES / "passing_build_targets.json"
    candidate = BuildTargets.model_validate_json(targets_path.read_bytes()).candidate.build
    delivery = queue_demo(targets_path)
    require(delivery["status"] in {"queued", "duplicate"}, "Signed build event was not accepted")
    viewer = {"Authorization": f"Bearer {os.environ['RELEASETRACE_READ_TOKEN']}"}
    approver = {"Authorization": f"Bearer {os.environ['RELEASETRACE_APPROVER_TOKEN']}"}
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=viewer, timeout=5, trust_env=False) as client:
        path = f"/v1/runs/{delivery['delivery_id']}"
        report = wait_for_report(client, path)["report"]
        require(report["verdict"] == "pass" and report["builds"]["candidate"] == candidate.model_dump(),
                "The corrected candidate did not pass with the pinned build identity")
        payload = {"decision": "approve", "image_digest": candidate.image_digest}
        endpoint = f"{path}/approval"
        require(client.post(endpoint, json=payload).status_code == 403, "Viewer could record an approval")
        wrong = client.post(endpoint, headers=approver, json={**payload, "image_digest": "sha256:" + "0" * 64})
        require(wrong.status_code == 409 and wrong.json()["code"] == "stale_candidate",
                "The API accepted a mismatched digest")
        saved = client.post(endpoint, headers=approver, json=payload).raise_for_status().json()
        retry = client.post(endpoint, headers=approver, json=payload).raise_for_status().json()
        require(saved == retry == client.get(path).raise_for_status().json()["approval"],
                "The approval retry or viewer read returned a different record")
    print("Verified passing evaluation, approver-only access, exact digest binding, and approval retries.")


if __name__ == "__main__":
    main()
