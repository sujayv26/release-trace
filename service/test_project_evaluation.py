import hmac
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from demo.release_trace_demo import digest

from .accounts import change_membership, issue_key
from .api import create_app
from .evaluation_config import EvaluationDefinition
from .projects import managed_projects
from .schema import upgrade_database
from .store import database, deliveries, process_one


def case(case_id, policy_id):
    return {
        "case_id": case_id,
        "question": f"What is the policy for {case_id}?",
        "required_citation": f"{policy_id}-v2",
        "policies": [
            {"id": f"{policy_id}-v1", "revision": 1, "text": "The superseded policy."},
            {"id": f"{policy_id}-v2", "revision": 2, "text": "The current policy."},
        ],
    }


class ProjectEvaluationTest(unittest.TestCase):
    def test_project_scopes_gate_and_queued_snapshot(self):
        original = {
            "suite": {"revision": "shipping-v1", "cases": [
                case("shipping-window", "shipping"), case("shipping-status", "tracking"),
            ]},
            "policy": {"revision": "shipping-gate-v1", "minimum_score": 0.5, "critical_cases": ["shipping-window"]},
        }
        updated = {
            "suite": {"revision": "shipping-v2", "cases": [
                case("shipping-window", "shipping"), case("shipping-expedite", "priority"),
                case("shipping-label", "labels"), case("shipping-dispatch", "dispatch"),
            ]},
            "policy": {"revision": "shipping-gate-v2", "minimum_score": 0.75, "critical_cases": ["shipping-expedite"]},
        }
        warranty = {
            "suite": {"revision": "warranty-v1", "cases": [case("warranty-coverage", "warranty")]},
            "policy": {"revision": "warranty-gate-v1", "minimum_score": 1.0, "critical_cases": []},
        }
        secret = b"project-evaluation-test"
        with tempfile.TemporaryDirectory() as directory, ExitStack() as cleanup:
            root = Path(directory)

            def write(name, value):
                (root / name).write_text(json.dumps(value), encoding="utf-8")

            def targets(port, candidate):
                return {variant: {
                    "url": f"http://127.0.0.1:{port + index}/evaluate",
                    "build": {"commit_sha": sha * 40, "image_digest": "sha256:" + sha * 64},
                } for index, (variant, sha) in enumerate((("baseline", "1"), ("candidate", candidate)))}

            shipping = targets(8101, "2")
            corrected = targets(8101, "3")
            beta = targets(8201, "4")
            write("shipping-targets.json", shipping)
            write("warranty-targets.json", beta)
            write("shipping-eval.json", original)
            write("warranty-eval.json", warranty)
            write("projects.json", {"projects": [
                {"repository_id": 71, "installation_id": 81, "organization": "shipping-team",
                 "name": "Shipping", "build_targets_file": "shipping-targets.json", "evaluation_file": "shipping-eval.json"},
                {"repository_id": 72, "installation_id": 82, "organization": "warranty-team", "name": "Warranty",
                 "build_targets_file": "warranty-targets.json", "evaluation_file": "warranty-eval.json"},
            ]})
            with patch.dict(os.environ, {
                "RELEASETRACE_DATABASE_URL": os.getenv("RELEASETRACE_TEST_DATABASE_URL", f"sqlite:///{(root / 'test.db').as_posix()}"),
                "RELEASETRACE_PROJECTS_FILE": str(root / "projects.json"),
                "RELEASETRACE_WEBHOOK_SECRET": secret.decode(),
            }):
                engine = database()
                cleanup.callback(engine.dispose)
                upgrade_database(engine)
                projects = managed_projects(root / "projects.json")
                with engine.begin() as connection:
                    key = issue_key(connection, "evaluation-reviewer", create=True)
                    for project in projects.values():
                        change_membership(connection, "evaluation-reviewer", project, "approver")
                auth = {"Authorization": f"Bearer {key}"}

                def push(client, repository, build_targets, delivery):
                    body = json.dumps({
                        "after": build_targets["candidate"]["build"]["commit_sha"],
                        "ref": "refs/heads/main", "deleted": False,
                        "repository": {"id": repository}, "installation": {"id": repository + 10},
                    }).encode()
                    response = client.post("/webhooks/github", content=body, headers={
                        "X-GitHub-Event": "push", "X-GitHub-Delivery": delivery,
                        "X-Hub-Signature-256": "sha256=" + hmac.digest(secret, body, "sha256").hex(),
                    })
                    self.assertEqual(response.status_code, 202)
                    return response.json()["status"]

                def evaluate(client, delivery, definition, build_targets, *, candidate_citation=None):
                    expected = {item["case_id"]: item for item in definition["suite"]["cases"]}
                    seen = {variant: [] for variant in build_targets}
                    by_url = {target["url"]: (variant, target["build"]) for variant, target in build_targets.items()}

                    def respond(request):
                        variant, build = by_url[str(request.url)]
                        payload = json.loads(request.content)
                        item = expected[payload["case_id"]]
                        self.assertEqual(payload, {"case_id": item["case_id"], "question": item["question"]})
                        seen[variant].append(item["case_id"])
                        citation = item["required_citation"]
                        if variant == "candidate" and candidate_citation and item["case_id"] == candidate_citation[0]:
                            citation = candidate_citation[1]
                        return httpx.Response(200, json={"build": build, "citation": citation})

                    with engine.connect() as connection:
                        inputs = connection.scalar(select(deliveries.c.inputs).where(deliveries.c.id == delivery))
                    snapshot = inputs["project_evaluation"]
                    self.assertEqual(snapshot, {
                        **definition, "dataset_digest": digest(definition["suite"]), "policy_digest": digest(definition["policy"]),
                    })
                    with httpx.Client(transport=httpx.MockTransport(respond)) as target_client:
                        with patch("service.evaluate_builds.httpx.Client", return_value=nullcontext(target_client)):
                            self.assertEqual(process_one(engine, delivery), delivery)
                    self.assertEqual(seen, {"baseline": list(expected), "candidate": list(expected)})
                    path = f"/v1/runs/{delivery}"
                    report = client.get(path, headers=auth).raise_for_status().json()["report"]
                    self.assertEqual(report["suite_revision"], definition["suite"]["revision"])
                    self.assertEqual(report["policy_revision"], definition["policy"]["revision"])
                    self.assertEqual(report["dataset_digest"], snapshot["dataset_digest"])
                    self.assertEqual(report["policy_digest"], snapshot["policy_digest"])
                    self.assertEqual(report["candidate"]["minimum_score"], definition["policy"]["minimum_score"])
                    cases = client.get(f"{path}/cases", headers=auth).raise_for_status().json()["items"]
                    self.assertEqual({item["case_id"] for item in cases}, set(expected))
                    self.assertEqual({item["case_id"] for item in cases if item["critical"]},
                                     set(definition["policy"]["critical_cases"]))
                    return report

                def approve(client, delivery, build_targets):
                    return client.post(f"/v1/runs/{delivery}/approval", headers=auth, json={
                        "decision": "approve", "image_digest": build_targets["candidate"]["build"]["image_digest"],
                    })

                with TestClient(create_app()) as client:
                    self.assertEqual(push(client, 71, shipping, "eval-shipping-old"), "queued")
                    self.assertEqual(push(client, 72, beta, "eval-warranty"), "queued")
                write("shipping-eval.json", updated)
                with TestClient(create_app()) as restarted:
                    self.assertEqual(push(restarted, 71, shipping, "eval-shipping-old"), "duplicate")
                    blocked = evaluate(restarted, "eval-shipping-old", original, shipping,
                                       candidate_citation=("shipping-window", "shipping-v1"))
                    self.assertEqual(blocked["candidate"]["score"], 0.5)
                    self.assertEqual(blocked["candidate"]["critical_failures"], ["shipping-window"])
                    self.assertEqual(blocked["verdict"], "block")
                    self.assertEqual(approve(restarted, "eval-shipping-old", shipping).json()["code"], "gate_not_passed")
                    self.assertEqual(evaluate(restarted, "eval-warranty", warranty, beta)["verdict"], "pass")
                write("shipping-targets.json", corrected)
                with TestClient(create_app()) as restarted:
                    self.assertEqual(push(restarted, 71, corrected, "eval-shipping-corrected"), "queued")
                    self.assertEqual(evaluate(restarted, "eval-shipping-corrected", updated, corrected)["verdict"], "pass")
                    self.assertEqual(approve(restarted, "eval-shipping-corrected", corrected).status_code, 200)
                    self.assertEqual(push(restarted, 71, corrected, "eval-shipping-missing"), "queued")
                    missing = evaluate(restarted, "eval-shipping-missing", updated, corrected,
                                       candidate_citation=("shipping-window", None))
                    self.assertEqual(missing["verdict"], "inconclusive")
                    self.assertEqual(missing["candidate"]["score"], missing["candidate"]["minimum_score"])
                    self.assertEqual(missing["candidate"]["missing_evidence"], ["shipping-window"])
                    denied = approve(restarted, "eval-shipping-missing", corrected)
                    self.assertEqual((denied.status_code, denied.json()["code"]), (409, "gate_not_passed"))

    def test_configuration_rejects_invalid_policy_or_evidence(self):
        item = case("account", "account")
        suite = {"revision": "suite-v1", "cases": [item]}
        policy = {"revision": "gate-v1", "minimum_score": 0.8, "critical_cases": ["account"]}
        for invalid in (
            {"suite": {**suite, "cases": [item, item]}, "policy": policy},
            {"suite": suite, "policy": {**policy, "critical_cases": ["other-project-case"]}},
            {"suite": suite, "policy": {**policy, "minimum_score": 1.01}},
            {"suite": {**suite, "cases": [{**item, "required_citation": "missing"}]}, "policy": policy},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                EvaluationDefinition.model_validate(invalid)
