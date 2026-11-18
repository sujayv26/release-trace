import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from .api import create_app
from .store import database, enqueue, metadata, process_one


class PipelineTest(unittest.TestCase):
    def test_signed_delivery_produces_one_scoped_report(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory, "releases.db").as_posix()
            with patch.dict(os.environ, {
                "RELEASETRACE_DATABASE_URL": f"sqlite:///{db_path}",
                "RELEASETRACE_WEBHOOK_SECRET": "test-secret",
                "RELEASETRACE_READ_TOKEN": "test-token",
                "RELEASETRACE_REPOSITORY_ID": "1",
                "RELEASETRACE_INSTALLATION_ID": "2",
            }):
                engine = database()
                self.addCleanup(engine.dispose)
                metadata.create_all(engine)
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
                    self.assertEqual(process_one(engine), "delivery-1")
                    self.assertIsNone(process_one(engine))
                    result = client.get(run, headers=auth).json()
                    self.assertEqual(result["state"], "completed")
                    self.assertEqual(result["commit_sha"], sha)
                    self.assertEqual(result["report"]["candidate"]["verdict"], "block")

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
                    rest = client.get(history, params={"after": first["next_cursor"]}, headers=auth).json()
                    self.assertEqual([item["id"] for item in rest["items"]], ["delivery-2"])
                    self.assertIsNone(rest["items"][0]["candidate_verdict"])
                    self.assertIsNone(rest["next_cursor"])
                    self.assertEqual(client.get("/v1/projects/2/runs", headers=auth).status_code, 404)
                    self.assertEqual(client.get("/v1/runs/delivery-3/cases", headers=auth).status_code, 404)
                    self.assertEqual(client.get(history, params={"limit": 101}, headers=auth).status_code, 422)
                engine.dispose()
