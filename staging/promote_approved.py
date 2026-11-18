"""Start an Argo canary for a current, approved ReleaseTrace evaluation."""

import argparse
import json
import os
import re
import subprocess
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from service.api import DELIVERY_ID, PromotionDetail
from service.evaluate_builds import LOOPBACK

RESOURCE_NAME = re.compile(r"[a-z0-9](?:[-.a-z0-9]*[a-z0-9])?")


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--rollout", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--apply", action="store_true", help="Update the Rollout image. The default previews the change.")
    args = parser.parse_args()
    if not DELIVERY_ID.fullmatch(args.run_id) or any(
        len(value) > 253 or not RESOURCE_NAME.fullmatch(value)
        for value in (args.rollout, args.namespace, args.container)
    ):
        parser.error("Invalid run ID or Kubernetes resource name")
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
    try:
        rollout = json.loads(subprocess.check_output([*kubectl, "get", resource, "-o", "json"], timeout=20))
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            response = client.get(
                f"{args.api_url.rstrip('/')}/v1/runs/{args.run_id}/promotion",
                headers={"Authorization": f"Bearer {token}", "Cache-Control": "no-cache"},
            )
            response.raise_for_status()
            promotion = PromotionDetail.model_validate_json(response.content)
        if promotion.run_id != args.run_id:
            raise ValueError("The promotion response identifies a different run")
        image, patch = rollout_patch(rollout, args.container, promotion)
        state = "already_configured"
        if patch:
            state = "preview"
            if args.apply:
                subprocess.run([*kubectl, "patch", resource, "--type=json",
                                "--patch", json.dumps(patch, separators=(",", ":"))],
                               check=True, timeout=20, stdout=subprocess.DEVNULL)
                state = "submitted"
    except httpx.HTTPStatusError as error:
        parser.exit(1, f"Promotion authorization failed with HTTP {error.response.status_code}.\n")
    except (httpx.RequestError, ValidationError, json.JSONDecodeError, KeyError, TypeError):
        parser.exit(1, "Could not read a valid promotion response or Rollout.\n")
    except (OSError, subprocess.SubprocessError):
        parser.exit(1, "kubectl could not read or update the Rollout.\n")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps({"run_id": promotion.run_id, "image": image, "state": state}))


if __name__ == "__main__":
    main()
