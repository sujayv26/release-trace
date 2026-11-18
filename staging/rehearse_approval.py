"""Verify the approved OCI image from signed push through a kind canary."""

import argparse
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import httpx

from service.evaluate_builds import BuildTargets
from service.smoke_demo import require, wait_for_report

API = "http://127.0.0.1:8000"
ROLLOUT = "rollouts.argoproj.io/approved-support"
HASH_LABEL = "rollouts-pod-template-hash"


def wait(label, observe):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if result := observe():
            return result
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {label}")


def healthy(client, url):
    try:
        return client.get(url).status_code == 200
    except httpx.RequestError:
        return False


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def execute(*command, timeout=30, **kwargs):
    return subprocess.check_output(command, text=True, timeout=timeout, **kwargs).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--baseline", required=True, help="Registry reference pinned by digest")
    parser.add_argument("--candidate", required=True, help="Registry reference pinned by digest")
    parser.add_argument("--commit", required=True)
    parser.add_argument("--attested-by")
    parser.add_argument("--attestation-bundle")
    args = parser.parse_args()
    if args.attestation_bundle and not args.attested_by:
        parser.error("--attestation-bundle requires --attested-by")
    attestation = []
    if args.attested_by:
        attestation.extend(("--attested-by", args.attested_by))
    if args.attestation_bundle:
        attestation.extend(("--attestation-bundle", args.attestation_bundle))
    baseline_repository, _, baseline_digest = args.baseline.partition("@")
    candidate_repository, _, candidate_digest = args.candidate.partition("@")
    if baseline_repository != candidate_repository or baseline_digest == candidate_digest:
        parser.error("Use two distinct digests in the same image repository")
    ports = {"baseline": 8101, "candidate": 8102}
    targets = BuildTargets.model_validate({
        variant: {
            "url": f"http://127.0.0.1:{port}/evaluate",
            "build": {"commit_sha": args.commit, "image_digest": digest},
        }
        for (variant, port), digest in zip(ports.items(), (baseline_digest, candidate_digest))
    })
    kubectl = ("kubectl", f"--context={args.context}", f"--namespace={args.namespace}", "--request-timeout=10s")

    def get(*resource):
        return json.loads(execute(*kubectl, "get", *resource, "-o", "json"))

    def stable_status(image):
        rollout = get(ROLLOUT)
        status = rollout.get("status", {})
        stable_hash = status.get("stableRS")
        selector = get("service/support-stable")["spec"]["selector"]
        if (status.get("phase") == "Healthy" and stable_hash
                and stable_hash == status.get("currentPodHash") == selector.get(HASH_LABEL)
                and rollout["spec"]["template"]["spec"]["containers"][0]["image"] == image):
            return stable_hash
        return None

    with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
        files = Path(directory)
        target_file = files / "targets.json"
        target_file.write_text(targets.model_dump_json(), encoding="utf-8")
        for port, ref in zip(ports.values(), (args.baseline, args.candidate)):
            container = execute("docker", "run", "--detach", "--rm", "--publish", f"127.0.0.1:{port}:8100",
                                "--volume", f"{target_file}:/etc/releasetrace/targets.json:ro", ref)
            cleanup.callback(subprocess.run, ["docker", "stop", container], stdout=subprocess.DEVNULL, timeout=20)

        secret, viewer, approver = (uuid4().hex for _ in range(3))
        environment = {
            **os.environ,
            "RELEASETRACE_DATABASE_URL": f"sqlite:///{(files / 'release.db').as_posix()}",
            "RELEASETRACE_BUILD_TARGETS_FILE": str(target_file),
            "RELEASETRACE_WEBHOOK_SECRET": secret,
            "RELEASETRACE_READ_TOKEN": viewer,
            "RELEASETRACE_APPROVER_TOKEN": approver,
            "RELEASETRACE_APPROVER_ACTOR": "Staging rehearsal",
            "RELEASETRACE_REPOSITORY_ID": "41",
            "RELEASETRACE_INSTALLATION_ID": "42",
        }
        execute(sys.executable, "-m", "service.store", env=environment)
        for command in (
            ("-m", "uvicorn", "service.api:create_app", "--factory"),
            ("-m", "service.worker"),
        ):
            process = subprocess.Popen([sys.executable, *command], env=environment, stdout=sys.stderr)
            cleanup.callback(stop, process)

        client = cleanup.enter_context(httpx.Client(base_url=API, timeout=5, trust_env=False))
        wait("ReleaseTrace API", lambda: healthy(client, "/readyz"))
        for port in ports.values():
            wait(f"evaluation target on {port}", lambda: healthy(client, f"http://127.0.0.1:{port}/healthz"))

        run_id = str(uuid4())
        body = json.dumps({
            "after": args.commit, "ref": "refs/heads/develop", "deleted": False,
            "repository": {"id": 41}, "installation": {"id": 42},
        }).encode()
        push_headers = {
            "X-GitHub-Event": "push", "X-GitHub-Delivery": run_id,
            "X-Hub-Signature-256": "sha256=" + hmac.digest(secret.encode(), body, "sha256").hex(),
        }
        for expected in ("queued", "duplicate"):
            delivery = client.post("/webhooks/github", content=body, headers=push_headers).raise_for_status().json()
            require(delivery == {"status": expected, "delivery_id": run_id}, "Push did not deduplicate")
        path = f"/v1/runs/{run_id}"
        client.headers["Authorization"] = f"Bearer {viewer}"
        report = wait_for_report(client, path)["report"]
        require(report["verdict"] == "pass", "The evaluation did not pass")
        require(report["builds"] == {
            variant: getattr(targets, variant).build.model_dump() for variant in ports
        }, "The evaluation identities did not match the registry digests")
        authorization = {"Authorization": f"Bearer {approver}"}
        require(client.get(f"{path}/promotion", headers=authorization).status_code == 409,
                "The run authorized deployment before approval")
        decision = client.post(f"{path}/approval", headers=authorization, json={
            "decision": "approve", "image_digest": candidate_digest,
        }).raise_for_status().json()
        require(decision["image_digest"] == candidate_digest, "Approval recorded a different digest")

        execute(*kubectl, "create", "configmap", "evaluation-targets", f"--from-file=targets.json={target_file}")
        manifest = Path(__file__).with_name("approved-canary.yaml").read_text(encoding="utf-8")
        execute(*kubectl, "apply", "-f", "-", input=manifest.replace("BASELINE_IMAGE", args.baseline))
        baseline_hash = wait("the evaluated baseline to become stable", lambda: stable_status(args.baseline))
        submission = json.loads(execute(
            sys.executable, "-m", "staging.promote_approved", run_id,
            "--context", args.context, "--namespace", args.namespace,
            "--rollout", "approved-support", "--container", "support", "--apply", "--observe", "120",
            *attestation, env=environment, timeout=220,
        ))
        require(submission["state"] == "submitted" and submission["image"] == args.candidate,
                "The submitted image did not match the approved registry digest")
        if args.attested_by:
            require(submission["verified_build"] == {"workflow": args.attested_by, "commit_sha": args.commit,
                                                     "image_digest": candidate_digest}, "Provenance did not match the approval")
        recorded = client.get(path).raise_for_status().json()["deployment"]
        require(recorded == submission["deployment"] and recorded["outcome"] == "promoted"
                and recorded["stable_image_digest"] == candidate_digest,
                "The run did not expose the saved rollout outcome to the viewer")
        stable_hash = wait("the approved image to become stable", lambda: stable_status(args.candidate))
        require(stable_hash != baseline_hash, "The approved image did not create a new stable revision")
        analyses = get("analysisruns")["items"]
        successful = [item["metadata"]["name"] for item in analyses if item["status"]["phase"] == "Successful"]
        require(bool(successful), "No successful canary analysis was recorded")
        pods = get("pods", "-l", f"app=approved-support,{HASH_LABEL}={stable_hash}")["items"]
        image_ids = [status["imageID"] for pod in pods for status in pod["status"].get("containerStatuses", [])
                     if status["name"] == "support" and status["ready"]]
        require(bool(image_ids) and all(image_id.endswith(candidate_digest) for image_id in image_ids),
                "The stable Pods did not report the approved runtime image digest")
        stable_probe = """
from urllib.request import Request, urlopen
request = Request("http://support-stable:8100/evaluate",
                  data=b'{"case_id":"account-closure","question":"How do I close my account?"}',
                  headers={"Content-Type": "application/json"})
with urlopen(request, timeout=3) as response:
    print(response.read().decode())
"""

        def stable_serves_candidate():
            try:
                answer = json.loads(execute(*kubectl, "exec", "service/support-stable", "--", "python", "-c", stable_probe))
            except subprocess.CalledProcessError:
                return False
            return answer == {"build": targets.candidate.build.model_dump(), "citation": "account-closure-v2"}

        wait("the stable Service to return the approved build and current policy", stable_serves_candidate)

        print(json.dumps({
            "delivery_id": run_id, "commit_sha": args.commit, "gate": report["verdict"],
            "approved_digest": decision["image_digest"], "submitted_image": submission["image"],
            "baseline_hash": baseline_hash, "stable_hash": stable_hash,
            "stable_pod_image_ids": image_ids, "successful_analysis_runs": successful,
            "stable_service_citation": "account-closure-v2",
            "verified_build": submission.get("verified_build"),
            "recorded_rollout": recorded,
        }, indent=2))


if __name__ == "__main__":
    main()
