import hmac
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from .api import create_app
from .evaluate_builds import FIXTURES, BuildTargets, evaluate_builds
from .github_check import BUILD_CHECK_NAME, check_payload, completed_run
from .investigator import process_investigation
from .schema import upgrade_database
from .store import database, enqueue, process_one


class PipelineTest(unittest.TestCase):
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
            }):
                engine = database()
                cleanup.callback(engine.dispose)
                upgrade_database(engine)
                with TestClient(create_app()) as client:
                    def push(commit):
                        body = json.dumps({
                            "after": commit, "ref": "refs/heads/main", "deleted": False,
                            "repository": {"id": 41}, "installation": {"id": 42},
                        }).encode()
                        response = client.post("/webhooks/github", content=body, headers={
                            "X-GitHub-Event": "push", "X-GitHub-Delivery": "build-1",
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

                    def run_pinned(targets, fixture, suite):
                        self.assertEqual(targets, pinned)
                        cases = {case["case_id"]: case for case in [fixture, *suite["cases"]]}

                        def respond(request):
                            case = cases[json.loads(request.content)["case_id"]]
                            is_candidate = request.url.port == 8102
                            target = targets.candidate if is_candidate else targets.baseline
                            citation = "account-closure-v1" if is_candidate and case["critical"] else case["required_citation"]
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
                    self.assertEqual(result["commit_sha"], sha)
                    self.assertEqual(result["report"]["candidate"]["verdict"], "block")
                    investigate = f"{run}/investigations"
                    maintain = {"Authorization": "Bearer maintainer-token"}
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
