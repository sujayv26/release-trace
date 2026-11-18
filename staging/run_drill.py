"""Measure healthy promotion and automatic canary abort in a test cluster."""

import argparse
import json
import subprocess
import time
from pathlib import Path

ROLLOUT = "rollout/release-drill"
HASH_LABEL = "rollouts-pod-template-hash"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True, help="An existing, dedicated drill namespace")
    args = parser.parse_args()

    def kubectl(*command):
        return subprocess.check_output(
            ["kubectl", "--context", args.context, "--namespace", args.namespace,
             "--request-timeout=10s", *command],
            text=True, timeout=20,
        )

    def get(resource, name=None):
        return json.loads(kubectl("get", resource, *([name] if name else []), "-o", "json"))

    def wait(description, observe):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = observe()
            if result:
                return result
            time.sleep(1)
        raise SystemExit(f"Timed out waiting for {description}")

    def stable_hash():
        return get("service", "stable")["spec"]["selector"].get(HASH_LABEL)

    def promoted(previous=None):
        status = get(ROLLOUT).get("status", {})
        stable = status.get("stableRS")
        if stable and stable != previous and stable == status.get("currentPodHash") == stable_hash():
            if status.get("phase") == "Healthy":
                return stable
        return None

    def patch_revision(revision):
        patch = [{"op": "replace", "path": "/spec/template/spec/containers/0/env/0/value", "value": revision}]
        kubectl("patch", ROLLOUT, "--type=json", "--patch", json.dumps(patch, separators=(",", ":")))

    def analysis_runs(phase):
        return {
            item["metadata"]["name"] for item in get("analysisruns")["items"]
            if item.get("status", {}).get("phase") == phase
        }

    def stable_response():
        code = 'from urllib.request import urlopen; print(urlopen("http://stable:8080", timeout=3).read().decode())'
        return kubectl("exec", "service/stable", "--", "python", "-c", code).strip()

    manifest = Path(__file__).with_name("canary.yaml")
    kubectl("create", "-f", str(manifest))
    baseline = wait("baseline", promoted)

    started = time.monotonic()
    patch_revision("healthy")
    healthy = wait("healthy promotion", lambda: promoted(baseline))
    successful = wait("successful analysis", lambda: analysis_runs("Successful"))
    if stable_response() != "healthy":
        raise SystemExit("The stable service did not serve the promoted revision")
    promotion_seconds = time.monotonic() - started

    previous_failed = analysis_runs("Failed")
    started = time.monotonic()
    patch_revision("unhealthy")

    def failed_analysis():
        failed = analysis_runs("Failed") - previous_failed
        return min(failed) if failed else None

    failed = wait("failed canary analysis", failed_analysis)
    detected = time.monotonic()

    def aborted():
        status = get(ROLLOUT)["status"]
        return (
            status.get("abort") is True and status.get("stableRS") == healthy == stable_hash()
            and status.get("currentPodHash") != healthy
        )

    wait("automatic abort with the same stable revision", aborted)
    if stable_response() != "healthy":
        raise SystemExit("The stable service did not serve the previous revision after abort")
    recovered = time.monotonic()
    print(json.dumps({
        "healthy_promotion": {
            "previous_hash": baseline, "stable_hash": healthy, "analysis_runs": sorted(successful),
            "seconds": round(promotion_seconds, 3),
        },
        "unhealthy_abort": {
            "stable_hash": healthy, "failed_analysis_run": failed,
            "seconds_to_detect": round(detected - started, 3),
            "seconds_from_detection_to_stable_probe": round(recovered - detected, 3),
        },
    }, indent=2))


if __name__ == "__main__":
    main()
