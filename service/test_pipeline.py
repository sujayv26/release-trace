import hmac
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, nullcontext, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from staging import promote_approved

from . import accounts as manage_accounts
from .api import PromotionDetail, create_app
from .evaluate_builds import FIXTURES, BuildTargets, evaluate_builds
from .github_check import BUILD_CHECK_NAME, check_payload, completed_run
from .investigator import process_investigation
from .schema import upgrade_database
from .store import database, enqueue, process_one


class PipelineTest(unittest.TestCase):
    def test_managed_accounts_isolate_runs_and_roles(self):
        targets = BuildTargets.model_validate_json((FIXTURES / "passing_build_targets.json").read_bytes())
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            config = Path(directory, "projects.json")
            Path(directory, "targets.json").write_text(targets.model_dump_json(), encoding="utf-8")
            config.write_text(json.dumps({"projects": [
                {"repository_id": 51, "installation_id": 61, "organization": "alpha-team", "name": "Support"},
                {"repository_id": 52, "installation_id": 62, "organization": "beta-team", "name": "Help desk",
                 "build_targets_file": "targets.json"},
            ]}), encoding="utf-8")
            db_path = Path(directory, "accounts.db").as_posix()
            with patch.dict(os.environ, {
                "RELEASETRACE_DATABASE_URL": os.getenv("RELEASETRACE_TEST_DATABASE_URL", f"sqlite:///{db_path}"),
                "RELEASETRACE_PROJECTS_FILE": str(config),
                "RELEASETRACE_WEBHOOK_SECRET": "managed-test-secret",
                "RELEASETRACE_READ_TOKEN": "legacy-viewer",
                "RELEASETRACE_CANDIDATE_EVALUATION_TOKEN": "legacy-token",
                "RELEASETRACE_CANDIDATE_EVALUATION_TOKEN_51": "another-project-token",
                "RELEASETRACE_CANDIDATE_EVALUATION_TOKEN_52": "beta-target-token",
            }):
                engine = database()
                cleanup.callback(engine.dispose)
                upgrade_database(engine)

                def provision(*arguments):
                    output = StringIO()
                    with patch("sys.argv", ["accounts", *arguments]), redirect_stdout(output):
                        manage_accounts.main()
                    return output.getvalue().strip()

                def bearer(key):
                    return {"Authorization": f"Bearer {key}"}

                alice = bearer(provision("create", "alpha-maintainer"))
                bob = bearer(provision("create", "beta-approver"))
                unassigned = bearer(provision("create", "no-project"))
                provision("grant", "alpha-maintainer", "51", "maintainer")
                provision("grant", "beta-approver", "52", "approver")

                with TestClient(create_app()) as client:
                    def push(repository, installation, sha, delivery_id):
                        body = json.dumps({
                            "after": sha, "ref": "refs/heads/main", "deleted": False,
                            "repository": {"id": repository}, "installation": {"id": installation},
                        }).encode()
                        return client.post("/webhooks/github", content=body, headers={
                            "X-GitHub-Event": "push", "X-GitHub-Delivery": delivery_id,
                            "X-Hub-Signature-256": "sha256=" + hmac.digest(b"managed-test-secret", body, "sha256").hex(),
                        })

                    self.assertEqual(client.get("/v1/me", headers=bearer("legacy-viewer")).status_code, 401)
                    self.assertEqual(client.get("/v1/me", headers=unassigned).json()["projects"], [])
                    self.assertEqual(client.get("/v1/me", headers=alice).json(), {
                        "handle": "alpha-maintainer",
                        "projects": [{"id": "51", "organization": "alpha-team", "name": "Support", "role": "maintainer"}],
                    })
                    self.assertEqual(push(51, 62, "a" * 40, "forged-scope").status_code, 403)
                    self.assertEqual(push(51, 61, "a" * 40, "managed-alpha").json()["status"], "queued")
                    self.assertEqual(push(52, 62, targets.candidate.build.commit_sha, "managed-beta").json()["status"], "queued")
                    self.assertEqual(process_one(engine), "managed-alpha")

                    def evaluate_target(request):
                        variant = "candidate" if request.url.port == 8102 else "baseline"
                        if variant == "candidate":
                            self.assertEqual(request.headers["authorization"], "Bearer beta-target-token")
                        policies = {case["case_id"]: case["required_citation"] for case in [
                            json.loads((FIXTURES / "fixture.json").read_bytes()),
                            *json.loads((FIXTURES / "gate_suite.json").read_bytes())["cases"],
                        ]}
                        case_id = json.loads(request.content)["case_id"]
                        return httpx.Response(200, json={
                            "build": getattr(targets, variant).build.model_dump(), "citation": policies[case_id],
                        })

                    with httpx.Client(transport=httpx.MockTransport(evaluate_target)) as evaluator:
                        with patch("service.evaluate_builds.httpx.Client", return_value=nullcontext(evaluator)):
                            self.assertEqual(process_one(engine), "managed-beta")
                    alpha, beta = "/v1/runs/managed-alpha", "/v1/runs/managed-beta"
                    self.assertEqual(client.get(alpha, headers=unassigned).status_code, 404)
                    report = client.post(f"{alpha}/investigations", headers=alice, json={"case_id": "account-closure"})
                    self.assertEqual(report.status_code, 202)
                    investigation_id = report.json()["investigation_id"]
                    self.assertEqual(process_investigation(engine), investigation_id)
                    investigation_path = f"{alpha}/investigations/{investigation_id}"
                    self.assertEqual(client.get(investigation_path, headers=alice).json()["state"], "completed")
                    for path in ("/v1/projects/51/runs", alpha, f"{alpha}/cases", investigation_path):
                        self.assertEqual(client.get(path, headers=bob).status_code, 404)
                    for path in ("/v1/projects/52/runs", beta, f"{beta}/cases", f"{beta}/promotion"):
                        self.assertEqual(client.get(path, headers=alice).status_code, 404)

                    decision = {"image_digest": targets.candidate.build.image_digest, "decision": "approve"}
                    self.assertEqual(client.post(f"{beta}/approval", headers=alice, json=decision).status_code, 404)
                    provision("grant", "alpha-maintainer", "52", "viewer")
                    self.assertEqual(len(client.get("/v1/me", headers=alice).json()["projects"]), 2)
                    self.assertEqual(client.get(beta, headers=alice).json()["report"]["verdict"], "pass")
                    self.assertEqual(client.post(f"{beta}/approval", headers=alice, json=decision).status_code, 403)
                    approved = client.post(f"{beta}/approval", headers=bob, json=decision).raise_for_status().json()
                    self.assertEqual(approved["actor"], "beta-approver")
                    self.assertEqual(client.get(f"{beta}/promotion", headers=bob).status_code, 200)

                    provision("revoke", "alpha-maintainer", "52")
                    self.assertEqual(client.get(beta, headers=alice).status_code, 404)
                    rotated = bearer(provision("rotate", "alpha-maintainer"))
                    self.assertEqual(client.get(alpha, headers=alice).status_code, 401)
                    self.assertEqual(client.get(alpha, headers=rotated).status_code, 200)
                    provision("disable", "alpha-maintainer")
                    self.assertEqual(client.get(alpha, headers=rotated).status_code, 401)

                changed = json.loads(config.read_bytes())
                changed["projects"][0]["organization"] = "gamma-team"
                config.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "cannot change organization"):
                    create_app()
                with patch.dict(os.environ, {"RELEASETRACE_PROJECTS_FILE": "", "RELEASETRACE_REPOSITORY_ID": "51",
                                             "RELEASETRACE_INSTALLATION_ID": "61"}):
                    with self.assertRaisesRegex(ValueError, "return to shared-token access"):
                        create_app()

    def test_build_push_persists_scoped_result_and_check(self):
        path = FIXTURES / "build_targets.json"
        pinned = BuildTargets.model_validate_json(path.read_bytes())
        sha = pinned.candidate.build.commit_sha
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            db_path = Path(directory, "builds.db").as_posix()
            with patch.dict(os.environ, {
                "RELEASETRACE_DATABASE_URL": os.getenv("RELEASETRACE_TEST_DATABASE_URL", f"sqlite:///{db_path}"),
                "RELEASETRACE_WEBHOOK_SECRET": "test-secret",
                "RELEASETRACE_READ_TOKEN": "test-token",
                "RELEASETRACE_REPOSITORY_ID": "41",
                "RELEASETRACE_INSTALLATION_ID": "42",
                "RELEASETRACE_BUILD_TARGETS_FILE": str(path),
                "RELEASETRACE_APPROVER_TOKEN": "test-approver",
                "RELEASETRACE_APPROVER_ACTOR": "release-reviewer",
            }):
                engine = database()
                cleanup.callback(engine.dispose)
                upgrade_database(engine)
                with TestClient(create_app()) as client:
                    def push(commit, delivery_id="build-1"):
                        body = json.dumps({
                            "after": commit, "ref": "refs/heads/main", "deleted": False,
                            "repository": {"id": 41}, "installation": {"id": 42},
                        }).encode()
                        response = client.post("/webhooks/github", content=body, headers={
                            "X-GitHub-Event": "push", "X-GitHub-Delivery": delivery_id,
                            "X-Hub-Signature-256": "sha256=" + hmac.digest(b"test-secret", body, "sha256").hex(),
                        })
                        self.assertEqual(response.status_code, 202)
                        return response.json()["status"]

                    self.assertEqual(push("a" * 40), "ignored")
                    self.assertIsNone(process_one(engine))
                    self.assertEqual(push(sha), "queued")
                    self.assertEqual(push(sha), "duplicate")
                    auth = {"Authorization": "Bearer test-token"}
                    detail = "/v1/runs/build-1"
                    self.assertEqual(client.get(detail, headers=auth).json()["evaluation_mode"], "build_endpoints")

                    def run_pinned(targets, fixture, suite, *, fail_candidate=True):
                        self.assertEqual(targets, pinned)
                        cases = {case["case_id"]: case for case in [fixture, *suite["cases"]]}

                        def respond(request):
                            case = cases[json.loads(request.content)["case_id"]]
                            is_candidate = request.url.port == 8102
                            target = targets.candidate if is_candidate else targets.baseline
                            citation = case["required_citation"]
                            if fail_candidate and is_candidate and case["critical"]:
                                citation = "account-closure-v1"
                            return httpx.Response(200, json={"build": target.build.model_dump(), "citation": citation})

                        with httpx.Client(transport=httpx.MockTransport(respond)) as evaluator:
                            return evaluate_builds(evaluator, targets, fixture, suite)

                    with patch("service.store.evaluate_configured_builds", side_effect=run_pinned):
                        self.assertEqual(process_one(engine), "build-1")
                    self.assertIsNone(process_one(engine))
                    history = client.get("/v1/projects/41/runs", headers=auth).json()["items"]
                    self.assertEqual([(run["verdict"], run["evaluation_mode"]) for run in history], [("block", "build_endpoints")])
                    result = client.get(detail, headers=auth).json()
                    self.assertEqual(result["report"]["builds"]["candidate"], pinned.candidate.build.model_dump())
                    self.assertNotIn("127.0.0.1", json.dumps(result))
                    failing = client.get(f"{detail}/cases?outcome=fail", headers=auth).json()["items"]
                    self.assertEqual(failing[0]["candidate"]["citation"], "account-closure-v1")
                    check = check_payload(completed_run(engine, "build-1", 41, 42))
                    self.assertEqual((check["name"], check["head_sha"], check["conclusion"]), (BUILD_CHECK_NAME, sha, "failure"))

                    approver = {"Authorization": "Bearer test-approver"}
                    approval = {"decision": "approve", "image_digest": pinned.candidate.build.image_digest}
                    endpoint = f"{detail}/approval"
                    self.assertEqual(client.post(endpoint, json=approval).status_code, 401)
                    self.assertEqual(client.post(endpoint, headers=auth, json=approval).status_code, 403)
                    blocked = client.post(endpoint, headers=approver, json=approval)
                    self.assertEqual((blocked.status_code, blocked.json()["code"]), (409, "gate_not_passed"))
                    rejected = client.post(endpoint, headers=approver, json={**approval, "decision": "reject"})
                    self.assertEqual(rejected.status_code, 200)
                    self.assertEqual(rejected.json()["decision"], "reject")
                    blocked_promotion = client.get(f"{detail}/promotion", headers=approver)
                    self.assertEqual((blocked_promotion.status_code, blocked_promotion.json()["code"]),
                                     (409, "gate_not_passed"))

                    self.assertEqual(push(sha, "build-passing"), "queued")
                    endpoint = "/v1/runs/build-passing/approval"
                    self.assertEqual(client.post(endpoint, headers=approver, json=approval).status_code, 409)
                    with patch("service.store.evaluate_configured_builds",
                               side_effect=lambda *args: run_pinned(*args, fail_candidate=False)):
                        self.assertEqual(process_one(engine), "build-passing")
                    passing_run = "/v1/runs/build-passing"
                    promotion_path = f"{passing_run}/promotion"
                    deployments_path = f"{passing_run}/deployments"
                    observation = {
                        "rollout_uid": "fc2fe7eb-32c0-4e65-ace8-058dd667be58",
                        "generation": 2, "namespace": "demo", "rollout": "support",
                        "outcome": "aborted", "image_digest": pinned.candidate.build.image_digest,
                        "stable_image_digest": pinned.baseline.build.image_digest,
                    }
                    self.assertEqual(client.get(promotion_path, headers=auth).status_code, 403)
                    pending = client.get(promotion_path, headers=approver)
                    self.assertEqual((pending.status_code, pending.json()["code"]), (409, "approval_required"))
                    pending_deployment = client.post(deployments_path, headers=approver, json=observation)
                    self.assertEqual((pending_deployment.status_code, pending_deployment.json()["code"]),
                                     (409, "approval_required"))
                    self.assertTrue(client.get(passing_run, headers=approver).json()["can_record_decision"])
                    self.assertFalse(client.get(passing_run, headers=auth).json()["can_record_decision"])
                    saved = client.post(endpoint, headers=approver, json=approval)
                    self.assertEqual(saved.status_code, 200)
                    self.assertEqual(saved.json()["actor"], "release-reviewer")
                    reviewed = client.get(passing_run, headers=auth).json()
                    self.assertEqual(reviewed["approval"], saved.json())
                    self.assertIsNone(reviewed["deployment"])
                    self.assertFalse(reviewed["can_record_decision"])
                    self.assertEqual(client.post(endpoint, headers=approver, json=approval).json(), saved.json())
                    authorized = client.get(promotion_path, headers=approver)
                    self.assertEqual(authorized.status_code, 200)
                    self.assertEqual(authorized.headers["Cache-Control"], "no-store")
                    promotion = PromotionDetail.model_validate(authorized.json())
                    self.assertEqual((promotion.baseline, promotion.candidate), (pinned.baseline.build, pinned.candidate.build))
                    baseline_image = f"registry.example/app@{promotion.baseline.image_digest}"
                    container = {"name": "app", "image": baseline_image}
                    rollout = {
                        "metadata": {"resourceVersion": "17", "uid": observation["rollout_uid"],
                                     "generation": 1, "namespace": "demo", "name": "support"},
                        "spec": {"strategy": {"canary": {}}, "template": {"spec": {"containers": [container]}}},
                        "status": {"phase": "Healthy", "stableRS": "baseline", "currentPodHash": "baseline"},
                    }
                    image, changes = promote_approved.rollout_patch(rollout, "app", promotion)
                    self.assertEqual(image, f"registry.example/app@{pinned.candidate.build.image_digest}")
                    self.assertEqual(changes, [
                        {"op": "test", "path": "/metadata/resourceVersion", "value": "17"},
                        {"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": image},
                    ])
                    argv = [
                        "promote_approved", "build-passing", "--context", "kind-staging",
                        "--namespace", "demo", "--rollout", "support", "--container", "app",
                    ]
                    command_output = StringIO()

                    aborted = {
                        "metadata": {**rollout["metadata"], "generation": 2},
                        "spec": {**rollout["spec"], "template": {"spec": {"containers": [{"name": "app", "image": image}]}}},
                        "status": {"observedGeneration": "2", "abort": True,
                                   "stableRS": "baseline", "currentPodHash": "candidate"},
                    }
                    stable_replicaset = {
                        "metadata": {"ownerReferences": [{"uid": observation["rollout_uid"], "controller": True}]},
                        "status": {"availableReplicas": 1},
                        "spec": {"template": {"spec": {"containers": [{"name": "app", "image": baseline_image}]}}},
                    }

                    patched = False

                    def execute_kubectl(command, **kwargs):
                        nonlocal patched
                        if "patch" in command:
                            patched = True
                            return json.dumps(aborted)
                        if "replicasets" in command:
                            return json.dumps({"items": [stable_replicaset]})
                        return json.dumps(aborted if patched else rollout)

                    with (
                        patch("staging.promote_approved.httpx.Client", side_effect=lambda **_: nullcontext(client)),
                        patch("staging.promote_approved.subprocess.check_output", side_effect=execute_kubectl) as kubectl,
                        patch("sys.argv", argv),
                        redirect_stdout(command_output),
                    ):
                        promote_approved.main()
                        self.assertEqual(kubectl.call_count, 1)
                        self.assertEqual(json.loads(command_output.getvalue())["state"], "preview")
                        command_output.truncate(0)
                        command_output.seek(0)
                        argv.extend(("--apply", "--observe", "5"))
                        promote_approved.main()
                        patch_commands = [call.args[0] for call in kubectl.call_args_list if "patch" in call.args[0]]
                        self.assertEqual(len(patch_commands), 1)
                        self.assertEqual(json.loads(patch_commands[0][patch_commands[0].index("--patch") + 1]), changes)
                        completed = json.loads(command_output.getvalue())
                        self.assertEqual(completed["state"], "submitted")
                        self.assertEqual(completed["deployment"]["outcome"], "aborted")
                    self.assertEqual(client.post(deployments_path, headers=auth, json=observation).status_code, 403)
                    retry = client.post(deployments_path, headers=approver, json=observation).raise_for_status()
                    self.assertEqual(retry.json(), completed["deployment"])
                    bad = client.post(deployments_path, headers=approver, json={**observation, "stable_image_digest": approval["image_digest"]})
                    self.assertEqual((bad.status_code, bad.json()["code"]), (409, "deployment_digest_mismatch"))
                    self.assertEqual(client.get(passing_run, headers=auth).json()["deployment"], completed["deployment"])
                    container["image"] = image
                    self.assertEqual(promote_approved.rollout_patch(rollout, "app", promotion), (image, []))
                    container["image"] = "registry.example/app@sha256:" + "0" * 64
                    with self.assertRaisesRegex(ValueError, "does not match the evaluated baseline"):
                        promote_approved.rollout_patch(rollout, "app", promotion)
                    conflict = client.post(endpoint, headers=approver, json={**approval, "decision": "reject"})
                    self.assertEqual((conflict.status_code, conflict.json()["code"]), (409, "decision_exists"))

                    updated = pinned.model_dump(mode="json")
                    updated["candidate"]["build"]["image_digest"] = "sha256:" + "e" * 64
                    new_targets = Path(directory, "superseding-targets.json")
                    new_targets.write_text(json.dumps(updated), encoding="utf-8")
                    with patch.dict(os.environ, {"RELEASETRACE_BUILD_TARGETS_FILE": str(new_targets)}):
                        with TestClient(create_app()) as restarted:
                            stale = restarted.post(endpoint, headers=approver, json=approval)
                            self.assertEqual((stale.status_code, stale.json()["code"]), (409, "stale_candidate"))
                            stale_promotion = restarted.get(promotion_path, headers=approver)
                            self.assertEqual((stale_promotion.status_code, stale_promotion.json()["code"]),
                                             (409, "stale_candidate"))
                            self.assertEqual(restarted.get(passing_run, headers=auth).json()["deployment"],
                                             completed["deployment"])

    def test_signed_delivery_produces_one_scoped_report(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            db_path = Path(directory, "releases.db").as_posix()
            with patch.dict(os.environ, {
                "RELEASETRACE_DATABASE_URL": os.getenv("RELEASETRACE_TEST_DATABASE_URL", f"sqlite:///{db_path}"),
                "RELEASETRACE_WEBHOOK_SECRET": "test-secret",
                "RELEASETRACE_READ_TOKEN": "test-token",
                "RELEASETRACE_REPOSITORY_ID": "1",
                "RELEASETRACE_INSTALLATION_ID": "2",
                "RELEASETRACE_BUILD_TARGETS_FILE": "",
                "RELEASETRACE_MAINTAINER_TOKEN": "maintainer-token",
            }):
                engine = database()
                cleanup.callback(engine.dispose)
                upgrade_database(engine)
                with TestClient(create_app()) as client:
                    self.assertEqual(client.get("/readyz").status_code, 200)
                    sha = "a" * 40
                    body = json.dumps({
                        "after": sha, "ref": "refs/heads/main", "deleted": False,
                        "repository": {"id": 1}, "installation": {"id": 2},
                    }).encode()
                    headers = {
                        "X-GitHub-Event": "push", "X-GitHub-Delivery": "delivery-1",
                        "X-Hub-Signature-256": "sha256=" + hmac.digest(
                            b"test-secret", body, "sha256",
                        ).hex(),
                    }
                    url = "/webhooks/github"
                    self.assertEqual(client.post(url, content=body, headers={
                        **headers, "X-Hub-Signature-256": "bad",
                    }).status_code, 401)
                    wrong_project = body.replace(b'"id": 2', b'"id": 3')
                    self.assertEqual(client.post(url, content=wrong_project, headers={
                        **headers, "X-Hub-Signature-256": "sha256=" + hmac.digest(
                            b"test-secret", wrong_project, "sha256",
                        ).hex(),
                    }).status_code, 403)
                    accepted = client.post(url, content=body, headers=headers)
                    self.assertEqual(accepted.status_code, 202)
                    self.assertEqual(accepted.json()["status"], "queued")
                    self.assertEqual(client.post(url, content=body, headers=headers).json()["status"], "duplicate")
                    run = "/v1/runs/delivery-1"
                    cases = f"{run}/cases"
                    history = "/v1/projects/1/runs"
                    auth = {"Authorization": "Bearer test-token"}
                    for endpoint in (run, cases, history):
                        self.assertEqual(client.get(endpoint).status_code, 401)
                    self.assertEqual(client.get(cases, headers=auth).json(), {
                        "state": "queued", "items": [], "next_cursor": None,
                    })
                    with self.assertRaisesRegex(ValueError, "No completed evaluation"):
                        completed_run(engine, "delivery-1", 1, 2)
                    self.assertEqual(process_one(engine), "delivery-1")
                    self.assertIsNone(process_one(engine))
                    result = client.get(run, headers=auth).json()
                    self.assertEqual(result["state"], "completed")
                    self.assertFalse(result["can_investigate"])
                    self.assertEqual(result["commit_sha"], sha)
                    self.assertEqual(result["report"]["candidate"]["verdict"], "block")
                    investigate = f"{run}/investigations"
                    maintain = {"Authorization": "Bearer maintainer-token"}
                    self.assertTrue(client.get(run, headers=maintain).json()["can_investigate"])
                    body_case = {"case_id": "account-closure"}
                    self.assertEqual(client.post(investigate, json=body_case, headers=auth).status_code, 403)
                    self.assertEqual(client.post(investigate, json={"case_id": "billing"}, headers=maintain).status_code, 409)
                    queued = client.post(investigate, json=body_case, headers=maintain)
                    self.assertEqual(queued.status_code, 202)
                    investigation_id = queued.json()["investigation_id"]
                    self.assertEqual(client.post(investigate, json=body_case, headers=maintain).json(), queued.json())
                    report_url = f"{investigate}/{investigation_id}"
                    self.assertEqual(client.get(report_url).status_code, 401)
                    self.assertEqual(client.get(report_url, headers=auth).json()["state"], "queued")
                    self.assertEqual(process_investigation(engine), investigation_id)
                    self.assertIsNone(process_investigation(engine))
                    diagnosis = client.get(report_url, headers=auth).json()["report"]
                    self.assertEqual(diagnosis["outcome"], "supported")
                    self.assertEqual(diagnosis["candidate"]["citation"], "account-closure-v1")
                    self.assertEqual(diagnosis["experiment"]["citation"], "account-closure-v2")
                    self.assertEqual(diagnosis["experiment"]["config"], {**diagnosis["candidate"]["config"], "retrieval": "newest"})
                    self.assertEqual(client.get(run, headers=auth).json()["report"], result["report"])
                    other = f"/v1/runs/other/investigations/{investigation_id}"
                    self.assertEqual(client.get(other, headers=auth).status_code, 404)
                    with self.assertRaisesRegex(ValueError, "No completed evaluation"):
                        completed_run(engine, "delivery-1", 1, 3)
                    check = check_payload(completed_run(engine, "delivery-1", 1, 2))
                    self.assertEqual(check["head_sha"], sha)
                    self.assertEqual(check["external_id"], "delivery-1")
                    self.assertEqual(check["conclusion"], "neutral")
                    self.assertIn("**block**", check["output"]["summary"])
                    self.assertIn("account-closure", check["output"]["summary"])
                    created = datetime.fromisoformat(result["created_at"].replace("Z", "+00:00"))
                    completed = datetime.fromisoformat(result["completed_at"].replace("Z", "+00:00"))
                    self.assertEqual(created.utcoffset(), timedelta(0))
                    self.assertEqual(completed.utcoffset(), timedelta(0))
                    self.assertGreaterEqual(completed, created)

                    failures = client.get(cases, params={"outcome": "fail"}, headers=auth).json()
                    self.assertEqual(len(failures["items"]), 1)
                    failure = failures["items"][0]
                    self.assertTrue(failure["critical"])
                    self.assertEqual(failure["investigation_id"], investigation_id)
                    self.assertEqual(failure["required_citation"], "account-closure-v2")
                    self.assertEqual(failure["baseline"]["evidence"]["id"], "account-closure-v2")
                    self.assertEqual(failure["candidate"]["evidence"]["id"], "account-closure-v1")
                    first = client.get(cases, params={"limit": 1}, headers=auth).json()
                    rest = client.get(cases, params={"after": first["next_cursor"]}, headers=auth).json()
                    self.assertEqual(first["next_cursor"], "account-closure")
                    self.assertEqual(len(rest["items"]), 4)
                    self.assertIsNone(rest["next_cursor"])

                    self.assertEqual(client.post(url, content=body, headers={
                        **headers, "X-GitHub-Delivery": "delivery-2",
                    }).status_code, 202)
                    enqueue(engine, "delivery-3", 1, 3, sha, {})
                    first = client.get(history, params={"limit": 1}, headers=auth).json()
                    self.assertEqual(first["next_cursor"], "delivery-1")
                    self.assertEqual(first["items"][0]["candidate_verdict"], "block")
                    saved_timestamp = datetime.fromisoformat(first["items"][0]["created_at"].replace("Z", "+00:00"))
                    self.assertEqual(saved_timestamp, created)
                    rest = client.get(history, params={"after": first["next_cursor"]}, headers=auth).json()
                    self.assertEqual([item["id"] for item in rest["items"]], ["delivery-2"])
                    self.assertIsNone(rest["items"][0]["candidate_verdict"])
                    self.assertIsNone(rest["next_cursor"])
                    self.assertEqual(client.get("/v1/projects/2/runs", headers=auth).status_code, 404)
                    self.assertEqual(client.get("/v1/runs/delivery-3/cases", headers=auth).status_code, 404)
                    self.assertEqual(client.get(history, params={"limit": 101}, headers=auth).status_code, 422)
