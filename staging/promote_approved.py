"""Start an Argo canary for a current, approved ReleaseTrace evaluation."""

import argparse
import json
import os
import re
import subprocess
import time
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from service.api import DELIVERY_ID, DeploymentObservation, PromotionDetail
from service.evaluate_builds import LOOPBACK

RESOURCE_NAME = re.compile(r"[a-z0-9](?:[-.a-z0-9]*[a-z0-9])?")
BUILD_WORKFLOW = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml")
HASH_LABEL = "rollouts-pod-template-hash"


def rollout_patch(rollout, container, promotion):
    containers = rollout["spec"]["template"]["spec"]["containers"]
    index, selected = next(((index, spec) for index, spec in enumerate(containers)
                            if spec["name"] == container), (None, None))
    if selected is None:
        raise ValueError("The named container is missing from the Rollout")
    repository, separator, current_digest = selected["image"].partition("@")
    if not separator or not repository or ":" in repository.rsplit("/", 1)[-1]:
        raise ValueError("The Rollout image must use repository@digest")
    candidate_digest = promotion.candidate.image_digest
    if current_digest == candidate_digest:
        return selected["image"], []
    if current_digest != promotion.baseline.image_digest:
        raise ValueError("The Rollout image digest does not match the evaluated baseline")
    status = rollout.get("status", {})
    if (status.get("phase") != "Healthy" or not status.get("stableRS")
            or status["stableRS"] != status.get("currentPodHash")):
        raise ValueError("The Rollout must have a healthy, stable revision before starting a canary")
    if "canary" not in rollout["spec"]["strategy"]:
        raise ValueError("The Rollout must use a canary strategy")
    image = f"{repository}@{candidate_digest}"
    return image, [
        {"op": "test", "path": "/metadata/resourceVersion", "value": rollout["metadata"]["resourceVersion"]},
        {"op": "replace", "path": f"/spec/template/spec/containers/{index}/image", "value": image},
    ]


def observe_rollout(get, resource, submitted, container, image, promotion, timeout):
    uid = submitted["uid"]
    generation = submitted["generation"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rollout = get(resource)
        metadata = rollout["metadata"]
        containers = rollout["spec"]["template"]["spec"]["containers"]
        if (metadata["uid"] != uid or metadata["generation"] != generation
                or not any(item["name"] == container and item["image"] == image for item in containers)):
            raise ValueError("The Rollout changed while waiting for the approved generation")
        status = rollout.get("status", {})
        stable, current = status.get("stableRS"), status.get("currentPodHash")
        if str(generation) == str(status.get("observedGeneration")) and stable and current:
            outcome = None
            if status.get("abort") is True and current != stable:
                outcome, expected = "aborted", promotion.baseline.image_digest
            elif status.get("phase") == "Healthy" and current == stable and not status.get("abort"):
                outcome, expected = "promoted", promotion.candidate.image_digest
            if outcome:
                replicasets = get("replicasets", "-l", f"{HASH_LABEL}={stable}")["items"]
                for replica in replicasets:
                    owned = any(owner.get("uid") == uid and owner.get("controller") is True
                                for owner in replica["metadata"].get("ownerReferences", []))
                    ready = replica.get("status", {}).get("availableReplicas", 0)
                    images = replica["spec"]["template"]["spec"]["containers"]
                    if owned and ready and any(item["name"] == container and item["image"].endswith(f"@{expected}")
                                               for item in images):
                        return DeploymentObservation(
                            rollout_uid=uid, generation=generation, namespace=metadata["namespace"],
                            rollout=metadata["name"], outcome=outcome, image_digest=promotion.candidate.image_digest,
                            stable_image_digest=expected,
                        )
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise ValueError("Rollout outcome was not confirmed before the timeout. Use --observe again to resume.")


def verify_build(image, commit, workflow):
    repository = "/".join(workflow.split("/")[:2])
    try:
        subprocess.run([
            "gh", "attestation", "verify", f"oci://{image}", "--repo", repository,
            "--signer-workflow", workflow, "--source-digest", commit,
        ], check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Build provenance verification failed for the approved image and commit") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--rollout", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--apply", action="store_true", help="Update the Rollout image. The default previews the change.")
    parser.add_argument("--observe", type=int, metavar="SECONDS", help="Wait for and record promotion or abort")
    parser.add_argument("--attested-by", metavar="WORKFLOW", help="Require provenance from owner/repo/.github/workflows/build.yml")
    args = parser.parse_args()
    if not DELIVERY_ID.fullmatch(args.run_id) or any(
        len(value) > 253 or not RESOURCE_NAME.fullmatch(value)
        for value in (args.rollout, args.namespace, args.container)
    ):
        parser.error("Invalid run ID or Kubernetes resource name")
    if args.observe is not None and not 1 <= args.observe <= 3600:
        parser.error("--observe must be between 1 and 3600 seconds")
    if args.attested_by and not BUILD_WORKFLOW.fullmatch(args.attested_by):
        parser.error("--attested-by requires an owner/repo/.github/workflows/build.yml reference")
    base = urlsplit(args.api_url)
    local = base.hostname in LOOPBACK or base.hostname == "::1"
    if (not base.hostname or base.username is not None or base.password is not None
            or base.query or base.fragment or base.path not in ("", "/")
            or (base.scheme != "https" and not (base.scheme == "http" and local))):
        parser.error("Use an HTTPS API origin or an HTTP loopback origin")
    token = os.getenv("RELEASETRACE_APPROVER_TOKEN")
    if not token:
        parser.error("RELEASETRACE_APPROVER_TOKEN is required")

    resource = f"rollouts.argoproj.io/{args.rollout}"
    kubectl = ["kubectl", f"--context={args.context}", f"--namespace={args.namespace}", "--request-timeout=10s"]
    def get(*target):
        return json.loads(subprocess.check_output([*kubectl, "get", *target, "-o", "json"], timeout=20))

    approval_url = f"{args.api_url.rstrip('/')}/v1/runs/{args.run_id}"
    observation = None
    try:
        rollout = get(resource)
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            headers = {"Authorization": f"Bearer {token}", "Cache-Control": "no-cache"}
            response = client.get(
                f"{approval_url}/promotion", headers=headers,
            )
            response.raise_for_status()
            promotion = PromotionDetail.model_validate_json(response.content)
            if promotion.run_id != args.run_id:
                raise ValueError("The promotion response identifies a different run")
            image, patch = rollout_patch(rollout, args.container, promotion)
            state = "preview" if patch else "already_configured"
            if args.observe is not None and patch and not args.apply:
                raise ValueError("The candidate is not configured. Use --apply to start the canary.")
            if args.attested_by:
                verify_build(image, promotion.candidate.commit_sha, args.attested_by)
            if patch and args.apply:
                rollout = json.loads(subprocess.check_output(
                    [*kubectl, "patch", resource, "--type=json",
                     "--patch", json.dumps(patch, separators=(",", ":")), "-o", "json"], timeout=20,
                ))
                state = "submitted"
            if args.observe is not None:
                recorded = observe_rollout(get, resource, rollout["metadata"], args.container, image, promotion, args.observe)
                saved = client.post(f"{approval_url}/deployments", headers=headers, json=recorded.model_dump(mode="json"))
                saved.raise_for_status()
                observation = saved.json()
    except httpx.HTTPStatusError as error:
        action = "Recording the rollout" if error.request.method == "POST" else "Promotion authorization"
        parser.exit(1, f"{action} failed with HTTP {error.response.status_code}.\n")
    except (httpx.RequestError, ValidationError, json.JSONDecodeError, KeyError, TypeError):
        parser.exit(1, "Could not read a valid promotion response or Rollout.\n")
    except (OSError, subprocess.SubprocessError):
        parser.exit(1, "kubectl could not read or update the Rollout.\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    result = {"run_id": promotion.run_id, "image": image, "state": state}
    if observation is not None:
        result["deployment"] = observation
    print(json.dumps(result))


if __name__ == "__main__":
    main()
