"""Run the clean-checkout operator drill against disposable Docker Compose services."""

import argparse
import json
import os
import ssl
import subprocess
from contextlib import ExitStack
from pathlib import Path
from secrets import token_hex
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from urllib.request import urlopen
from uuid import uuid4

import httpx

from demo.pilot_target import PROJECTS, identity
from service.seed_demo import signed_push_request
from service.smoke_demo import require, wait_for_report


def target_url(project, variant):
    return f"https://pilot-target:8443/{project}/{variant}/evaluate"


def write_targets(directory, project, candidate="candidate"):
    (directory / f"{project}-builds.json").write_text(json.dumps({
        variant: {"url": target_url(project, candidate if variant == "candidate" else variant),
                  "build": identity(project, variant)}
        for variant in ("baseline", "candidate")
    }), encoding="utf-8")


def prepare(directory):
    projects = []
    directory.mkdir()
    for name, (repository, installation, case, citation, question) in PROJECTS.items():
        write_targets(directory, name)
        projects.append({"repository_id": repository, "installation_id": installation, "organization": f"pilot-{name}",
                         "name": name.title(), "build_targets_file": f"{name}-builds.json",
                         "evaluation_file": f"{name}-evaluation.json"})
        (directory / f"{name}-evaluation.json").write_text(json.dumps({
            "suite": {"revision": f"{name}-suite-v1", "cases": [{"case_id": case, "question": question,
                      "required_citation": citation, "policies": [{"id": citation, "revision": 1, "text": question}]}]},
            "policy": {"revision": f"{name}-gate-v1", "minimum_score": 1.0 if name == "alpha" else 0.8,
                       "critical_cases": [case] if name == "alpha" else []},
        }), encoding="utf-8")
    (directory / "projects.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")


def provision(compose, name, role):
    command = ("exec", "-T", "api", "python", "-m", "service.accounts")
    # Capture the one-time key and use it in memory only.
    key = compose(*command, "create", name, capture=True).strip()
    compose(*command, "grant", name, str(PROJECTS[name][0]), role)
    return key


def queue(api_url, secret, project, delivery, expected="queued"):
    repository, installation, *_ = PROJECTS[project]
    request = signed_push_request(api_url, repository, installation, identity(project, "candidate")["commit_sha"],
                                  delivery, secret.encode())
    with urlopen(request, timeout=5) as response:
        require(response.status == 202 and json.load(response) == {"status": expected, "delivery_id": delivery},
                f"Unexpected webhook result for {delivery}. Expected {expected}.")


def inspect_project(client, project, delivery):
    repository, _, case, citation, _ = PROJECTS[project]
    account = client.get("/v1/me").raise_for_status().json()
    require([item["id"] for item in account["projects"]] == [str(repository)], f"Incorrect membership for {project}")
    path = f"/v1/runs/{delivery}"
    run = wait_for_report(client, path)
    report = run["report"]
    require(run["project_id"] == str(repository) and report["verdict"] == "pass"
            and report["suite_revision"] == f"{project}-suite-v1" and report["policy_revision"] == f"{project}-gate-v1"
            and report["candidate"]["minimum_score"] == (1.0 if project == "alpha" else 0.8),
            f"Incorrect build result or evaluation provenance for {project}")
    cases = client.get(f"{path}/cases").raise_for_status().json()["items"]
    require([(item["case_id"], item["required_citation"], item["critical"]) for item in cases]
            == [(case, citation, project == "alpha")], f"Incorrect case selection for {project}")
    return {"repository_id": repository, "delivery": delivery, "verdict": report["verdict"], "case": case,
            **{field: report[field] for field in ("suite_revision", "policy_revision", "dataset_digest", "policy_digest")}}


def verify_denial(compose, directory, secret, alpha, beta, target):
    write_targets(directory, "beta", candidate="unapproved")
    compose("restart", "api")
    compose("up", "-d", "--wait", "--wait-timeout", "60", "api")
    api_url = "http://" + compose("port", "api", "8000", capture=True).strip()
    alpha.base_url = beta.base_url = api_url
    before = target.get("/requests").raise_for_status().json()
    delivery = "pilot-beta-denied"
    queue(api_url, secret, "beta", delivery)
    path = f"/v1/runs/{delivery}"
    run = wait_for_report(beta, path)
    case = beta.get(f"{path}/cases").raise_for_status().json()["items"][0]
    after = target.get("/requests").raise_for_status().json()
    require(run["report"]["verdict"] == "inconclusive" and case["candidate"]["reason"] == "destination_not_approved",
            "Worker did not record the destination denial")
    require(after.get("beta/unapproved", 0) == 0 and after["beta/baseline"] == before["beta/baseline"] + 1,
            "Expected zero requests to the denied endpoint and one to the approved baseline")
    return api_url, {"delivery": delivery, "verdict": "inconclusive", "reason": case["candidate"]["reason"],
                     "unapproved_requests": after.get("beta/unapproved", 0), "approved_baseline_requests": 1}


def recover(compose, api_url, secret, alpha, target):
    delivery = "pilot-alpha-recovery"
    path = f"/v1/runs/{delivery}"
    before = target.get("/requests").raise_for_status().json()
    target.post("/hold-next-alpha-baseline").raise_for_status()
    start = monotonic()
    queue(api_url, secret, "alpha", delivery)
    while target.get("/requests").raise_for_status().json()["alpha/baseline"] == before["alpha/baseline"]:
        require(monotonic() - start < 15, "Worker did not reach the held request")
        sleep(0.1)
    detected = monotonic()
    compose("kill", "-s", "SIGKILL", "worker")
    pending = alpha.get(path).raise_for_status().json()
    require(pending["state"] == "queued" and pending["report"] is None, "The worker completed before interruption")
    sleep(1.1)
    monitor = ("exec", "-T", "api", "python", "-m", "service.queue_health", "--max-age-seconds", "1")
    alert = json.loads(compose(*monitor, capture=True, expected_status=1))
    require(alert["status"] == "stalled" and alert["stalled_count"] == 1,
            "Expected a stalled evaluation alert while the worker was stopped")
    queue(api_url, secret, "alpha", delivery, expected="duplicate")
    compose("start", "worker")
    run = wait_for_report(alpha, path)
    counts = target.get("/requests").raise_for_status().json()
    require(run["report"]["verdict"] == "pass"
            and counts["alpha/baseline"] == before["alpha/baseline"] + 2
            and counts["alpha/candidate"] == before["alpha/candidate"] + 1,
            "Expected an interrupted baseline request and a successful reevaluation of both builds")
    history = alpha.get(f"/v1/projects/{PROJECTS['alpha'][0]}/runs").raise_for_status().json()["items"]
    require(sum(item["id"] == delivery for item in history) == 1, "Recovery produced duplicate runs")
    healthy = json.loads(compose(*monitor, capture=True))
    require(healthy["status"] == "healthy", "Queue alert did not clear after recovery")
    return path, {"delivery": delivery, "state_after_kill": pending["state"], "redelivery": "duplicate",
                  "queue_alert": alert, "queue_after_recovery": healthy,
                  "logical_results": 1, "verdict": run["report"]["verdict"], "baseline_requests": 2,
                  "candidate_requests": 1, "detected_seconds": round(detected - start, 3),
                  "recovered_after_detection_seconds": round(monotonic() - detected, 3)}


def record_and_restore(compose, directory, path, alpha, key):
    candidate = identity("alpha", "candidate")["image_digest"]
    approval_payload = {"decision": "approve", "image_digest": candidate}
    observation = {"rollout_uid": str(uuid4()), "generation": 1, "namespace": "pilot-local", "rollout": "alpha",
                   "outcome": "aborted", "image_digest": candidate,
                   "stable_image_digest": identity("alpha", "baseline")["image_digest"]}
    approval = alpha.post(f"{path}/approval", json=approval_payload).raise_for_status().json()
    require(alpha.post(f"{path}/approval", json=approval_payload).raise_for_status().json() == approval
            and approval["actor"] == "alpha", "Approval retry did not preserve the original record")
    deployment = alpha.post(f"{path}/deployments", json=observation).raise_for_status().json()
    require(alpha.post(f"{path}/deployments", json=observation).raise_for_status().json() == deployment,
            "Observation retry did not preserve the original record")
    original = alpha.get(path).raise_for_status().json()
    require(original["approval"] == approval and original["deployment"] == deployment, "Run is missing saved records")
    db = ("exec", "-T", "db")
    with (directory / "release.dump").open("w+b") as backup:
        compose(*db, "pg_dump", "-U", "releasetrace", "-Fc", "releasetrace", stdout=backup)
        compose(*db, "createdb", "-U", "releasetrace", "releasetrace_restored")
        backup.seek(0)
        compose(*db, "pg_restore", "-U", "releasetrace", "--exit-on-error", "--single-transaction", "-d",
                "releasetrace_restored", stdin=backup)
    compose("up", "-d", "--wait", "--wait-timeout", "90", "restore-api")
    address = compose("port", "restore-api", "8000", capture=True).strip()
    with httpx.Client(base_url=f"http://{address}", headers={"Authorization": f"Bearer {key}"}, trust_env=False) as client:
        require(client.get(path).raise_for_status().json() == original, "Restored API returned different release records")
        require(client.get(f"{path}/cases").raise_for_status().json()
                == alpha.get(f"{path}/cases").raise_for_status().json(), "Restored API returned different case evidence")
    command = ("python", "-m", "service.accounts", "history")
    source_audit = json.loads(compose("exec", "-T", "api", *command, capture=True))
    restored_audit = json.loads(compose("exec", "-T", "restore-api", *command, capture=True))
    require(len(source_audit) == 5 and restored_audit == source_audit, "Restore returned different access history")
    return {"approval": approval, "rollout_observation": deployment, "observation_source": "synthetic fixture",
            "restore_database": "releasetrace_restored", "api_run_and_cases_match": True,
            "restored_access_events": len(restored_audit)}


def exercise(compose, directory, secret):
    compose("up", "--build", "-d", "--wait", "--wait-timeout", "90")
    api_url = "http://" + compose("port", "api", "8000", capture=True).strip()
    target_url = "https://" + compose("port", "pilot-target", "8443", capture=True).strip()
    alpha_key = provision(compose, "alpha", "approver")
    beta_key = provision(compose, "beta", "viewer")
    require(alpha_key != beta_key, "Reviewer keys are not individual")
    context = ssl.create_default_context(cafile=str(directory / "tls" / "cert.pem"))
    with ExitStack() as clients:
        alpha, beta = [clients.enter_context(httpx.Client(base_url=api_url,
                       headers={"Authorization": f"Bearer {key}"}, trust_env=False)) for key in (alpha_key, beta_key)]
        target = clients.enter_context(httpx.Client(base_url=target_url, verify=context, trust_env=False))
        for project in PROJECTS:
            queue(api_url, secret, project, f"pilot-{project}")
        projects = [inspect_project(client, project, f"pilot-{project}")
                    for client, project in ((alpha, "alpha"), (beta, "beta"))]
        cross_reads = [alpha.get("/v1/runs/pilot-beta").status_code, beta.get("/v1/runs/pilot-alpha").status_code]
        require(cross_reads == [404, 404], "Cross-project read did not fail closed")
        api_url, denial = verify_denial(compose, directory / "projects", secret, alpha, beta, target)
        compose("exec", "-T", "api", "python", "-m", "service.accounts", "disable", "beta")
        revoked_status = beta.get("/v1/runs/pilot-beta").status_code
        require(revoked_status == 401, "Disabled reviewer key still has access")
        path, recovery = recover(compose, api_url, secret, alpha, target)
        restored = record_and_restore(compose, directory, path, alpha, alpha_key)
    return {"projects": projects, "cross_project_statuses": cross_reads, "revoked_key_status": revoked_status,
            "destination_denial": denial, "recovery": recovery, "restored": restored}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=Path("pilot-evidence.json"))
    args = parser.parse_args()
    with TemporaryDirectory(prefix="releasetrace-pilot-") as temporary:
        directory = Path(temporary)
        prepare(directory / "projects")
        tls = directory / "tls"
        tls.mkdir()
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=pilot-target",
            "-addext", "subjectAltName=DNS:pilot-target,IP:127.0.0.1", "-keyout", str(tls / "key.pem"),
            "-out", str(tls / "cert.pem"),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        # The temp directory is private to the operator. The demo container runs as UID 10001.
        (tls / "key.pem").chmod(0o644)
        project_name = f"releasetrace-pilot-{token_hex(4)}"
        secret = token_hex(32)
        destinations = {str(data[0]): {variant: target_url(name, variant) for variant in ("baseline", "candidate")}
                        for name, data in PROJECTS.items()}
        env = {**os.environ, "RELEASETRACE_PILOT_DIR": directory.as_posix(), "RELEASETRACE_PILOT_WEBHOOK_SECRET": secret,
               "RELEASETRACE_PILOT_DESTINATIONS": json.dumps(destinations), "RELEASETRACE_API_PORT": "0",
               "RELEASETRACE_DASHBOARD_PORT": "0"}
        base = ("docker", "compose", "-p", project_name, "-f", "compose.yaml", "-f", "compose.pilot.yaml")

        def compose(*command, capture=False, check=True, expected_status=0, **streams):
            print(subprocess.list2cmdline([*base, *command]), flush=True)
            if capture:
                streams["stdout"] = subprocess.PIPE
            result = subprocess.run([*base, *command], env=env, **streams)
            if check and result.returncode != expected_status:
                raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout)
            return result.stdout.decode() if capture else None

        try:
            evidence = exercise(compose, directory, secret)
        except Exception:
            compose("ps", "-a", check=False)
            compose("logs", "--tail", "60", check=False)
            raise
        finally:
            compose("--profile", "restore", "down", "--volumes", "--remove-orphans")
    redacted = json.dumps(evidence, indent=2) + "\n"
    args.evidence.write_text(redacted, encoding="utf-8")
    print(redacted, end="")


if __name__ == "__main__":
    main()
